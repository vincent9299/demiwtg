"""抽样核验:p31_qid_bucket_map 的主类 vs Wikidata API 真值(首 P31)"""
import json, random, subprocess, time

random.seed(42)
rows = {}
for i, l in enumerate(open("p31_sample150.tsv")):
    if i == 0:
        continue
    rows[i] = l.rstrip("\n").split("\t")
sample = random.sample(list(rows), 150)
UA = "demiwtg-taxonomy-analysis/0.1"

ok = bad = nop31 = 0
bad_examples = []
for k in range(0, len(sample), 30):
    batch = sample[k:k + 30]
    url = ("https://www.wikidata.org/w/api.php?action=wbgetentities&format=json"
           f"&ids={'|'.join(rows[i][0] for i in batch)}"
           "&props=claims")
    r = subprocess.run(["curl", "-s", "-m", "30", "-A", UA, url],
                       capture_output=True, text=True)
    ent = json.loads(r.stdout)["entities"]
    for i in batch:
        qid, main = rows[i][0], rows[i][1]
        claims = (ent.get(qid) or {}).get("claims", {})
        p31 = claims.get("P31", [])
        truth = None
        for st in p31:
            if st.get("rank") == "preferred":
                truth = "Q%d" % st["mainsnak"]["datavalue"]["value"]["numeric-id"]
                break
        if truth is None and p31:
            truth = "Q%d" % p31[0]["mainsnak"]["datavalue"]["value"]["numeric-id"]
        if truth is None:
            nop31 += 1
        elif truth == main:
            ok += 1
        else:
            bad += 1
            if len(bad_examples) < 10:
                bad_examples.append(f"{qid}: 真值{truth} vs 抽取{main}")
    time.sleep(2)
print(f"样本150:主类一致 {ok},不一致 {bad},API无P31 {nop31}")
print("不一致示例:")
for x in bad_examples:
    print(" ", x)
