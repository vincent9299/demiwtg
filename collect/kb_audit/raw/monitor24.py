#!/usr/bin/env python3
# 24h 值守：节点 worker 存活重启、分块进度看板、PlantNet 出货、磁盘水位
import subprocess, os, time, glob, json
RAW = "/home/ubuntu/demi/raw"
LOG = f"{RAW}/logs/monitor24.log"
COS = "/lhcos-data/demiwtg-data/datasets/raw"
NODES = {"a": "pipeline-a", "b": "pipeline-b", "c": "pipeline-c", "d": "pipeline-d", "m": "localhost"}
FILES = {  # name: (cosdir, total)
 "DF20-train_val.tar.gz": (f"{COS}/df20", 115741214441),
 "latest-mediainfo.json.bz2": (f"{COS}/wikimedia", 60493362029),
 "commonswiki-latest-image.sql.gz": (f"{COS}/wikimedia", 18452452774),
 "inaturalist-open-data-20260827.tar.gz": (f"{COS}/inat", 35093052336),
 "CID-InChI-Key.gz": (f"{COS}/pubchem", 7366217952),
 "CID-SMILES.gz": (f"{COS}/pubchem", 1486110215),
}
def sh(cmd, host=None):
    if host and host != "localhost":
        cmd = f"ssh -o ConnectTimeout=8 -o BatchMode=yes {host} '{cmd}'"
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
def log(msg):
    with open(LOG, "a") as f: f.write(f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}\n")
def alive(node):
    if node == "m":
        r = sh("pgrep -fc 'worker.sh /home/ubuntu/demi/raw/state/q_m.tsv'")
    else:
        r = sh("pgrep -fc 'worker.sh ~/q_phase1.tsv'", NODES[node])
    try: return int(r.stdout.strip() or 0) > 0
    except: return False
def restart(node):
    if node == "m":
        sh(f"nohup bash {RAW}/worker.sh {RAW}/state/q_m.tsv >> {RAW}/logs/worker_m.log 2>&1 &")
    else:
        sh("nohup bash ~/worker.sh ~/q_phase1.tsv >> ~/q_phase1.log 2>&1 &", NODES[node])
    log(f"RESTART worker@{node}")
def progress():
    out, tot_done = {}, 0
    for name,(d,total) in FILES.items():
        got = 0; parts = 0
        for p in glob.glob(f"{d}/{name}.part-*"):
            if not p.endswith(".tmp"):
                try:
                    sz = os.path.getsize(p)
                    if sz in (1073741824,) or (name, p) and sz <= 1073741824: got += sz; parts += 1
                except: pass
        out[name] = (got, total, parts)
        tot_done += got
    return out, tot_done
round_ = 0
while True:
    round_ += 1
    try:
        for n in NODES:
            if not alive(n): restart(n)
        prog, tot = progress()
        with open(f"{RAW}/logs/DASHBOARD.txt","w") as f:
            f.write(f"==== 多节点下载看板 {time.strftime('%m-%d %H:%M:%S')} round={round_} ====\n")
            for n,(got,totf,parts) in prog.items():
                f.write(f"{n:40s} {got/1e9:8.2f}/{totf/1e9:6.2f}GB {got*100//max(totf,1):3d}% parts={parts}\n")
            f.write(f"{'TOTAL':40s} {tot/1e9:8.2f}GB  预算 5TB 用量含 wiki 库\n")
            # plantnet 出货状态
            try:
                s = open(f"{RAW}/logs/ship_plantnet.log").read().strip().splitlines()[-1] if os.path.exists(f"{RAW}/logs/ship_plantnet.log") else "no-log"
            except: s = "?"
            f.write(f"plantnet_ship: {s}\n")
            r = sh("pgrep -fc ship_plantnet.sh")
            f.write(f"plantnet_ship_proc: {r.stdout.strip()}\n")
            f.write("master_disk_free_GB: " + sh("df --output=avail -BG / | tail -1").stdout.strip() + "\n")
        if round_ % 15 == 0: log(f"round {round_} total {tot/1e9:.1f}GB")
    except Exception as e:
        log(f"ERR {e}")
    time.sleep(120)
