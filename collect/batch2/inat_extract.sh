#!/bin/bash
# 流式过 iNat tar, 抽 taxa/photos/observations 到本地
D=/lhcos-data/demiwtg-data/datasets/raw/inat
nohup bash -c "cat $D/inaturalist-open-data-20260827.tar.gz.part-* | tar -x -C /home/ubuntu --f=demo.tar --wildcards '*/taxa.csv' '*/photos.csv' '*/observations.csv' 2>/tmp/inat_tar.err" > /dev/null 2>&1 &
