#!/usr/bin/env python3
"""COS 队列 worker：循环认领 batch → 跑 kb_backfill → 标记 done。
用法: queue_worker.py --identity <w号> [--cos-bucket ...]
身份（proxy/UA）沿用 hub 配置；manifest 沿用该身份的 run_kb_w<w>/。
"""
import argparse, json, os, subprocess, sys, time, hashlib, hmac, urllib.parse, urllib.request, gzip

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cos_util

QUEUE = 'lhcos-data/demiwtg-data/queue'

def call(method, key, data=None, timeout=(15, 300)):
    sid, skey = cos_util.creds()
    path = key if key.startswith('/') else '/' + key
    url = f'https://{os.environ.get("COS_HOST", cos_util.HOST)}{urllib.parse.quote(path)}'
    now = int(time.time()); kt = f'{now-60};{now+900}'
    sk = hmac.new(skey.encode(), kt.encode(), hashlib.sha1).hexdigest()
    hs = f'{method.lower()}\n{path}\n\nhost={os.environ.get("COS_HOST", cos_util.HOST)}\n'
    sts = f'sha1\n{kt}\n{hashlib.sha1(hs.encode()).hexdigest()}\n'
    v = hmac.new(sk.encode(), sts.encode(), hashlib.sha1).hexdigest()
    auth = (f'q-sign-algorithm=sha1&q-ak={sid}&q-sign-time={kt}&q-key-time={kt}'
            f'&q-header-list=host&q-url-param-list=&q-signature={v}')
    req = urllib.request.Request(url, data=data, method=method, headers={'authorization': auth})
    try:
        with urllib.request.urlopen(req, timeout=timeout[1]) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200]
    except Exception:
        return 0, b'transport_error'

def list_prefix(prefix):
    """简易 LIST（分页）返回 key 列表。"""
    sid, skey = cos_util.creds()
    host = os.environ.get('COS_HOST', cos_util.HOST)
    keys, marker = [], ''
    while True:
        params = {'prefix': prefix, 'max-keys': '1000'}
        if marker: params['marker'] = marker
        q = '&'.join(f'{urllib.parse.quote(k, safe="")}={urllib.parse.quote(v, safe="")}' for k, v in sorted(params.items()))
        url = f'https://{host}/?{q}'
        now = int(time.time()); kt = f'{now-60};{now+900}'
        sk = hmac.new(skey.encode(), kt.encode(), hashlib.sha1).hexdigest()
        hs = f'get\n/\n{q}\nhost={host}\n'
        sts = f'sha1\n{kt}\n{hashlib.sha1(hs.encode()).hexdigest()}\n'
        v = hmac.new(sk.encode(), sts.encode(), hashlib.sha1).hexdigest()
        auth = (f'q-sign-algorithm=sha1&q-ak={sid}&q-sign-time={kt}&q-key-time={kt}'
                f'&q-header-list=host&q-url-param-list={sorted(k.lower() for k in params)}&q-signature={v}')
        # 瞬时 403/429/5xx 重试（新 IP 首次突发 LIST 会触发 COS 限频，一击即杀过整批 worker）
        import re
        t = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(
                        urllib.request.Request(url, headers={'authorization': auth}),
                        timeout=60) as r:
                    t = r.read().decode()
                break
            except urllib.error.HTTPError as e:
                if e.code in (403, 429, 500, 502, 503) and attempt < 2:
                    time.sleep(20 * (attempt + 1))
                    continue
                raise
        if t is None:
            break
        for block in re.findall(r'<Contents>.*?</Contents>', t, re.S):
            km = re.search(r'<Key>([^<]+)</Key>', block)
            if km: keys.append(km.group(1))
        if '<IsTruncated>true</IsTruncated>' not in t: break
        nm = re.search(r'<NextMarker>([^<]+)</NextMarker>', t)
        marker = nm.group(1) if nm else ''
    return keys

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--identity', required=True, help='worker 身份号（如 42）')
    ap.add_argument('--rps', type=float, default=0.9)
    ap.add_argument('--lanes', type=int, default=3)
    ap.add_argument('--queue', default=QUEUE, help='COS 队列前缀（默认第 1 批主队列）')
    args = ap.parse_args()
    wid = args.identity
    wd = os.path.expanduser('~/wk_backfill')
    manifest = f'{wd}/run_kb_w{wid}/manifest.jsonl'
    os.makedirs(os.path.dirname(manifest), exist_ok=True)

    # 身份的 proxy/UA 由环境注入（部署器写 /tmp/qw_env_<wid>）
    env = {}
    env_file = f'/tmp/qw_env_{wid}'
    if os.path.exists(env_file):
        for line in open(env_file):
            if '=' in line:
                k, v = line.strip().split('=', 1)
                env[k] = v

    while True:
        batches = [k for k in list_prefix(f'{args.queue}/batches/') if k.endswith('.jsonl.gz')]
        claimed = {os.path.basename(c).split('.')[0] for c in list_prefix(f'{args.queue}/claims/')}
        done = {os.path.basename(d).split('.')[0] for d in list_prefix(f'{args.queue}/done/')}
        todo = [b for b in batches if os.path.basename(b).split('.')[0] not in claimed | done]
        if not todo:
            print('[qw] 队列空，退出', flush=True)
            return
        bkey = todo[0]
        bid = os.path.basename(bkey).split('.')[0]
        # 认领（claim-verify）
        st, _ = call('PUT', f'{args.queue}/claims/{bid}.{wid}', data=json.dumps({'w': wid, 'ts': time.time()}).encode())
        if st not in (200, 204):
            continue
        st, body = call('GET', f'{args.queue}/claims/{bid}.{wid}')
        try:
            owner = json.loads(body).get('w')
        except Exception:
            owner = None
        if owner != wid:
            continue  # 竞态输家
        # 下载批文件
        st, blob = call('GET', bkey)
        if st != 200:
            continue
        local_tasks = f'{wd}/queue_task_{bid}.jsonl.gz'
        with open(local_tasks, 'wb') as f:
            f.write(blob)
        print(f'[qw] w{wid} 认领 {bid} ({len(blob)}B)', flush=True)
        # 跑 kb_backfill
        cmd = ['python3', '-u', f'{wd}/kb_backfill.py',
               '--tasks', local_tasks, '--shard', '0/1',
               '--manifest', manifest,
               '--proxy', env.get('KBP_PROXY', ''),
               '--ua', env.get('KBP_UA', ''),
               '--rps-start', str(args.rps), '--rps-max', str(args.rps),
               '--hard-cap-mb', '64', '--lanes', str(args.lanes), '--transport', 'pycurl']
        e = dict(os.environ); e.update(env)
        r = subprocess.run(cmd, env=e, capture_output=True, text=True)
        if os.path.exists(local_tasks):
            os.remove(local_tasks)
        if r.returncode != 0:
            # 失败绝不标 done（否则批被假完成、行静默丢失）；释放认领，退避后重试
            err = (r.stderr or '')[-500:].replace('\n', ' ')
            print(f'[qw] w{wid} 失败 {bid} rc={r.returncode}：{err}', flush=True)
            call('DELETE', f'{args.queue}/claims/{bid}.{wid}')
            time.sleep(60)
            continue
        # 完成标记
        call('PUT', f'{args.queue}/done/{bid}.{wid}',
             data=json.dumps({'w': wid, 'ts': time.time(), 'rc': r.returncode}).encode())
        print(f'[qw] w{wid} 完成 {bid} rc={r.returncode}', flush=True)

if __name__ == '__main__':
    main()
