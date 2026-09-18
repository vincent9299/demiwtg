#!/bin/bash
D="/lhcos-data/demiwtg-data/datasets/raw/pubchem"; mkdir -p "$D"
for f in CID-InChI-Key.gz CID-SMILES.gz; do
  U="https://ftp.ncbi.nlm.nih.gov/pubchem/Compound/Extras/$f"
  E=$(curl -sI "$U" --max-time 30 | grep -i content-length | tr -dc '0-9')
  for i in 1 2 3 4 5 6; do curl -C - -o "$D/$f" --connect-timeout 30 --speed-limit 10240 --speed-time 60 "$U" && break; sleep 10; done
  SZ=$(stat -c%s "$D/$f" 2>/dev/null || echo 0)
  echo "[$(date +%T)] $f $SZ/$E $([ "$SZ" = "$E" ] && echo OK || echo FAIL)"
done
echo ALL_DONE
