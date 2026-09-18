#!/bin/bash
KEYS=""
for i in $(seq 0 6); do KEYS="$KEYS demiwtg-data/datasets/raw/wikimedia/commonswiki-latest-page.sql.gz.part-$(printf %05d $i)"; done
python3 /home/ubuntu/demi/raw/cos_cat.py $KEYS | zcat | python3 /home/ubuntu/demi/raw/page_parse2.py
