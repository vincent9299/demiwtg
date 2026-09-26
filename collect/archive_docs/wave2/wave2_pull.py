import sys, os, time
sys.path.insert(0, "/home/ubuntu/demiflow_collect")
from cosio import COSCreds, COSIO, build_host
io = COSIO(COSCreds.discover(paths=("/home/ubuntu/.cos_creds",)),
           build_host("lhcos-368f6-1256345599", "ap-singapore"))
SG = "lhcos-data/demiwtg-data/datasets/demiwtg/kb/"
OLD = "lhcos-data/demiwtg-data/kb/"
IN = "/home/ubuntu/merge_wave2/in"
MACHINES = ["VM-12-10","VM-12-11","VM-12-15","VM-12-2","VM-12-4","VM-12-5","VM-12-7",
            "VM-4-11","VM-4-12","VM-4-13","VM-4-15","VM-4-17","VM-4-2","VM-4-3","VM-4-6",
            "VM-4-7","VM-4-8","VM-8-11","VM-8-2","VM-8-4"]
jobs = [
    (SG + "images.v2.jsonl.gz", IN + "/images.v2.jsonl.gz", 1735152758),
    (SG + "quarantine/deadletter.v2.jsonl.gz", IN + "/deadletter.v2.jsonl.gz", 113846095),
    (SG + "quarantine/qid_images_v2_pid.jsonl.gz", IN + "/qid_images_v2_pid.jsonl.gz", 974888989),
    (SG + "quarantine/rebuild_report.json", IN + "/rebuild_report.json", 138),
]
for m in MACHINES:
    jobs.append((OLD + "fleet_manifests/final_20260924/si2/" + m + "-ubuntu.tgz",
                 IN + "/si2/" + m + "-ubuntu.tgz", None))
    jobs.append((OLD + "fleet_manifests/final_20260924/th1200/" + m + "-ubuntu.tgz",
                 IN + "/th1200/" + m + "-ubuntu.tgz", None))
jobs.append((OLD + "fleet_manifests/final_20260924/wm404/lake_fix_13.jsonl", IN + "/wm404/lake_fix_13.jsonl", 2502))
jobs.append((OLD + "fleet_manifests/final_20260924/wm404/lake_probe_alive.jsonl", IN + "/wm404/lake_probe_alive.jsonl", 1631))
for m in MACHINES:
    jobs.append((OLD + "manifest_snapshots_0923/" + m + "-ubuntu.tgz",
                 IN + "/snap0923/" + m + "-ubuntu.tgz", None))
t0 = time.time()
for i, (k, dst, expect) in enumerate(jobs):
    if os.path.exists(dst) and os.path.getsize(dst) > 0 and (expect is None or os.path.getsize(dst) == expect):
        print("cached", os.path.basename(dst), flush=True)
        continue
    ok = io.download_to(k, dst)
    sz = os.path.getsize(dst) if os.path.exists(dst) else -1
    print("[%d/%d] %s sz=%d ok=%s t=%.0fs" % (i + 1, len(jobs), os.path.basename(dst), sz, ok, time.time() - t0), flush=True)
print("PULL_DONE", flush=True)
