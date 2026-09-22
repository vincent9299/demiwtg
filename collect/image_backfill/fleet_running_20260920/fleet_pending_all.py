#!/usr/bin/env python3
"""在 fleet 机上：停所有 fleet 进程，汇总所有 run 的 done/dead，
对每个候选文件求未完成行，输出 /tmp/pending_all.jsonl 供调度中枢收取。"""
import glob, json, os, subprocess, time

os.chdir(os.path.expanduser("~/wk_backfill"))
subprocess.run("pkill -f 'fleet_curl.py' ; pkill -f 'fleet_curl_watchdog.sh'", shell=True)
time.sleep(2)

done, dead = set(), set()
for f in glob.glob("run*/meta/done.jsonl"):
    for l in open(f):
        try: done.add(json.loads(l)["sha256"])
        except Exception: pass
for f in glob.glob("run*/meta/dead.jsonl"):
    for l in open(f):
        try: dead.add(json.loads(l)["s"])
        except Exception: pass

cand = (glob.glob("wm_*.jsonl") + glob.glob("tailb_*.jsonl")
        + glob.glob("pending_*.jsonl") + glob.glob("proxy_round2.jsonl"))
seen, out = set(), []
for c in cand:
    if c.endswith("_all.jsonl"): continue
    for l in open(c):
        try: r = json.loads(l)
        except Exception: continue
        s = r.get("s") or r.get("sha256")
        if s in seen or s in done or s in dead: continue
        seen.add(s)
        out.append(json.dumps({"c": r.get("c") or r.get("concepts") or [],
                               "u": r.get("u") or r.get("content_url"),
                               "s": s, "e": r.get("e") or r.get("ext"),
                               "src": r.get("src") or r.get("source")},
                              ensure_ascii=False) + "\n")
with open("/tmp/pending_all.jsonl", "w") as f:
    f.writelines(out)
print("pending", len(out), "done", len(done), "dead", len(dead))
