#!/bin/bash
SRC=/home/ubuntu/demi/raw/plantnet300k/plantnet_300K.zip
DST=/lhcos-data/demiwtg-data/datasets/raw/plantnet300k/plantnet_300K.zip
EXPECT=31670505069
cp "$SRC" "$DST" || { echo COPY_FAIL; exit 1; }
S=$(stat -c%s "$DST")
if [ "$S" = "$EXPECT" ]; then
  python3 -c "import zipfile; zipfile.ZipFile('$DST').namelist()[:5]" && { rm -f "$SRC"; echo "[$(date +%T)] SHIPPED+COS_ZIP_OK"; } || echo "COS_ZIP_CHECK_FAIL_kept_local"
else
  echo "SIZE_FAIL $S"; rm -f "$DST"
fi
