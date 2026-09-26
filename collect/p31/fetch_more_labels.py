"""补齐 human_candidates.tsv 388 个候选的标签,输出带标签清单"""
import json, subprocess, time

UA = "demiwtg-taxonomy-analysis/0.1 (research)"
rows = [l.rstrip("\n").split("\t") for l in open("human_candidates.tsv")]
qids = [r[1] for r in rows]
labels = {}
for i in range(0, len(qids), 40):
    batch = qids[i:i + 40]
    url = ("https://www.wikidata.org/w/api.php?action=wbgetentities&format=json"
           f"&ids={'|'.join(batch)}&props=labels&languages=en|zh")
    for attempt in range(4):
        r = subprocess.run(["curl", "-s", "-m", "30", "-A", UA, url],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.startswith("{"):
            data = json.loads(r.stdout)
            break
        time.sleep(5 * (attempt + 1))
    for q in batch:
        lab = (data.get("entities", {}).get(q) or {}).get("labels", {})
        labels[q] = (lab.get("en", {}).get("value", ""),
                     (lab.get("zh") or lab.get("zh-hans") or {}).get("value", ""))
    time.sleep(2)

with open("human_candidates_labeled.tsv", "w") as f:
    f.write("hop\tqid\tentities\ten\tzh\n")
    for tag, q, n in sorted(rows, key=lambda r: -int(r[2])):
        en, zh = labels.get(q, ("", ""))
        f.write(f"{tag}\t{q}\t{n}\t{en}\t{zh}\n")
print("done", len(labels))
