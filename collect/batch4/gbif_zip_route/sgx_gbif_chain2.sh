#!/bin/bash
# GBIF 接收链 v2:选择性片集(201 片全局网格,跳过 verbatim 区)
set -u
cd /home/ubuntu
echo "chain2 start $(date -u +%FT%T)"
python3 /home/ubuntu/sgx_wait_assemble.py || { echo ASSEMBLE_FAIL; exit 1; }
echo ZIP_ASSEMBLED_SPARSE
python3 /home/ubuntu/sgx_gbif_parse2.py /home/ubuntu/gbif_dl.zip || { echo PARSE_FAIL; exit 1; }
echo GBIF_CHAIN_DONE
