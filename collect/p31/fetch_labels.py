"""Wikidata API 标签补齐(curl 版):top 类 + 桶表 → p31_labels.tsv (qid \t en \t zh)"""
import json, subprocess, time

UA = "demiwtg-taxonomy-analysis/0.1 (research)"
qids = []
for line in open("p31_top_classes.tsv"):
    q = line.strip().split("\t")[0]
    if q.startswith("Q") and q not in qids:
        qids.append(q)
for line in open("p31_buckets.tsv"):
    line = line.strip()
    if line and not line.startswith("#"):
        q = line.split("\t")[0]
        if q not in qids:
            qids.append(q)

out = open("p31_labels.tsv", "w", encoding="utf-8")
got = miss = 0
for i in range(0, len(qids), 40):
    batch = qids[i:i + 40]
    url = ("https://www.wikidata.org/w/api.php?action=wbgetentities&format=json"
           f"&ids={'|'.join(batch)}&props=labels&languages=en|zh|zh-hans|zh-hant")
    data = None
    for attempt in range(4):
        r = subprocess.run(["curl", "-s", "-m", "30", "-A", UA, url],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.startswith("{"):
            try:
                data = json.loads(r.stdout)
                break
            except json.JSONDecodeError:
                pass
        time.sleep(5 * (attempt + 1))
    if data is None:
        print(f"batch {i} FAILED, skip")
        continue
    ent = data.get("entities", {})
    for q in batch:
        lab = (ent.get(q) or {}).get("labels", {})
        en = lab.get("en", {}).get("value", "")
        zh = (lab.get("zh") or lab.get("zh-hans")
              or lab.get("zh-hant") or {}).get("value", "")
        if en or zh:
            got += 1
            clean = lambda s: s.replace("\t", " ").replace("\n", " ")
            out.write(f"{q}\t{clean(en)}\t{clean(zh)}\n")
        else:
            miss += 1
    time.sleep(2)
out.close()
print(f"got={got} miss={miss}")
