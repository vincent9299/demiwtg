import asyncio, json, time
import httpx
UA="collect-v2/0.1 (research image collection; https://github.com/vincent9299/demiwtg-data) httpx/0.28"
REF="https://vincent9299.github.io/"
rows=[json.loads(l) for l in open("/home/ubuntu/wk_backfill/wm_06.jsonl")][1000:1024]

async def arm(http2, conc):
    limits=httpx.Limits(max_connections=1, max_keepalive_connections=1)
    async with httpx.AsyncClient(http2=http2, limits=limits,
            headers={"User-Agent":UA,"Referer":REF}, timeout=30, follow_redirects=True) as c:
        sem=asyncio.Semaphore(conc); ok=c429=0
        async def one(r):
            nonlocal ok,c429
            async with sem:
                try:
                    resp=await c.get(r["u"])
                    if resp.status_code==200: ok+=1
                    elif resp.status_code==429: c429+=1
                except Exception: pass
        await asyncio.gather(*(one(r) for r in rows))
    proto = "h2" if http2 else "h1"
    return f"{proto} x{conc}conn1: ok={ok} 429={c429}/24"

async def main():
    time.sleep(30)
    print(await arm(False,1))       # 基线：h1.1 串行
    time.sleep(30)
    print(await arm(True,8))        # h2 单连接 8 流
    time.sleep(30)
    print(await arm(True,16))       # h2 单连接 16 流
asyncio.run(main())
