#!/usr/bin/env python3
"""第 1 批 81-worker 顶配部署器（python 版，替代 bash 启动器的 stdin/引号坑）。
61 代理 worker（w0-60）+ 20 直连 worker（w61-80）；清单已存在则跳过拉取；
逐 worker 错峰 3s；幂等（远端 pgrep 同 manifest 已在跑则跳过）。"""
import os
import subprocess
import sys
import time

STG = "/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill"
HUB = "/yzp/zhaozy/yangzepeng/0905/demiwtg/state/curation/image_backfill_full_v1/hub"
COS_PREFIX = "lhcos-data/demiwtg-data/datasets/demiwtg/kb/blobs"


def run(cmd, timeout=60, inp=None):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          timeout=timeout, input=inp)


def main() -> None:
    key = sys.argv[1]                       # COS 清单 key
    rps = sys.argv[2] if len(sys.argv) > 2 else "0.25"
    tag = os.path.basename(key)
    for suf in (".jsonl.gz", ".jsonl"):
        if tag.endswith(suf):
            tag = tag[: -len(suf)]
            break
    nw = 81
    ua_assign = {}
    for l in open(f"{HUB}/ua_assign.tsv", encoding="utf-8"):
        k, _, v = l.rstrip("\n").partition("\t")
        if k and v:
            ua_assign[k] = v
    proxies = [l.strip() for l in open(f"{HUB}/proxies/master_final.txt") if l.strip()]

    # 1) 依赖 + 清单（每机一次，存在即跳过）
    for i in range(1, 21):
        h = f"r{i}"
        run(f"timeout 120 scp -q -o ConnectTimeout=15 {STG}/kb_backfill.py {STG}/fleet_curl.py "
            f"{STG}/cos_util.py {STG}/.cos_creds {HUB}/ua_pool.txt {HUB}/ua_assign.tsv {h}:~/wk_backfill/")
        run(f'''timeout 30 ssh -o ConnectTimeout=15 {h} "chmod 600 ~/wk_backfill/.cos_creds; '''
            f'''printf 'UA=%s\\n' \\"\\$(awk -F'\\t' -v k=host:{h} '\\$1==k{{print \\$2}}' ~/wk_backfill/ua_assign.tsv)\\" > ~/wk_backfill/ua.env"''')
        r = run(f'''timeout 30 ssh -o ConnectTimeout=15 {h} "test -s ~/wk_backfill/tasks_{tag}.jsonl.gz && echo exist"''')
        if "exist" in r.stdout:
            print(f"{h} tasks exist", flush=True)
        else:
            pull = (f"timeout 900 ssh -o ConnectTimeout=15 {h} \"cd ~/wk_backfill && python3 -c \\\""
                    f"import cos_util, urllib.request\n"
                    f"k='{key}'\n"
                    f"sid,sk=cos_util.creds()\n"
                    f"a=cos_util._sig('GET','/'+k,{{}},sid,sk)\n"
                    f"r=urllib.request.Request('https://'+cos_util.HOST+'/'+k,headers={{'authorization':a}})\n"
                    f"open('tasks_{tag}.jsonl.gz','wb').write(urllib.request.urlopen(r,timeout=800).read())\n"
                    f"print('pulled')\\\"\"")
            rr = run(pull, timeout=920)
            print(f"{h} tasks: {rr.stdout.strip() or rr.stderr[-60:]}", flush=True)
        time.sleep(1)

    # 2) 81 worker 错峰启动
    for w in range(nw):
        ip = user = pwd = ua = ""
        if w < 61:
            spec = proxies[w]
            ip, port, user, pwd = spec.split(":")
            h = f"r{w % 20 + 1}"
            proxy_opt = f" --proxy 'http://{user}:{pwd}@{ip}:{port}'"
            ua = ua_assign.get(f"proxy:{ip}", "")
            ua_opt = f" --ua '{ua}'" if ua else ""
            kind = f"px-{ip}"
        else:
            h = f"r{w - 61 + 1}"
            proxy_opt = ""
            ua_opt = ""                       # 直连走 ua.env（每机唯一）
            kind = "direct"
        def sh(h, script, timeout=40):
            return subprocess.run(["timeout", str(timeout), "ssh", "-o", "ConnectTimeout=15",
                                   h, script], capture_output=True, text=True)
        guard = sh(h, f"pgrep -f 'run_kb_{tag}_w{w}[/]manifest' >/dev/null && echo RUN", 25)
        if "RUN" in guard.stdout:
            print(f"w{w}@{h} {kind} already", flush=True)
            continue
        # systemd-run 发射：进 systemd 自管 scope，免疫会话/cgroup 清杀（死亡根因）
        script = (f"sudo -n systemd-run --unit=kbw{w} --uid=1000 --gid=1000 "
                  f"bash -c 'cd ~/wk_backfill && exec python3 -u kb_backfill.py "
                  f"--tasks tasks_{tag}.jsonl.gz --shard {w}/{nw} "
                  f"--manifest run_kb_{tag}_w{w}/manifest.jsonl"
                  + (f" --proxy http://{user}:{pwd}@{ip}:{port}" if w < 61 else "")
                  + (f' --ua "{ua}"' if w < 61 and ua else "")
                  + f" --rps-start {rps} --hard-cap-mb 64"
                  f" > kb_{tag}_w{w}.log 2>&1'")
        r = sh(h, script)
        print(f"w{w}@{h} {kind}: {(r.stdout or '').strip() or (r.stderr or '')[-60:]}", flush=True)
        time.sleep(3)
    print("DEPLOY-ALL-DONE", flush=True)


if __name__ == "__main__":
    main()
