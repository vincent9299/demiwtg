"""collect_v2/op_search.py 最小冒烟：MockTransport 验证两个代表源的解析与契约。

运行：python3 -m collect_v2.smoke_search
"""

from __future__ import annotations

import asyncio
import json

import httpx

from collect_v2 import infra, op_search


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def main() -> None:
    infra.RETRY_INTERVAL = 0.05

    # 1) wikimedia_zh：乱序 pages 按 index 排回相关度序，字段提取正确
    wm_payload = {
        "query": {
            "pages": {
                "101": {"pageid": 101, "index": 2, "title": "File:乙.jpg",
                        "imageinfo": [{"url": "https://up.example/乙.jpg", "width": 800,
                                       "height": 600, "mime": "image/jpeg",
                                       "extmetadata": {"LicenseShortName": {"value": "CC-BY"}}}],
                        "pageprops": {"canonicalurl": "https://zh.wikipedia.org/wiki/File:乙.jpg"}},
                "100": {"pageid": 100, "index": 1, "title": "File:甲.png",
                        "imageinfo": [{"url": "https://up.example/甲.png", "width": 1024,
                                       "height": 768, "mime": "image/png", "extmetadata": {}}]},
            }
        }
    }

    def wm_handler(req):
        assert "commons.wikimedia.org" in str(req.url)
        assert req.url.params["gsrsearch"] == "慕田峪长城"
        return httpx.Response(200, json=wm_payload)

    seed = op_search.Seed(name="慕田峪长城")
    cands = await op_search.search(seed, "wikimedia_zh", client=make_client(wm_handler))
    assert [c.native["page_title"] for c in cands] == ["File:甲.png", "File:乙.jpg"], cands
    assert cands[0].rank == 0 and cands[0].query == "慕田峪长城"
    assert cands[0].content_url == "https://up.example/甲.png"
    assert cands[1].license == "CC-BY"
    assert cands[1].landing_url == "https://zh.wikipedia.org/wiki/File:乙.jpg"
    print("[PASS] wikimedia_zh 排序与字段提取")

    # 2) baidu：middleURL 优先（不用加密 objURL）、尺寸取 URL 查询串、空壳剔除、去重
    bd_payload = {"data": [
        {"objURL": "ipprf_z2C$q加密串", "middleURL": "https://img0.baidu.com/it/u=1&fm=253?w=640&h=480",
         "width": "3000", "height": "2000", "fromURL": "https://p.example", "di": 12345},
        {"width": "1", "height": "1"},  # 空壳
        {"thumbURL": "https://t.example/b.jpg"},  # 无尺寸查询串
        {"middleURL": "https://img0.baidu.com/it/u=1&fm=253?w=640&h=480"},  # 与首条重复
    ]}

    def bd_handler(req):
        assert "baidu.com" in str(req.url)
        if req.url.path != "/search/acjson":
            return httpx.Response(200, text="<html>home</html>")  # 预热请求
        assert req.url.params["word"] == "菠萝包"
        return httpx.Response(200, json=bd_payload)

    cands = await op_search.search(op_search.Seed(name="菠萝包"), "baidu",
                                   client=make_client(bd_handler))
    assert len(cands) == 2, cands
    assert cands[0].content_url.startswith("https://img0.baidu.com")  # middleURL 优先，不碰 objURL
    assert (cands[0].declared_width, cands[0].declared_height) == (640, 480)  # 尺寸取 URL 查询串而非原图字段
    assert cands[0].native["orig_width"] == 3000                      # 原图尺寸留 native
    assert cands[1].rank == 1 and cands[1].declared_width is None     # 无查询串放行为 None
    assert cands[0].instance == "菠萝包"                              # 种子实例名随行透传
    print("[PASS] baidu middleURL 优先、URL 尺寸提取、空壳剔除与去重")

    # 3) baidu 反爬页（非 JSON）→ TransientExhaustedError
    def bd_anti(req):
        if req.url.path != "/search/acjson":
            return httpx.Response(200, text="<html>home</html>")
        return httpx.Response(200, text="<html>verify</html>")

    try:
        await op_search.search(op_search.Seed(name="x"), "baidu",
                               client=make_client(bd_anti))
        raise AssertionError("非 JSON 应答应抛 TransientExhaustedError")
    except infra.TransientExhaustedError:
        pass
    print("[PASS] baidu 反爬页按瞬态失败上抛")

    # 4) baidu 反爬明确拦截（antiFlag）→ DeterministicError
    def bd_antiflag(req):
        if req.url.path != "/search/acjson":
            return httpx.Response(200, text="<html>home</html>")
        return httpx.Response(200, json={"antiFlag": 1, "message": "Forbid spider access"})

    try:
        await op_search.search(op_search.Seed(name="x"), "baidu",
                               client=make_client(bd_antiflag))
        raise AssertionError("antiFlag 应抛 DeterministicError")
    except infra.DeterministicError:
        pass
    print("[PASS] baidu antiFlag 拦截按确定性失败认缺")

    # 5) K 封顶与认缺：gsrlimit 不超过 k_cap；空结果原样返回
    def wm_empty(req):
        assert int(req.url.params["gsrlimit"]) <= op_search.K_SEMANTIC
        return httpx.Response(200, json={"query": {"pages": {}}})

    cands = await op_search.search(op_search.Seed(name="不存在的东西"), "wikimedia_zh", k=99,
                                   client=make_client(wm_empty))
    assert cands == []
    print("[PASS] K 封顶与空列表认缺")

    # 6) 未注册源报错
    try:
        await op_search.search(op_search.Seed(name="x"), "no_such_source")
        raise AssertionError("未注册源应报错")
    except ValueError:
        pass
    print("[PASS] 未注册源拒绝")

    print("冒烟全部通过")


if __name__ == "__main__":
    asyncio.run(main())
