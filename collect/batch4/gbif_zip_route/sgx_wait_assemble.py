import os, sys, time
sys.path.insert(0, "/home/ubuntu/demiflow_collect")
from cosio import COSCreds, COSIO, build_host

TOTAL = 164456211593
PSZ = 512 * 1024 * 1024
# 选择性计划:occurrence 区(含头部小件)idx 0..157,multimedia+中央目录区 idx 264..306
NEED = sorted(set(range(0, 158)) | set(range(264, 307)))
PFX = "lhcos-data/demiwtg-data/kb/osm_parts/gbif/"

io = COSIO(COSCreds.discover(paths=("/home/ubuntu/.cos_creds",)),
           build_host("lhcos-368f6-1256345599", "ap-singapore"))


def have_map():
    have = {}
    for k in io.list_prefix(PFX):
        name = k.rsplit("/", 1)[-1]
        if name.startswith("g") and name.endswith(".bin"):
            try:
                have[int(name[1:-4])] = k
            except ValueError:
                pass
    return have


# 1) 等片齐(201 片全局网格;旧 m*_p* 片不计)
while True:
    have = have_map()
    missing = [i for i in NEED if i not in have]
    print(f"waiting g-pieces: {len(have)}/{len(NEED)} missing={missing[:8]}", flush=True)
    if not missing:
        break
    time.sleep(120)

# 2) 稀疏装配:全尺寸文件,verbatim 区留洞(unzip -p 只读目标成员,永不触洞)
OUT = "/home/ubuntu/gbif_dl.zip"
if os.path.exists(OUT):
    os.unlink(OUT)
with open(OUT, "wb") as f:
    f.truncate(TOTAL)
for n, idx in enumerate(NEED):
    off = idx * PSZ
    end = min(off + PSZ, TOTAL)
    want = end - off
    tmp = f"/tmp/asp_{idx}.bin"
    if not io.download_to(have[idx], tmp):
        print(f"DL_FAIL idx={idx}"); sys.exit(3)
    if os.path.getsize(tmp) != want:
        print(f"SIZE_FAIL idx={idx}"); sys.exit(4)
    with open(OUT, "r+b") as f, open(tmp, "rb") as s:
        f.seek(off)
        while True:
            b = s.read(64 << 20)
            if not b:
                break
            f.write(b)
    os.unlink(tmp)
    if n % 20 == 0:
        print(f"  assembled {n}/{len(NEED)}", flush=True)
print(f"ASSEMBLE_DONE size={os.path.getsize(OUT):,}", flush=True)
