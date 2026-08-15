#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_unified.py — 将现有标签体系数据融合为「统一 schema」的两个交付文件。

设计目标（对应需求）：
  1. 标签体系：以 build/tag_tree.json 的全树（通用分类标签 + IP 分类标签）为结构来源。
  2. 实例级富描述：虚构角色 IP 的富描述（data/虚构角色IP_实例简介.json）并入对应实例；
     其余分支实例无富描述时 source=derived。
  3. 覆盖整个标签体系，不保留虚构角色 IP 个性化存储：所有实例统一挂在所属叶子节点下。

数据来源：
  build/tag_tree.json                  全树结构 + 实例（纯字符串名）
  data/虚构角色IP_实例简介.json        虚构角色 IP 实例级富描述
  data/taxonomy.json（若存在）         已有节点的 KB 字段（合并保留，避免重建丢失）

产物（两个文件，共用 schema/tag_taxonomy.schema.json）：
  data/taxonomy.json          标签树：节点结构 + KB 字段 + instances 名称列表
  data/instances_meta.json    实例级富描述：扁平 instance 列表

用法：
  python3 scripts/build_unified.py            # 仅预览统计
  python3 scripts/build_unified.py --write    # 落盘两个 JSON
"""
import json
import os
import re
import sys
import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(REPO, "build", "tag_tree.json")
FIC = os.path.join(REPO, "data", "虚构角色IP_实例简介.json")
TAXONOMY = os.path.join(REPO, "data", "taxonomy.json")
META = os.path.join(REPO, "data", "instances_meta.json")
SCHEMA = os.path.join(REPO, "schema", "tag_taxonomy.schema.json")

KB_FIELDS = ["definition", "knowledge_intro", "aliases", "representative_cases", "related_tags"]


def norm_cat(c: str) -> str:
    """统一分隔符为 ' / '，并去掉首尾空白。"""
    return " / ".join(p.strip() for p in c.replace("/", " / ").split(" / ") if p.strip())


def load_kb_from_taxonomy():
    """从已有的 data/taxonomy.json 抽取 _path -> KB 字典（重建时保留已富化字段）。缺失则返回 {}。"""
    try:
        doc = json.load(open(TAXONOMY, encoding="utf-8"))
    except Exception:
        return {}
    kb = {}
    st = [doc.get("tree", {})]
    while st:
        n = st.pop()
        p = n.get("path")
        if p and any(n.get(f) for f in KB_FIELDS):
            kb[p] = {f: n[f] for f in KB_FIELDS if n.get(f)}
        for ch in (n.get("children") or []):
            if isinstance(ch, dict):
                st.append(ch)
    print(f"  从现有 taxonomy.json 复用 KB 节点: {len(kb)}")
    return kb


def load_fic():
    """虚构角色 IP 富描述 -> 键 (name, path从'IP 分类标签'起)。"""
    fic = json.load(open(FIC, encoding="utf-8"))
    d = {}
    for x in fic:
        cat = norm_cat(x["category"])
        d[(x["name"], cat)] = x
    print(f"  虚构角色IP 富描述条目: {len(d)}")
    return d


def node_cat_key(path: str) -> str:
    """将节点 path 归一到与虚构角色IP JSON 一致的键：从 'IP 分类标签' 起。"""
    idx = path.find("IP 分类标签")
    return path[idx:] if idx >= 0 else path


INSTANCES = []  # 扁平实例收集器


def build(node, kb, fic, root=False):
    path = node.get("_path", "")
    out = {
        "name": node["name"],
        "path": path,
        "depth": node.get("depth", path.count(" / ")),
    }
    if path in kb:
        out.update(kb[path])
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
        cat_key = node_cat_key(path)
        for nm in insts:
            if not isinstance(nm, str):
                nm = str(nm)
            names.append(nm)
            rec = {"name": nm, "category": path, "source": "derived"}
            f = fic.get((nm, cat_key))
            if f:
                rec["source"] = f.get("source", "templated")
                rec["intro"] = f.get("intro", "")
                rec["definition"] = f.get("definition", "")
                rec["desc"] = f.get("desc", "")
            INSTANCES.append(rec)
        out["instances"] = names

    if children:
        out["children"] = [build(ch, kb, fic) for ch in children if isinstance(ch, dict)]
    return out


def main():
    global INSTANCES
    INSTANCES = []
    print("== 读取源 ==")
    tree = json.load(open(BUILD, encoding="utf-8"))
    print(f"  build/tag_tree.json: 根={tree['name']!r}")
    kb = load_kb_from_taxonomy()
    fic = load_fic()

    print("== 构建统一树 ==")
    unified_tree = build(tree, kb, fic, root=True)

    # 统计
    nodes = insts = enriched = kb_nodes = 0
    branches = set()
    st = [unified_tree]
    while st:
        n = st.pop()
        nodes += 1
        if any(k in n for k in KB_FIELDS):
            kb_nodes += 1
        if n["depth"] == 1:
            branches.add(n["name"])
        for it in (n.get("instances") or []):
            insts += 1
        for ch in (n.get("children") or []):
            st.append(ch)
    enriched = sum(1 for i in INSTANCES if i.get("source") in ("curated", "templated"))

    now = datetime.datetime.now().isoformat(timespec="seconds")
    tax_doc = {
        "schema_version": "1.0.0",
        "meta": {
            "generated_at": now,
            "source": "build/tag_tree.json（结构+实例） + data/虚构角色IP_实例简介.json（实例富描述） + 现有 taxonomy.json（KB 复用）",
            "description": "标签树（taxonomy）：覆盖整棵树（通用分类标签 + IP 分类标签），节点含 KB 字段与实例名称列表；实例富描述见 instances_meta.json。",
        },
        "tree": unified_tree,
    }
    meta_doc = {
        "schema_version": "1.0.0",
        "meta": {
            "generated_at": now,
            "source": "build/tag_tree.json（实例名） + data/虚构角色IP_实例简介.json（富描述）",
            "description": "实例级富描述（扁平列表），与 taxonomy.json 通过 name + category 关联。",
            "stats": {
                "instances": len(INSTANCES),
                "instances_enriched": enriched,
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
        f"富描述实例={enriched}  KB节点={kb_nodes}"
    )


if __name__ == "__main__":
    main()
