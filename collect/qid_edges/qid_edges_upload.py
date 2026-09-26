"""qid_edges 产物上传 COS:gz + md5 + put_smart(>256MB 自动分片) + HEAD 核验
对象键:lhcos-data/demiwtg-data/datasets/demiwtg/kb/qid_edges/
"""
import hashlib, os, subprocess, sys

sys.path.insert(0, "/home/ubuntu")
from demiflow_collect.cosio import COSCreds, COSIO, build_host

HOME = "/home/ubuntu"
PREFIX = "lhcos-data/demiwtg-data/datasets/demiwtg/kb/qid_edges"
FILES = [
    ("qid_edges.tsv", "qid_edges.tsv.gz"),
    ("qid_class_labels.tsv", "qid_class_labels.tsv.gz"),
]

io = COSIO(COSCreds.discover(paths=(f"{HOME}/.cos_creds",)),
           build_host("lhcos-368f6-1256345599", "ap-singapore"))


def md5_of(path, chunk=1 << 24):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


for src, gz in FILES:
    sp, zp = f"{HOME}/{src}", f"{HOME}/{gz}"
    if not os.path.exists(zp) or os.path.getmtime(zp) < os.path.getmtime(sp):
        print(f"[gz] {src} -> {gz}", flush=True)
        subprocess.run(["pigz", "-p", "14", "-kf", sp], check=True)
    size = os.path.getsize(zp)
    md5 = md5_of(zp)
    print(f"[md5] {gz}: {size:,}B md5={md5}", flush=True)
    key = f"{PREFIX}/{gz}"
    ok = io.put_smart(key, zp, md5)
    got = io.head(key)
    print(f"[put] {key} ok={ok} head={got} match={got == size}", flush=True)
    if got != size:
        sys.exit(f"上传尺寸不符: {key}")
    sidecar = f"{HOME}/{gz}.md5"
    open(sidecar, "w").write(f"{md5}  {gz}\n")
    io.put_smart(f"{key}.md5", sidecar, md5_of(sidecar))

print("UPLOAD_ALL_OK", flush=True)
