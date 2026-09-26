"""从完整 qid_cut_map 重建类目表(修 writer bug 的产物,无需重跑)"""
import collections, sys
HOME = "/home/ubuntu"
PNAME = {"P106": "职业", "P171": "物种树", "P140": "宗派",
         "P131": "属地", "P136": "体裁"}
for mx in (20000, 10000, 5000, 1000):
    cnt = collections.Counter()
    with open(f"{HOME}/qid_cut_map_v4_{mx}.tsv") as f:
        next(f)
        for l in f:
            cnt[l.rstrip("\n").rsplit("\t", 1)[1]] += 1
    old_nm = {}
    try:
        with open(f"{HOME}/cut_categories_v4_{mx}.tsv") as f:
            next(f)
            for l in f:
                p = l.rstrip("\n").split("\t")
                old_nm[p[0]] = p[1]
    except FileNotFoundError:
        pass
    def fb(cid):
        if cid == "UNMAPPED": return "未归类长尾"
        parts = cid.split("|")
        base = parts[0].split("#")[0]
        b = old_nm.get(cid)
        if b: return b
        # 从同类前缀旧表找名字线索失败则给结构名
        if len(parts) == 2 and parts[1].startswith("shard"):
            return f"{base}(分段{int(parts[1][5:])+1})"
        if len(parts) == 3 and parts[2] == "noaux":
            return f"{base}·无{PNAME.get(parts[1],parts[1])}值"
        if len(parts) >= 3 and parts[2].startswith("@"):
            return f"{base}·{PNAME.get(parts[1],parts[1])}其他"
        if cid.endswith("#self"): return f"{base}(本级直挂)"
        if cid.endswith("#other"): return f"{base}·其他"
        return base
    tot = sum(cnt.values())
    with open(f"{HOME}/cut_categories_v4_{mx}.tsv", "w") as f:
        f.write("cat_id\tlabel\tentities\n")
        for cid, m in cnt.most_common():
            f.write(f"{cid}\t{fb(cid)}\t{m}\n")
    print(f"MAX={mx}: cats={len(cnt):,} total={tot:,}")
