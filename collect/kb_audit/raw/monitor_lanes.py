#!/usr/bin/env python3
# 值守 v3：多节点专线 worker 存活重启 + 进度看板（d:E线+smith, a:mediainfo×2, b:DF20+img偶, c:img奇）
import subprocess, os, time, glob
RAW = "/home/ubuntu/demi/raw"
LOG = f"{RAW}/logs/monitor_lanes.log"
COS = "/lhcos-data/demiwtg-data/datasets/raw"
G = 1073741824
LANES = {
 "pipeline-d": [],
 "pipeline-a": ["q_a1", "q_a2"],
 "pipeline-b": ["q_b1", "q_b2"],
 "pipeline-c": ["q_c1", "q_c2"],
}
FILES = {
 "DF20-train_val.tar.gz": (f"{COS}/df20", 115741214441),
 "latest-mediainfo.json.bz2": (f"{COS}/wikimedia", 60493362029),
 "commonswiki-latest-image.sql.gz": (f"{COS}/wikimedia", 18452452774),
 "inaturalist-open-data-20260827.tar.gz": (f"{COS}/inat", 35093052336),
 "CID-InChI-Key.gz": (f"{COS}/pubchem", 7366217952),
 "CID-SMILES.gz": (f"{COS}/pubchem", 1486110215),
}
def sh(cmd, host=None):
    if host: cmd = f"ssh -o ConnectTimeout=10 -o BatchMode=yes {host} '{cmd}'"
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
def log(m):
    with open(LOG, "a") as f: f.write(f"[{time.strftime('%m-%d %H:%M:%S')}] {m}\n")
rnd = 0
while True:
    rnd += 1
    try:
        for host, lanes in LANES.items():
            for lane in lanes:
                r = sh(f"pgrep -c -f '{lane}[.]tsv'", host)
                if not (r.stdout.strip().isdigit() and int(r.stdout.strip()) > 0):
                    sh(f"nohup bash ~/worker_v4.sh ~/{lane}.tsv >> ~/{lane}.log 2>&1 &", host)
                    log(f"RESTART {host}:{lane}")
        r = sh("pgrep -c -f 'smith_batc[h]'", "pipeline-d")
        if not (r.stdout.strip().isdigit() and int(r.stdout.strip()) > 0):
            sh("nohup python3 ~/smith_batch.py >> ~/smith.log 2>&1 &", "pipeline-d")
            log("RESTART smith")
        lines = [f"==== 看板(a:medi×2 | b:DF20+img偶 | c:img奇+iNat) {time.strftime('%m-%d %H:%M:%S')} r={rnd} ===="]
        tot = 0
        for name, (d, total) in FILES.items():
            got = parts = 0
            for p in glob.glob(f"{d}/{name}.part-*"):
                if p.endswith(".tmp"): continue
                try:
                    sz = os.path.getsize(p)
                    if 0 < sz <= G: got += sz; parts += 1
                except: pass
            tot += got
            lines.append(f"{name:42s} {got/1e9:7.2f}/{total/1e9:6.2f}GB {got*100//max(total,1):3d}% parts={parts}")
        bt = len(glob.glob(f"{COS}/smithsonian/smithsonian_batches/batch_*.tar.gz"))
        lines.append(f"{'Smithsonian batches':42s} {bt}/46")
        lines.append(f"{'TOTAL':42s} {tot/1e9:7.2f}GB")
        open(f"{RAW}/logs/DASHBOARD.txt", "w").write("\n".join(lines) + "\n")
        if rnd % 20 == 0: log(f"r{rnd} tot {tot/1e9:.1f}GB smith {bt}/46")
    except Exception as e:
        log(f"ERR {e}")
    time.sleep(120)
