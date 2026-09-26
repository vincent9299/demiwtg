import gzip, sys, collections
# 有图集
have = set()
for l in open("/home/ubuntu/haveimg_final.txt"):
    l = l.strip()
    if l.startswith("Q") and l[1:].isdigit():
        have.add(l)
# sitelink 全集
sitelink = set()
with gzip.open("/home/ubuntu/wd_full/sitelink_uniq.txt.gz", "rt") as f:
    for l in f:
        l = l.strip()
        if l:
            sitelink.add(l)
print(f"have={len(have):,} sitelink={len(sitelink):,}", flush=True)
have_cls = collections.Counter(); noimg_cls = collections.Counter()
have_no31 = noimg_no31 = 0
n = 0
for l in open("/home/ubuntu/p31_all.tsv"):
    q, _, cls = l.rstrip("\n").partition("\t")
    n += 1
    first = cls.split(",")[0]
    if q in have:
        have_cls[first] += 1
    elif q in sitelink:
        noimg_cls[first] += 1
print(f"p31 rows {n:,}", flush=True)
have31 = sum(have_cls.values())
for q, c in noimg_cls.most_common(200):
    noimg_no31 += 0
print("HAVEIMG_TOP:")
for k, c in have_cls.most_common(60):
    print(f"  {k}\t{c}")
print("NOIMG_TOP:")
for k, c in noimg_cls.most_common(60):
    print(f"  {k}\t{c}")
# 无图且完全不在 p31_all(sitelink 中无 P31 的)——由差集补
only_sitelink_nop31 = len(sitelink - have) - sum(noimg_cls.values())
print(f"SUMMARY have_with_p31={have31:,} have_total={len(have):,} "
      f"noimg_with_p31={sum(noimg_cls.values()):,} noimg_total={len(sitelink)-len(have&sitelink):,} "
      f"noimg_no_p31≈{only_sitelink_nop31:,} have_not_in_sitelink={len(have-sitelink):,}", flush=True)
