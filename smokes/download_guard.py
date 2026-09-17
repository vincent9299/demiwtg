"""download() 守门修复最小冒烟(2026-09-17): 429 重试/429 用尽/404 直弃/
200-HTML 兜底/超封顶/真图放行 + 流引擎 _fetch_one 同口径。
金测红线: 错误页字节永远进不了 Sink。运行: python3 -m smokes.download_guard
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx

JPEG = b"\xff\xd8\xff\xe0" + b"\x12\x34" * 64
HTML_ERR = (b"<!DOCTYPE html>\n<html lang=\"en\">\n<title>Wikimedia Error"
            b"</title>\n<p>Error: 429, Your bot is making too many requests")
PASS = 0


def ok(name, cond):
    global PASS
    assert cond, name
    PASS += 1
    print(f"  ok  {name}")


def make_fetcher(handler):
    import flow_images_batch as fb
    fb.RETRY_BACKOFF = (0.01, 0.02, 0.03)          # 冒烟不等真退避
    f = fb.Fetcher(SimpleNamespace(api_rate=100, dl_rate=100, dl_conc=2))
    f.client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 follow_redirects=True)
    return fb, f


async def t_batch():
    print("[batch download]")
    calls = {"n": 0}

    def h_429_then_200(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, content=JPEG)

    fb, f = make_fetcher(h_429_then_200)
    got = await f.download({"url": "https://x/ok.jpg"})
    ok("429→退避→200 放行原图", got == (JPEG, "orig") and calls["n"] == 2)
    await f.aclose()

    def h_always_429(request):
        return httpx.Response(429, text="slow down")

    fb, f = make_fetcher(h_always_429)
    ok("429 持续→重试用尽认缺", await f.download({"url": "https://x/a.jpg"}) is None)
    await f.aclose()

    fb, f = make_fetcher(lambda r: httpx.Response(404, text="gone"))
    ok("404 直弃不重试", await f.download({"url": "https://x/b.jpg"}) is None)
    await f.aclose()

    fb, f = make_fetcher(lambda r: httpx.Response(200, content=HTML_ERR))
    ok("200-HTML 错误页兜底拦截", await f.download({"url": "https://x/c.jpg"}) is None
       and f.miss_html == 1)
    await f.aclose()

    fb, f = make_fetcher(lambda r: httpx.Response(200, content=JPEG))
    ok("200 真图放行", await f.download({"url": "https://x/d.jpg"}) == (JPEG, "orig"))
    await f.aclose()

    fb, f = make_fetcher(lambda r: httpx.Response(200, content=JPEG * 999))
    fb.HARD_CAP_BYTES = 128
    ok("超封顶认缺", await f.download({"url": "https://x/e.jpg"}) is None)
    await f.aclose()


async def t_stream():
    print("[stream _fetch_one]")
    from operators import commons as oc
    from demiflow.collect import net

    net.set_download_client(httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=HTML_ERR))))
    st = oc.CommonsFetchStage()
    got = await st._fetch_one({"qid": "Q1", "commons_file": "a.jpg"},
                              {"url": "https://x/a.jpg"})
    ok("200-HTML 错误页兜底拦截", got is None)

    net.set_download_client(httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=JPEG))))
    got = await st._fetch_one({"qid": "Q1", "commons_file": "a.jpg"},
                              {"url": "https://x/a.jpg", "extmetadata": {}})
    ok("真图放行且 tier=orig", got is not None and got["tier"] == "orig"
       and got["data"] == JPEG)

    net.set_download_client(httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(429, text="slow down"))))
    try:
        await st._fetch_one({"qid": "Q1", "commons_file": "a.jpg"},
                            {"url": "https://x/a.jpg"})
        raised = False
    except Exception:
        raised = True
    ok("429 由 net 层拦下抛错(不产数据行)", raised)
    net.reset_injected_clients()


async def main():
    await t_batch()
    await t_stream()
    print(f"SMOKE_PASS ({PASS} assertions)")


if __name__ == "__main__":
    asyncio.run(main())
