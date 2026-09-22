import subprocess, sys
PROXIES = [
 "http://AsXabmHjKLnV:zVPTbjxOB2@23.142.108.123:443",
 "http://AsXabmHjKLnV:zVPTbjxOB2@149.119.185.219:443",
 "http://AsXabmHjKLnV:zVPTbjxOB2@66.17.67.82:443",
 "http://AsXabmHjKLnV:zVPTbjxOB2@50.3.64.106:443",
 "http://AsXabmHjKLnV:zVPTbjxOB2@66.17.67.97:443",
]
for p in PROXIES:
    ip = p.rsplit("@",1)[1].rsplit(":",1)[0]
    e = subprocess.run(["curl","-sx",p,"--max-time","15","https://api.ipify.org"],capture_output=True,text=True).stdout.strip()
    w = subprocess.run(["curl","-sx",p,"-o","/dev/null","-w","%{http_code} %{time_total}s","--max-time","25",
        "-A","collect-v2/1.0 (research image collection; https://github.com/vincent9299/demiwtg-data)",
        "-e","https://vincent9299.github.io/",
        "https://upload.wikimedia.org/wikipedia/commons/5/5e/Guppy_Poecilia_reticulata.jpg"],capture_output=True,text=True).stdout.strip()
    print(f"{ip} egress={e or 'FAIL'} wikimedia={w}", flush=True)
