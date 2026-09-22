import json, sys, os
n = sys.argv[1]
os.chdir(os.path.expanduser("~/wk_backfill"))
for f in [f"run_{n}/meta/done.jsonl", f"run_{n}/meta/dead.jsonl"]:
    out=[]; bad=0
    for l in open(f):
        try: json.loads(l); out.append(l)
        except Exception: bad+=1
    open(f,"w").writelines(out)
    print(f, "bad", bad)
