#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_unified.py — 由「统一标签体系」的两个交付文件互相印证、重新生成。

全量收敛后（见讨论结论）：
  - data/taxonomy.json   是结构权威源（标签树：节点结构 + KB 字段 + 实例名称列表 + 节点别名）。
  - data/instances_meta.json 是实例权威源（实例级富描述扁平列表，含实例别名）。

本脚本的作用：在 taxonomy.json 作为结构源的前提下，重新产出这两份文件，
保证二者一致且符合 schema。实例的富描述（intro/definition/desc）与实例别名
（aliases）从「现有 instances_meta.json」按 (name, category) 携带回写，避免重建丢失。
节点 KB 字段与节点别名直接来自 taxonomy.json 本身（它即为源）。

已不再依赖：build/tag_tree.json、data/虚构角色IP_实例简介.json、data/ip_instances.json、
data/ip_query_aliases.json（这些为遗留文件，收敛后删除）。

产物（两个文件，共用 schema/tag_taxonomy.schema.json）：
  data/taxonomy.json          标签树
  data/instances_meta.json    实例级富描述

用法：
  python3 scripts/build_unified.py            # 仅预览统计
  python3 scripts/build_unified.py --write    # 落盘两个 JSON
"""
import json
import os
import sys
import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAXONOMY = os.path.join(REPO, "data", "taxonomy.json")
META = os.path.join(REPO, "data", "instances_meta.json")
SCHEMA = os.path.join(REPO, "schema", "tag_taxonomy.schema.json")

NODE_KB = ["definition", "knowledge_intro", "aliases", "representative_cases", "related_tags"]
INST_CARRY = ["intro", "definition", "desc", "aliases", "source"]


def load_carryover():
    """从现有 instances_meta.json 抽取 (name, category) -> 富描述/别名，用于回写。"""
    try:
        doc = json.load(open(META, encoding="utf-8"))
    except Exception:
        return {}
    co = {}
    for it in doc.get("instances", []):
        key = (it.get("name"), it.get("category"))
        co[key] = {k: it[k] for k in INST_CARRY if it.get(k) not in (None, "", [], {})}
    print(f"  从现有 instances_meta.json 复用实例富描述/别名: {len(co)}")
    return co


INSTANCES = []  # 扁平实例收集器


def build(node, carry, root=False):
    path = node.get("path", "")
    out = {
        "name": node["name"],
        "path": path,
        "depth": node.get("depth", path.count(" / ")),
    }
    for f in NODE_KB:
        if node.get(f) not in (None, "", [], {}):
            out[f] = node[f]
    children = node.get("children") or []
    if root:
        out["type"] = "root"
    elif not children:
        out["type"] = "leaf"
    else:
        out["type"] = "category"

    insts = node.get("instances") or []
    if insts:
        names = []
        for nm in insts:
            if not isinstance(nm, str):
                nm = str(nm)
            names.append(nm)
            rec = {"name": nm, "category": path, "source": "derived"}
            c = carry.get((nm, path))
            if c:
                for k in INST_CARRY:
                    if c.get(k) not in (None, "", [], {}):
                        rec[k] = c[k]
            INSTANCES.append(rec)
        out["instances"] = names

    if children:
        out["children"] = [build(ch, carry) for ch in children if isinstance(ch, dict)]
    return out


def main():
    global INSTANCES
    INSTANCES = []
    print("== 读取源 ==")
    doc = json.load(open(TAXONOMY, encoding="utf-8"))
    tree = doc["tree"]
    print(f"  data/taxonomy.json: 根={tree['name']!r}")
    carry = load_carryover()

    print("== 构建统一树 ==")
    unified_tree = build(tree, carry, root=True)

    nodes = insts = enriched = kb_nodes = aliased_inst = 0
    branches = set()
    st = [unified_tree]
    while st:
        n = st.pop()
        nodes += 1
        if any(k in n for k in NODE_KB):
            kb_nodes += 1
        if n["depth"] == 1:
            branches.add(n["name"])
        for it in (n.get("instances") or []):
            insts += 1
        for ch in (n.get("children") or []):
            st.append(ch)
    enriched = sum(1 for i in INSTANCES if i.get("source") in ("curated", "templated"))
    aliased_inst = sum(1 for i in INSTANCES if i.get("aliases"))

    now = datetime.datetime.now().isoformat(timespec="seconds")
    tax_doc = {
        "schema_version": doc.get("schema_version", "1.0.0"),
        "meta": {
            "generated_at": now,
            "source": "data/taxonomy.json（结构+实例名+节点KB/别名，作为权威源）",
            "description": "标签树（taxonomy）：覆盖整棵树（通用分类标签 + IP 分类标签），节点含 KB 字段与实例名称列表；实例富描述见 instances_meta.json。",
        },
        "tree": unified_tree,
    }
    meta_doc = {
        "schema_version": "1.0.0",
        "meta": {
            "generated_at": now,
            "source": "data/taxonomy.json（实例名） + 现有 data/instances_meta.json（富描述/别名携带）",
            "description": "实例级富描述（扁平列表），与 taxonomy.json 通过 name + category 关联。",
            "stats": {
                "instances": len(INSTANCES),
                "instances_enriched": enriched,
                "instances_aliased": aliased_inst,
            },
        },
        "instances": INSTANCES,
    }

    if "--write" in sys.argv:
        with open(TAXONOMY, "w", encoding="utf-8") as f:
            json.dump(tax_doc, f, ensure_ascii=False, indent=1)
        with open(META, "w", encoding="utf-8") as f:
            json.dump(meta_doc, f, ensure_ascii=False, indent=1)
        print(f"已写出: {TAXONOMY} ({os.path.getsize(TAXONOMY)/1024/1024:.2f} MB)")
        print(f"已写出: {META} ({os.path.getsize(META)/1024/1024:.2f} MB)")
    print(
        f"统计: 节点={nodes}  一级分支={len(branches)}  实例={len(INSTANCES)}  "
        f"富描述实例={enriched}  含别名实例={aliased_inst}  KB节点={kb_nodes}"
    )


if __name__ == "__main__":
    main()
