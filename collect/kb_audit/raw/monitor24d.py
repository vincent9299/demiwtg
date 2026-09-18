#!/usr/bin/env python3
# 值守(d-only)：worker 存活重启、分块进度看板、Smithsonian 进度、磁盘水位
import subprocess, os, time, glob
RAW = "/home/ubuntu/demi/raw"
LOG = f"{RAW}/logs/monitor24d.log"
COS = "/lhcos-data/demiwtg-data/datasets/raw"
DHOST = "pipeline-d"
FILES = {
 "DF20-train_val.tar.gz": (f"{COS}/df20", 115741214441),
 "latest-mediainfo.json.bz2": (f"{COS}/wikimedia", 60493362029),
 "inaturalist-open-data-20260827.tar.gz": (f"{COS}/inat", 35093052336),
 "commonswiki-latest-image.sql.gz": (f"{COS}/wikimedia", 18452452774),
 "CID-InChI-Key.gz": (f"{COS}/pubchem", 7366217952),
 "CID-SMILES.gz": (f"{COS}/pubchem", 1486110215),
}
G = 1073741824
def sh(cmd, host=None):
    if host:
        cmd = f"ssh -o ConnectTimeout=8 -o BatchMode=yes {host} '{cmd}'"
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=90)
def log(msg):
    with open(LOG, "a") as f: f.write(f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}\n")
def d_alive():
    r = sh("pgrep -c -f 'q_d1[.]tsv' ; pgrep -c -f 'q_d2[.]tsv'", DHOST)
    c1, c2 = (r.stdout.split() + ["0","0"])[:2]
    return int(c1) > 0, int(c2) > 0
def smith_alive():
    r = sh("pgrep -c -f 'smithsonian_loca[l]'", DHOST)
    try: return int(r.stdout.strip() or 0) > 0
    except: return False
def progress():
    out, tot = {}, 0
    for name, (d, total) in FILES.items():
        got = parts = 0
        for p in glob.glob(f"{d}/{name}.part-*"):
            if p.endswith(".tmp"): continue
            try:
                sz = os.path.getsize(p)
                if 0 < sz <= G: got += sz; parts += 1
            except: pass
        out[name] = (got, total, parts); tot += got
    wit_parts = len(glob.glob(f"{COS}/wit/*.tsv.gz.part-*"))
    wit_done = sum(os.path.getsize(p) for p in glob.glob(f"{COS}/wit/*.tsv.gz.part-*") if not p.endswith(".tmp"))
    smith = len(glob.glob(f"{COS}/smithsonian/metadata_*"))
    return out, tot, wit_parts, wit_done, smith
rnd = 0
while True:
    rnd += 1
    try:
        a1, a2 = d_alive()
        if not a1: sh("nohup bash ~/worker.sh ~/q_d1.tsv >> ~/qd1.log 2>&1 &", DHOST); log("RESTART qd1")
        if not a2: sh("nohup bash ~/worker.sh ~/q_d2.tsv >> ~/qd2.log 2>&1 &", DHOST); log("RESTART qd2")
        if not smith_alive(): sh("nohup python3 ~/smithsonian_local.py >> ~/smithsonian.log 2>&1 &", DHOST); log("RESTART smith")
        prog, tot, wp, wd, smith = progress()
        ddf = sh("df --output=avail -BG / | tail -1", DHOST).stdout.strip()
        with open(f"{RAW}/logs/DASHBOARD.txt", "w") as f:
            f.write(f"==== 下载看板(d-only限速12M) {time.strftime('%m-%d %H:%M:%S')} r={rnd} ====\n")
            for n, (got, t, p) in prog.items():
                f.write(f"{n:42s} {got/1e9:7.2f}/{t/1e9:6.2f}GB {got*100//max(t,1):3d}% parts={p}\n")
            f.write(f"{'WIT TSV(parts)':42s} {wd/1e9:7.2f}GB parts={wp}\n")
            f.write(f"{'Smithsonian shards':42s} {smith}/13606\n")
            f.write(f"{'TOTAL':42s} {tot/1e9:7.2f}GB | node-d free: {ddf}\n")
        if rnd % 20 == 0: log(f"r{rnd} tot {tot/1e9:.1f}GB smith {smith}")
    except Exception as e:
        log(f"ERR {e}")
    time.sleep(120)
