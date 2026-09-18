#!/bin/bash
# 顺序真值审计剩余文件
for spec in "inaturalist-open-data-20260827.tar.gz|/lhcos-data/demiwtg-data/datasets/raw/inat|35093052336" \
            "commonswiki-latest-image.sql.gz|/lhcos-data/demiwtg-data/datasets/raw/wikimedia|18452452774" \
            "latest-mediainfo.json.bz2|/lhcos-data/demiwtg-data/datasets/raw/wikimedia|60493362029" \
            "CID-InChI-Key.gz|/lhcos-data/demiwtg-data/datasets/raw/pubchem|7366217952" \
            "CID-SMILES.gz|/lhcos-data/demiwtg-data/datasets/raw/pubchem|1486110215"; do
  IFS='|' read -r name cosd total <<< "$spec"
  echo "=== AUDIT $name ==="
  python3 /home/ubuntu/demi/raw/audit_file.py "$name" "$cosd" "$total"
done
echo AUDIT_CHAIN_DONE
