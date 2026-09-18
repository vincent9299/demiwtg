#!/usr/bin/env python3
# 为已完成的数据集写 _MANIFEST.json（拼接与溯源的关键）+ 总台账 INVENTORY.md
import json, os, glob, time
COS = "/lhcos-data/demiwtg-data/datasets/raw"
G = 1073741824

def manifest(name, cosdir, total, url, license, note=""):
    parts = sorted(glob.glob(f"{cosdir}/{name}.part-*"))
    got = sum(os.path.getsize(p) for p in parts if not p.endswith(".tmp"))
    m = {
        "name": name, "source_url": url, "license": license,
        "total_bytes": total, "chunk_bytes": G, "parts_expected": (total + G - 1) // G,
        "parts_present": len(parts), "bytes_present": got,
        "complete": got == total, "note": note,
        "reassembly": f"cat {name}.part-* > {name}",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(f"{cosdir}/_MANIFEST.json", "w") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    return m

done = [
    manifest("plantnet_300K.zip", f"{COS}/plantnet300k", 31670505069,
             "https://zenodo.org/records/5645731", "CC BY 4.0",
             "309399 条目已验证；1081 物种目录"),
    manifest("CID-InChI-Key.gz", f"{COS}/pubchem", 7366217952,
             "https://ftp.ncbi.nlm.nih.gov/pubchem/Compound/Extras/CID-InChI-Key.gz", "PD",
             "PubChem CID→InChIKey 映射，桥 P662+P235"),
    manifest("CID-SMILES.gz", f"{COS}/pubchem", 1486110215,
             "https://ftp.ncbi.nlm.nih.gov/pubchem/Compound/Extras/CID-SMILES.gz", "PD",
             "结构式离线渲染原料"),
]
partial = [
    manifest("DF20-train_val.tar.gz", f"{COS}/df20", 115741214441,
             "http://ptak.felk.cvut.cz/plants/DanishFungiDataset/DF20-train_val.tar.gz", "CC BY",
             "1,604 真菌种全尺寸"),
    manifest("latest-mediainfo.json.bz2", f"{COS}/wikimedia", 60493362029,
             "https://dumps.wikimedia.org/commonswiki/entities/latest-mediainfo.json.bz2", "CC0",
             "SDC P180 depicts：图→QID 反向映射"),
    manifest("inaturalist-open-data-20260827.tar.gz", f"{COS}/inat", 35093052336,
             "https://inaturalist-open-data.s3.amazonaws.com/metadata/inaturalist-open-data-20260827.tar.gz", "mixed",
             "快照含 taxa/photos/observations；照片许可逐条过滤"),
    manifest("commonswiki-latest-image.sql.gz", f"{COS}/wikimedia", 18452452774,
             "https://dumps.wikimedia.org/commonswiki/latest/commonswiki-latest-image.sql.gz", "CC0",
             "img_sha1/尺寸/MIME 预筛表"),
]
single = []
for nm, d, src, lic, note in [
    ("MetObjects.csv", f"{COS}/metmuseum", "github.com/metmuseum/openaccess (LFS)", "CC0", "49.2万藏品记录，isPublicDomain 过滤后取图"),
    ("DF20-metadata.zip", f"{COS}/df20", "ptak.felk.cvut.cz", "CC BY", "DF20 观测元数据（学名/GBIF）"),
]:
    p = f"{d}/{nm}"
    if os.path.exists(p):
        single.append({"name": nm, "bytes": os.path.getsize(p), "license": lic, "source": src, "note": note})
oi_files = []
for p in sorted(glob.glob(f"{COS}/openimages/*")):
    if os.path.getsize(p) > 1e6:
        oi_files.append({"name": os.path.basename(p), "bytes": os.path.getsize(p)})
smith_n = len(glob.glob(f"{COS}/smithsonian/smithsonian_batches/batch_*.tar.gz"))
inv = {
    "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    "chunked_complete": [m["name"] for m in done],
    "chunked_partial": {m["name"]: f"{m['parts_present']}/{m['parts_expected']}" for m in partial},
    "single_files": single, "openimages_annotations": oi_files,
    "smithsonian_batches": f"{smith_n}/46 (每批300片tar.gz, 48.35GB全量)",
    "pending_blocked": {
        "WIT全量TSV(27GB)": "GCS 对节点d限速0，待恢复或换机",
        "ImageNet-21K(1.1-1.3TB)": "HF gated，需 token 接受条款",
        "VisualSem(31GB)": "文件密码，需机构邮箱向作者申请",
        "BIOSCAN/HPA/OI图片": "按概念清单取，等 concept_xref 关联产出",
        "Rijksmuseum/Europeana": "需免费 API key",
        "OI val/test bbox 标注": "URL 403，需从 V7 下载页取正确链接",
    },
}
with open(f"{COS}/../INVENTORY.md", "w") as f:
    json.dump(inv, f, ensure_ascii=False, indent=2)
print("manifests:", [m["name"] + ("✓" if m["complete"] else f" {m['parts_present']}/{m['parts_expected']}") for m in done + partial])
