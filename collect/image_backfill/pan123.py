#!/usr/bin/env python3
"""123云盘开放平台上传/下载工具

用法:
  pan123.py login                    # 用 creds.json 里的 clientID/Secret 换取并缓存 token
  pan123.py ls [parentFileId]        # 列出目录(默认根目录0), 自动翻页, 过滤回收站
  pan123.py search <关键字>          # 全局搜索
  pan123.py dl <fileId> -n 文件名 [-o 目录]   # 单文件下载(aria2c 16线程)
  pan123.py dl-dir <folderId> [-o 目录]  # 递归下载整个文件夹
  pan123.py mkdir <parentId> <名字>  # 新建目录, 打印新 dirID
  pan123.py dirid <parentId> <名字>  # 按名查子目录ID, 不存在则新建(幂等)
  pan123.py upload <parentId> <本地路径> [-n 名字]  # 上传(md5秒传+分片PUT+complete)
  pan123.py trash <fileId>...        # 移入回收站

上传要点(端点字段名不一致, 勿"顺手统一"):
  create 用 parentFileID; mkdir 用 parentID 且返回 dirID; trash 用 fileIDs 数组。
配置: /yzp/zhaozy/yangzepeng/0905/pan123/creds.json  {"clientID": "...", "clientSecret": "..."}
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

BASE = "https://open-api.123pan.com"
CREDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "creds.json")
TOKEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.json")
API_PROXY = None  # 直连, 不走环境变量代理(国内站点直连更快)


class Pan123Error(Exception):
    pass


def http(method, url, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read().decode())
    if resp.get("code") != 0:
        raise Pan123Error(f"API错误 code={resp.get('code')} message={resp.get('message')}")
    return resp["data"]


def auth_header():
    if not os.path.exists(TOKEN):
        raise Pan123Error("token 不存在, 先执行: pan123.py login")
    with open(TOKEN) as f:
        tok = json.load(f)
    if tok.get("expiredAt", "") < __import__("datetime").datetime.now().astimezone().isoformat():
        raise Pan123Error("token 已过期, 重新执行: pan123.py login")
    return {"Authorization": f"Bearer {tok['accessToken']}", "Platform": "open_platform"}


def cmd_login():
    with open(CREDS) as f:
        c = json.load(f)
    data = http("POST", f"{BASE}/api/v1/access_token",
                headers={"Platform": "open_platform"},
                body={"clientID": c["clientID"], "clientSecret": c["clientSecret"]})
    with open(TOKEN, "w") as f:
        json.dump(data, f)
    os.chmod(TOKEN, 0o600)
    print(f"token 已缓存, 过期时间: {data['expiredAt']}")


def list_dir(parent_id=0, search=None):
    out, last = [], None
    while True:
        url = f"{BASE}/api/v2/file/list?parentFileId={parent_id}&limit=100"
        if last is not None:
            url += f"&lastFileId={last}"
        if search:
            url += f"&searchData={urllib.request.quote(search)}"
        data = http("GET", url, headers=auth_header())
        out += [x for x in data.get("fileList", []) if not x.get("trashed")]
        last = data.get("lastFileId", -1)
        if last == -1 or not data.get("fileList"):
            break
    return out


def fmt_size(n):
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}PB"


def show(items):
    if not items:
        print("(空)")
    for x in items:
        t = "DIR " if x["type"] == 1 else "FILE"
        print(f"{t}  {x['fileId']:<12} {fmt_size(x.get('size', 0)):>10}  {x['filename']}")


def get_url(file_id):
    data = http("GET", f"{BASE}/api/v1/file/download_info?fileId={file_id}", headers=auth_header())
    return data["downloadUrl"]


def aria2_get(url, out_dir, filename):
    if not shutil.which("aria2c"):
        raise Pan123Error("缺少 aria2c")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, filename)
    if os.path.exists(path) and not os.path.exists(path + ".aria2"):
        print(f"跳过已存在: {filename}")
        return
    cmd = ["aria2c", "-c", "-x", "16", "-s", "16", "-k", "4M", "--file-allocation=none",
           "--console-log-level=warn", "--summary-interval=0",
           "-d", out_dir, "-o", filename, url]
    rc = subprocess.call(cmd)
    if rc != 0:
        raise Pan123Error(f"aria2c 退出码 {rc}")


def dl_file_named(file_id, filename, out_dir="."):
    aria2_get(get_url(file_id), out_dir, filename)


def dl_dir(folder_id, out_dir="."):
    for x in list_dir(folder_id):
        if x["type"] == 1:
            dl_dir(x["fileId"], os.path.join(out_dir, x["filename"]))
        else:
            print(f"下载 {x['filename']} ({fmt_size(x['size'])})")
            dl_file_named(x["fileId"], x["filename"], out_dir)


def mkdir(parent_id, name):
    data = http("POST", f"{BASE}/upload/v1/file/mkdir", headers=auth_header(),
                body={"parentID": parent_id, "name": name})
    return data["dirID"]


def dir_id(parent_id, name):
    """按名找子目录ID, 不存在则新建(幂等, 供守护进程逐级造目录)"""
    for x in list_dir(parent_id):
        if x["type"] == 1 and x["filename"] == name:
            return x["fileId"]
    return mkdir(parent_id, name)


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def get_upload_url(preupload_id, slice_no):
    return http("POST", f"{BASE}/upload/v1/file/get_upload_url", headers=auth_header(),
                body={"preuploadID": preupload_id, "sliceNo": slice_no})


def upload_complete(preupload_id):
    return http("POST", f"{BASE}/upload/v1/file/upload_complete", headers=auth_header(),
                body={"preuploadID": preupload_id})


def upload_async_result(preupload_id):
    return http("POST", f"{BASE}/upload/v1/file/upload_async_result", headers=auth_header(),
                body={"preuploadID": preupload_id})


def put_slice(url, chunk):
    req = urllib.request.Request(url, data=chunk, method="PUT")
    req.add_header("Content-Type", "application/octet-stream")
    with urllib.request.urlopen(req, timeout=600) as r:
        r.read()


def upload_file(path, parent_file_id, name=None):
    """上传: create{etag=md5} → 秒传或分片PUT → complete/异步轮询。
    返回 dict(fileID, reuse)。同内容秒传幂等, 崩溃重传安全(整文件级重试)。"""
    name = name or os.path.basename(path)
    size = os.path.getsize(path)
    etag = md5_file(path)
    data = http("POST", f"{BASE}/upload/v1/file/create", headers=auth_header(),
                body={"parentFileID": parent_file_id, "filename": name,
                      "etag": etag, "size": size})
    if data.get("reuse"):
        return {"fileID": data.get("fileID"), "reuse": True}
    pre, slice_size = data["preuploadID"], int(data["sliceSize"])
    slices = (size + slice_size - 1) // slice_size
    with open(path, "rb") as f:
        for no in range(1, slices + 1):
            chunk = f.read(slice_size)
            for attempt in range(3):  # presigned URL 可能过期/抖动, 重取重传
                try:
                    u = get_upload_url(pre, no)["presignedURL"]
                    put_slice(u, chunk)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(2 * (attempt + 1))
    r = upload_complete(pre)
    for _ in range(60):  # 大文件异步合并, 轮询到 completed
        if r.get("completed"):
            break
        time.sleep(2)
        r = upload_async_result(pre)
    if not r.get("completed"):
        raise Pan123Error(f"上传合并未完成 preuploadID={pre}")
    return {"fileID": r.get("fileID"), "reuse": False}


def trash(file_ids):
    ids = file_ids if isinstance(file_ids, list) else [file_ids]
    return http("POST", f"{BASE}/api/v1/file/trash", headers=auth_header(),
                body={"fileIDs": ids})


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
    elif a[0] == "login":
        cmd_login()
    elif a[0] == "ls":
        show(list_dir(int(a[1]) if len(a) > 1 else 0))
    elif a[0] == "search":
        show(list_dir(0, search=a[1]))
    elif a[0] == "dl":
        fid = int(a[1])
        if "-n" not in a:
            raise Pan123Error("请用 -n 指定文件名(与 ls 显示的一致), 例: dl 14749954 -n data.zip")
        name = a[a.index("-n") + 1]
        out = a[a.index("-o") + 1] if "-o" in a else "."
        dl_file_named(fid, name, out)
    elif a[0] == "dl-dir":
        out = a[a.index("-o") + 1] if "-o" in a else "."
        dl_dir(int(a[1]), out)
    elif a[0] == "mkdir":
        print(mkdir(int(a[1]), a[2]))
    elif a[0] == "dirid":
        print(dir_id(int(a[1]), a[2]))
    elif a[0] == "upload":
        name = a[a.index("-n") + 1] if "-n" in a else None
        r = upload_file(a[2], int(a[1]), name)
        print(("秒传命中" if r["reuse"] else "上传完成") + f" fileID={r['fileID']}")
    elif a[0] == "trash":
        trash([int(x) for x in a[1:]])
        print(f"已移入回收站 {len(a) - 1} 项")
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        main()
    except Pan123Error as e:
        sys.exit(str(e))
