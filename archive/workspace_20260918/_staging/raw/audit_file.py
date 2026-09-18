#!/usr/bin/env python3
# 块级真值审计：cat|wc 逐块读回 COS（绕过 cosfs stat 缓存），删坏块并列出缺失
# 用法: python3 audit_file.py <name> <cosdir> <total_bytes>
import os, sys, subprocess, glob
name, cosdir, total = sys.argv[1], sys.argv[2], int(sys.argv[3])
G = 1073741824
nparts = (total + G - 1) // G
bad, missing, good = [], [], 0
for i in range(nparts):
    p = f"{cosdir}/{name}.part-{i:05d}"
    exp = min(G, total - i * G)
    if not os.path.exists(p):
        missing.append(i); continue
    r = subprocess.run(f"cat '{p}' | wc -c", shell=True, capture_output=True, text=True)
    got = int(r.stdout.strip() or 0)
    if got != exp:
        bad.append((i, got, exp)); os.remove(p)
    else:
        good += 1
print(f"{name}: good={good}/{nparts} bad={len(bad)} missing={len(missing)}")
if bad: print("  坏块(已删,需重下):", [b[0] for b in bad])
if missing: print("  缺块:", missing)
# 输出修复队列（供 worker 直接吃）
with open(f"/home/ubuntu/demi/raw/state/repair_{name}.tsv", "w") as f:
    src_url = {
        "DF20-train_val.tar.gz": "http://ptak.felk.cvut.cz/plants/DanishFungiDataset/DF20-train_val.tar.gz",
    }.get(name)
    if src_url:
        for i in [b[0] for b in bad] + missing:
            f.write("\t".join([name, src_url, cosdir, str(total), str(i), str(i), ""]) + "\n")
