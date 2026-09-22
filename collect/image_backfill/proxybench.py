import json, subprocess, time
PROXIES = [
 "http://AsXabmHjKLnV:zVPTbjxOB2@23.142.108.123:443",
 "http://AsXabmHjKLnV:zVPTbjxOB2@149.119.185.219:443",
 "http://AsXabmHjKLnV:zVPTbjxOB2@66.17.67.82:443",
 "http://AsXabmHjKLnV:zVPTbjxOB2@50.3.64.106:443",
 "http://AsXabmHjKLnV:zVPTbjxOB2@66.17.67.97:443",
]
UA="collect-v2/1.0 (research image collection; https://github.com/vincent9299/demiwtg-data)"
rows=[json.loads(l) for l in open("/home/ubuntu/wk_backfill/wm_06.jsonl")][2000:2020]
for p in PROXIES:
    ip=p.rsplit("@",1)[1].rsplit(":",1)[0]
    ok=c429=0; t0=time.time()
    for r in rows:
        c=subprocess.run(["curl","-sx",p,"-o","/dev/null","-w","%{http_code}","--max-time","45","-A",UA,
                          "-e","https://vincent9299.github.io/",r["u"]],capture_output=True,text=True).stdout
        if c=="200": ok+=1
        elif c=="429": c429+=1
        time.sleep(0.5)
    print(f"{ip}: {ok}/20 ok 429={c429} {time.time()-t0:.0f}s", flush=True)
