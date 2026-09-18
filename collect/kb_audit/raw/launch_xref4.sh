#!/bin/bash
# 4 节点按属性子集并行扫 truthy
COMMON='D=demiwtg-data/datasets/raw/wikimedia/latest-truthy.nt.bz2.part; KEYS=""; for i in $(seq 10000 10016); do KEYS="$KEYS demiwtg-data/datasets/raw/wikimedia/latest-truthy.nt.bz2.part-$(printf %05d $i)"; done; for i in $(seq 0 23); do KEYS="$KEYS demiwtg-data/datasets/raw/wikimedia/latest-truthy.nt.bz2.part-$(printf %05d $i)"; done; python3 /tmp/cos_cat.py $KEYS | /tmp/lbzip2 -d -c | grep -aE "prop/direct/P(SET)" | python3 /tmp/xref_extract2.py SETCSV'
ssh -o BatchMode=yes pipeline-a "cat > ~/x4.sh << 'EOA'
#!/bin/bash
${COMMON//SET/662|235}
EOA
sed -i 's/SETCSV/ P662,P235 /' ~/x4.sh 2>/dev/null; chmod +x ~/x4.sh; nohup bash ~/x4.sh > ~/x4.log 2>&1 & echo a-started"
ssh -o BatchMode=yes pipeline-b "cat > ~/x4.sh << 'EOB'
#!/bin/bash
${COMMON//SET/646|8814|244|2581|1256}
EOB
sed -i 's/SETCSV/ P646,P8814,P244,P2581,P1256 /' ~/x4.sh 2>/dev/null; chmod +x ~/x4.sh; nohup bash ~/x4.sh > ~/x4.log 2>&1 & echo b-started"
ssh -o BatchMode=yes pipeline-c "cat > ~/x4.sh << 'EOC'
#!/bin/bash
${COMMON//SET/225|3151|846|685}
EOC
sed -i 's/SETCSV/ P225,P3151,P846,P685 /' ~/x4.sh 2>/dev/null; chmod +x ~/x4.sh; nohup bash ~/x4.sh > ~/x4.log 2>&1 & echo c-started"
ssh -o BatchMode=yes pipeline-d "cat > ~/x4.sh << 'EOD'
#!/bin/bash
${COMMON//SET/245|1014|1667|594|352|18|935}
EOD
sed -i 's/SETCSV/ P245,P1014,P1667,P594,P352,P18,P935 /' ~/x4.sh 2>/dev/null; chmod +x ~/x4.sh; nohup bash ~/x4.sh > ~/x4.log 2>&1 & echo d-started"
