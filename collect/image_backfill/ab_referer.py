import json, subprocess, time, sys
rows=[json.loads(l) for l in open("/home/ubuntu/wk_backfill/wm_19.jsonl")][500:520]
UA="collect-v2/0.1 (research image collection; https://github.com/vincent9299/demiwtg-data) httpx/0.28"
def run(with_ref):
    ok=r429=0
    for r in rows:
        cmd=["curl","-so","/dev/null","-w","%{http_code}","--max-time","30","-A",UA]
        if with_ref: cmd+=["-e","https://commons.wikimedia.org/"]
        cmd.append(r["u"])
        c=subprocess.run(cmd,capture_output=True,text=True).stdout
        if c=="200": ok+=1
        elif c=="429": r429+=1
        time.sleep(1)
    return ok,r429
time.sleep(30)
print("no-referer:", run(False))
time.sleep(30)
print("with-referer:", run(True))
