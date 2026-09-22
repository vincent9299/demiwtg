#!/usr/bin/env python3
"""fleet 下载速率持续监控。

每轮（默认 60s）对 r1-r20 各发一次 ssh，远端只读各 worker manifest 自上次偏移之后的
新增字节，统计成功行（"miss": null）/总行数/429 死信，连同 /proc/net/dev 的 eth0
计数一起返回；本地算每 worker 速率、每机聚合、全网总速，打印表格并追加 CSV。

用法：
  python3 fleet_monitor.py                 # 持续监控，60s 一轮
  python3 fleet_monitor.py --interval 30   # 30s 一轮
  python3 fleet_monitor.py --once          # 只跑一轮
  python3 fleet_monitor.py --once --verbose  # 显示每个 worker 明细
状态 /tmp/fleet_mon_state.json（断点续读，重启不重扫）；CSV 追加写 monitor_dir。
"""
import argparse, concurrent.futures as cf, json, os, shlex, subprocess, sys, time

MON_DIR = "/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/checkpoints/monitor"
HOSTS = [f"r{i}" for i in range(1, 21)]
SSH = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]

# 远端采集代码：零单引号（便于 shell 包裹），偏移从 stdin 传入
REMOTE = '''
import sys,json,os,glob
try: offs=json.loads(sys.stdin.read() or "{}")
except Exception: offs={}
res={"w":{}}
for f in sorted(glob.glob(os.path.expanduser("~/wk_backfill/run_kb_w*/manifest.jsonl"))):
    w=f.split("run_kb_w")[1].split("/")[0]
    try: size=os.path.getsize(f)
    except OSError: continue
    off=offs.get(w,0)
    if size<off: off=0
    succ=r429=rows=0
    if size>off:
        with open(f,"rb") as fh:
            fh.seek(off); data=fh.read(size-off)
        end=data.rfind(b"\\n")
        if end>=0:
            chunk=data[:end+1]; newoff=off+end+1
            rows=chunk.count(b"\\n")
            succ=chunk.count(b"\\"miss\\": null")
            r429=chunk.count(b"\\"miss\\": \\"http:429")
        else:
            newoff=off
    else:
        newoff=off
    res["w"][w]={"off":newoff,"succ":succ,"rows":rows,"r429":r429,"size":size}
rx=tx=0
for line in open("/proc/net/dev"):
    p=line.split()
    if len(p)>9 and p[0].rstrip(":")=="eth0":
        rx,tx=int(p[1]),int(p[9]); break
res["net"]={"rx":rx,"tx":tx}
print(json.dumps(res))
'''


def poll_host(host, offsets):
    try:
        cmd = SSH + [host, "python3 -c " + shlex.quote(REMOTE)]
        r = subprocess.run(cmd, input=json.dumps(offsets),
                           capture_output=True, text=True, timeout=45)
        if r.returncode == 0 and r.stdout.strip():
            return host, json.loads(r.stdout.strip().splitlines()[-1]), None
        return host, None, (r.stderr.strip()[:120] or f"rc={r.returncode}")
    except Exception as e:
        return host, None, str(e)[:120]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    os.makedirs(MON_DIR, exist_ok=True)
    state_path = "/tmp/fleet_mon_state.json"
    state = {"hosts": {}}  # host -> {worker: {off, cum, ts}, net, ts}
    if os.path.exists(state_path):
        try:
            state = json.load(open(state_path))
        except Exception:
            pass
    csv_path = os.path.join(MON_DIR, "fleet_monitor.csv")
    if not os.path.exists(csv_path):
        open(csv_path, "w").write("ts,host,worker,succ_delta,rate_per_s,succ_cum,r429_delta\n")

    zero_streak = {}  # (host,worker) -> 连续零速轮数
    while True:
        t0 = time.time()
        reqs = {h: {w: d["off"] for w, d in state["hosts"].get(h, {}).items()
                    if isinstance(d, dict) and "off" in d}
                for h in HOSTS}
        with cf.ThreadPoolExecutor(max_workers=20) as ex:
            futs = [ex.submit(poll_host, h, reqs[h]) for h in HOSTS]
            results = [f.result() for f in futs]

        fleet_delta = 0
        fleet_secs = 0.0
        rows_out = []
        print(f"\n===== {time.strftime('%H:%M:%S')} =====")
        print(f"{'host':5} {'wk':>3} {'succ/s':>7} {'Mbps↓':>7} {'Mbps↑':>7}  {'429Δ':>5}  note")
        for host, data, err in sorted(results, key=lambda x: int(x[0][1:])):
            if err:
                print(f"{host:5} {'-':>3} {'-':>7} {'-':>7} {'-':>7}  {'-':>5}  UNREACHABLE {err[:60]}")
                continue
            prev = state["hosts"].get(host, {})
            baseline = not prev  # 首轮：只建立基线，不报速率
            now = {}
            host_delta = 0
            dt = max(1e-9, t0 - prev.get("ts", t0)) if not baseline else 0
            host_429 = 0
            for w, d in sorted(data["w"].items(), key=lambda x: int(x[0])):
                pd = prev.get(w) or {}
                # d["succ"] 即本轮增量（远端按偏移只数新增字节）
                cum = pd.get("cum", 0) + d["succ"]
                rate = d["succ"] / dt if (pd and dt > 0) else None
                now[w] = {"off": d["off"], "cum": cum}
                host_delta += d["succ"]
                host_429 += d["r429"]
                key = (host, w)
                if rate is not None:
                    if rate == 0:
                        zero_streak[key] = zero_streak.get(key, 0) + 1
                    else:
                        zero_streak.pop(key, None)
                    if args.verbose or (zero_streak.get(key, 0) >= 2):
                        print(f"  w{w:>4} {host} rate={rate:.2f}/s cum={cum} off={d['off']}"
                              + ("  <<<< STALL" if zero_streak.get(key, 0) >= 2 else ""))
                rows_out.append((host, w, d["succ"], rate, cum, d["r429"]))
            net = data.get("net", {})
            pnet = prev.get("net") or {}
            if baseline:
                rx_mbs = tx_mbs = None
            else:
                rx_mbs = (net.get("rx", 0) - pnet.get("rx", net.get("rx", 0))) / dt / 1e6 * 8
                tx_mbs = (net.get("tx", 0) - pnet.get("tx", net.get("tx", 0))) / dt / 1e6 * 8
            now["net"] = net; now["ts"] = t0
            state["hosts"][host] = now
            if not baseline:
                fleet_delta += host_delta
                fleet_secs = max(fleet_secs, dt)
            print(f"{host:5} {len(data['w']):>3} "
                  f"{'baseline' if baseline else f'{host_delta/dt:.1f}':>7} "
                  f"{('-' if rx_mbs is None else f'{rx_mbs:.0f}'):>7} "
                  f"{('-' if tx_mbs is None else f'{tx_mbs:.0f}'):>7}  {host_429:>5}")
        n_hosts = sum(1 for _, d, e in results if e is None)
        if fleet_secs > 0:
            print(f"FLEET: {fleet_delta:.0f} succ in {fleet_secs:.0f}s  ≈ {fleet_delta/fleet_secs:.1f}/s   hosts {n_hosts}/20")
        else:
            print(f"FLEET: baseline cycle done (rates from next cycle)   hosts {n_hosts}/20")
        with open(csv_path, "a") as f:
            ts = int(t0)
            for host, w, delta, rate, cum, r429 in rows_out:
                f.write(f"{ts},{host},{w},{delta},{'' if rate is None else f'{rate:.3f}'},{cum},{r429}\n")
        tmp = state_path + ".tmp"
        json.dump(state, open(tmp, "w"))
        os.replace(tmp, state_path)
        if args.once:
            break
        time.sleep(max(5, args.interval - (time.time() - t0)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nbye")
