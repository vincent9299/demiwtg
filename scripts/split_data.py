# -*- coding: utf-8 -*-
"""把统一 JSON（data/taxonomy_unified.json）拆成两个对外交付文件：

  data/taxonomy.json        -- 标签树：节点结构 + KB 字段 + instances 名称列表（结构指针）
  data/instances_meta.json  -- 实例级富描述：扁平 instance 列表，符合 schema 的 instance 定义

主查看器 tag_tree_explorer.html 运行时分别 fetch 这两个文件（懒加载）。
两者共用 schema/tag_taxonomy.schema.json：node 定义树，instance 定义实例元。

用法：
  python3 scripts/split_data.py          # 预览
  python3 scripts/split_data.py --write # 落盘两个文件
"""
import json, os, sys, datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIFIED = os.path.join(REPO, "data", "taxonomy_unified.json")
TAXONOMY = os.path.join(REPO, "data", "taxonomy.json")
META = os.path.join(REPO, "data", "instances_meta.json")


def split(tree):
    """原地改造 tree：节点 instances 改为名称列表；收集全部实例富描述返回。"""
    instances = []

    def walk(n):
        insts = n.get("instances") or []
        names = []
        for it in insts:
            if isinstance(it, dict):
                names.append(it.get("name"))
                rec = {k: it[k] for k in ("name", "category", "source", "intro", "definition", "desc") if it.get(k) is not None}
                if rec.get("name"):
                    instances.append(rec)
            elif isinstance(it, str):
                names.append(it)
        if names:
            n["instances"] = names
        else:
            n.pop("instances", None)
        for ch in (n.get("children") or []):
            if isinstance(ch, dict):
                walk(ch)

    walk(tree)
    return instances


def main():
    doc = json.load(open(UNIFIED, encoding="utf-8"))
    tree = doc["tree"]
    instances = split(tree)

    enriched = sum(1 for i in instances if i.get("source") in ("curated", "templated"))
    now = datetime.datetime.now().isoformat(timespec="seconds")

    tax_doc = {
        "schema_version": doc.get("schema_version", "1.0.0"),
        "meta": {
            "generated_at": now,
            "source": "split from data/taxonomy_unified.json",
            "description": "标签树（taxonomy）：节点结构 + KB 字段 + 实例名称列表；实例富描述见 instances_meta.json。",
        },
        "tree": tree,
    }
    meta_doc = {
        "schema_version": doc.get("schema_version", "1.0.0"),
        "meta": {
            "generated_at": now,
            "source": "split from data/taxonomy_unified.json（实例富描述）",
            "description": "实例级富描述（扁平列表），与 taxonomy.json 通过 name + category 关联。",
            "stats": {"instances": len(instances), "instances_enriched": enriched},
        },
        "instances": instances,
    }

    print(f"实例总数: {len(instances)}  富描述(curated/templated): {enriched}")
    if "--write" in sys.argv:
        with open(TAXONOMY, "w", encoding="utf-8") as f:
            json.dump(tax_doc, f, ensure_ascii=False, indent=1)
        with open(META, "w", encoding="utf-8") as f:
            json.dump(meta_doc, f, ensure_ascii=False, indent=1)
        print(f"已写出: {TAXONOMY} ({os.path.getsize(TAXONOMY)/1024/1024:.2f} MB)")
        print(f"已写出: {META} ({os.path.getsize(META)/1024/1024:.2f} MB)")
    else:
        print("（未加 --write，仅预览）")


if __name__ == "__main__":
    main()
