# -*- coding: utf-8 -*-
"""给标签树中「IP 分类标签」下的节点补 KB 字段（definition / knowledge_intro /
aliases / representative_cases / related_tags），写回统一 JSON。

统一版（2026-08-15）：数据源与产物均为 data/taxonomy.json（标签树）。
KB 字段（definition/knowledge_intro/aliases/representative_cases/related_tags）写回
节点。不再读写 tag_tree_explorer.html。主查看器运行时分别 fetch taxonomy.json
与 instances_meta.json。

运行：python3 scripts/gen_full_enrich.py        -> 仅打印待 enriched 节点数与样本
      python3 scripts/gen_full_enrich.py --write -> 把 KB 字段写回 data/taxonomy.json
"""
import re, json, sys, os, datetime
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAXONOMY = os.path.join(REPO, "data", "taxonomy.json")
KB_FIELDS = ["definition", "knowledge_intro", "aliases", "representative_cases", "related_tags"]

doc = json.load(open(TAXONOMY, encoding="utf-8"))
obj = doc["tree"]


def has_kb(n):
    return any(n.get(k) for k in KB_FIELDS)


def find_ip(n):
    for ch in n.get("children", []) or []:
        if "IP" in ch.get("name", ""):
            return ch
    return None


ip_root = find_ip(obj)

nodes = []
def walk(n, parent):
    nodes.append((n, parent))
    for ch in n.get("children", []) or []:
        walk(ch, n)
walk(ip_root, None)


def sample_spread(lst, k=8):
    if not lst:
        return []
    if len(lst) <= k:
        return list(lst)
    step = len(lst) / k
    return [lst[int(idx * step)] for idx in range(k)]


def collect_desc_instances(n, limit=14):
    out = []
    def rec(m):
        for inst in (m.get("instances") or []):
            name = inst.get("name") if isinstance(inst, dict) else inst
            if name not in out:
                out.append(name)
            if len(out) >= limit:
                return
        for ch in (m.get("children") or []):
            rec(ch)
            if len(out) >= limit:
                return
    rec(n)
    return out[:limit]


def path_segs(n):
    return (n.get("path") or "").split(" / ")


S3_VARIANTS = [
    "在 IP 开发、授权衍生与跨媒介运营中，{n}作为一个结构化标签，便于内容的检索、组合与二次创作。",
    "对内容平台与创作者而言，{n}提供了清晰的归类框架，有助于 IP 资产的沉淀与再利用。",
    "在文旅、品牌与数字内容融合的背景下，{n}可支撑从单一作品到系列化 IP 的延展。",
    "作为标签体系中的节点，{n}既服务检索，也承载了该类内容的审美与商业共识。",
]


RELATED_CACHE = {}
by_parent = defaultdict(list)
for n, p in nodes:
    by_parent[id(p)].append(n)
for n, p in nodes:
    rel = []
    for s in by_parent.get(id(p), []):
        if s is n:
            continue
        rel.append(s.get("name", ""))
    rel = rel[:6]
    if p is not None and p.get("name") and p.get("name") not in rel:
        rel.append(p.get("name"))
    RELATED_CACHE[id(n)] = rel[:8]


def build_fields(n):
    segs = path_segs(n)
    name = n.get("name", "")
    idx = segs.index("IP 分类标签") if "IP 分类标签" in segs else 0
    topcat = segs[idx + 1] if idx + 1 < len(segs) else name
    parent_ctx = segs[-2] if len(segs) >= 2 else topcat
    if parent_ctx == name:
        parent_ctx = topcat

    insts = n.get("instances") or []
    if not insts:
        insts = collect_desc_instances(n)
    cases = sample_spread(insts, 8)

    aliases = []
    core = name
    for pat in (" IP", "IP ", "（IP）", "(IP)", " ip", "IP"):
        core = core.replace(pat, "")
    core = core.strip()
    if core and core != name:
        aliases.append(core)

    related = RELATED_CACHE.get(id(n), [])

    if parent_ctx and parent_ctx != topcat and parent_ctx != name:
        definition = (f"{name}是融合世界标签体系中「{topcat}」大类下的细分标签，"
                      f"归类于「{parent_ctx}」体系，用于标识与{name}相关的 IP 内容。")
    else:
        definition = (f"{name}是融合世界标签体系中「{topcat}」大类下的核心标签，"
                      f"用于标识与{name}相关的 IP 内容。")

    s1 = f"{name}属于「{topcat}」范畴"
    if parent_ctx and parent_ctx != topcat and parent_ctx != name:
        s1 += f"，是「{parent_ctx}」下的细分类型"
    s1 += f"，聚焦于{name}相关的 IP 资源进行体系化归类。"
    if cases:
        s2 = f"该标签下收录了{'、'.join(cases)}等具体 IP，覆盖该类别中具有代表性的内容与形象。"
    else:
        s2 = "该标签下聚合了所属类别内的多个具体 IP 实例。"
    s3 = S3_VARIANTS[len(name) % len(S3_VARIANTS)].format(n=name)
    knowledge_intro = s1 + s2 + s3

    return {
        "definition": definition,
        "knowledge_intro": knowledge_intro,
        "aliases": aliases,
        "representative_cases": cases,
        "related_tags": related,
    }


already = [n for n, _ in nodes if has_kb(n)]
todo = [(n, p) for n, p in nodes if not has_kb(n) and n.get("name") != "IP 分类标签"]
print("IP branch total nodes:", len(nodes))
print("already enriched     :", len(already))
print("to be enriched       :", len(todo))

if "--write" in sys.argv:
    for n, p in todo:
        n.update(build_fields(n))
    doc["meta"]["source"] = "data/taxonomy.json（结构+实例名+节点KB 复用） + 本脚本补充分类 KB（IP 分支节点）"
    doc["meta"]["generated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    nodes_c = insts_c = enriched_c = kb_c = 0
    st = [doc["tree"]]
    while st:
        m = st.pop()
        nodes_c += 1
        if any(m.get(k) for k in KB_FIELDS):
            kb_c += 1
        for it in (m.get("instances") or []):
            insts_c += 1
        for ch in (m.get("children") or []):
            st.append(ch)
    doc["meta"]["stats"] = {"nodes": nodes_c, "instances": insts_c, "kb_nodes": kb_c}
    with open(TAXONOMY, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("WROTE:", TAXONOMY)
else:
    for n, p in todo[:3]:
        print("\n==== SAMPLE ====")
        print("name:", n.get("name"))
        print(json.dumps(build_fields(n), ensure_ascii=False, indent=2))
