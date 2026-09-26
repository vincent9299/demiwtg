import struct, subprocess, sys
URL = "https://occurrence-download.gbif.org/occurrence/download/request/0000975-260921141020460.zip"
TOTAL = 164456211593
TAIL = 1 * 1024 * 1024
# 拉 zip 尾部 64MB
r = subprocess.run(["curl", "-sfL", "--retry", "3", "--retry-delay", "20", "-m", "300", "-r", f"{TOTAL-TAIL}-{TOTAL-1}", URL],
                   capture_output=True, timeout=320)
tail = r.stdout
print("tail bytes:", len(tail))
# 找 EOCD
i = tail.rfind(b"PK\x05\x06")
assert i >= 0, "no EOCD"
eocd = tail[i:i+22]
_, _, _, _, n_total, cd_size, cd_off, _ = struct.unpack("<IHHHHIIH", eocd)
print(f"EOCD: entries={n_total} cd_size={cd_size:,} cd_off={cd_off:,}")
if cd_off == 0xFFFFFFFF:  # zip64: locator 存绝对偏移,不信 rfind(数据里会有假 PK0606)
    k = tail.rfind(b"PK\x06\x07")
    assert k >= 0, "no zip64 locator"
    z64_abs = struct.unpack("<Q", tail[k+8:k+16])[0]
    base = TOTAL - len(tail)
    j = z64_abs - base
    z64 = tail[j:j+56]
    assert len(z64) == 56 and z64[:4] == b"PK\x06\x06", "bad z64 slice"
    t = struct.unpack("<IQHHIIQQQQ", z64)   # 56 字节定长:含两个 total entries 字段
    n_total, cd_size, cd_off = t[7], t[8], t[9]
    print(f"zip64: entries={n_total} cd_size={cd_size:,} cd_off={cd_off:,}")
# CD 在尾部 64MB 内?(通常在最后)
if cd_off >= TOTAL - TAIL:
    cd = tail[cd_off - (TOTAL - TAIL): cd_off - (TOTAL - TAIL) + cd_size]
else:
    r2 = subprocess.run(["curl", "-sfL", "--retry", "3", "--retry-delay", "20", "-m", "120", "-r", f"{cd_off}-{cd_off+cd_size-1}", URL],
                        capture_output=True, timeout=140)
    cd = r2.stdout
    print("cd pulled:", len(cd))
# 解析条目
pos, members = 0, []
while pos < len(cd) and cd[pos:pos+4] == b"PK\x01\x02":
    (sig, vmade, vneed, flags, method, mtime, mdate, crc, csize, usize,
     nlen, elen, clen, disk, iattr, eattr, lho) = struct.unpack("<IHHHHHHIIIHHHHHII", cd[pos:pos+46])
    name = cd[pos+46:pos+46+nlen].decode("utf-8", "replace")
    extra = cd[pos+46+nlen:pos+46+nlen+elen]
    # zip64 extra 解析
    if csize == 0xFFFFFFFF or usize == 0xFFFFFFFF or lho == 0xFFFFFFFF:
        ep = 0
        while ep + 4 <= len(extra):
            hid, hsz = struct.unpack("<HH", extra[ep:ep+4])
            if hid == 1:
                vals = []
                off = ep + 4
                for fld in (usize, csize, lho):
                    if fld == 0xFFFFFFFF:
                        vals.append(struct.unpack("<Q", extra[off:off+8])[0]); off += 8
                    else:
                        vals.append(None)
                u64 = [v for v in vals if v is not None]
                # 顺序固定 usize,csize,lho 但只出现 FFFF 的字段——重排
                fields = []
                off = ep + 4
                for fld in (usize, csize, lho):
                    if fld == 0xFFFFFFFF:
                        fields.append(struct.unpack("<Q", extra[off:off+8])[0]); off += 8
                k = 0
                if usize == 0xFFFFFFFF: usize = fields[k]; k += 1
                if csize == 0xFFFFFFFF: csize = fields[k]; k += 1
                if lho == 0xFFFFFFFF: lho = fields[k]; k += 1
            ep += 4 + hsz
    members.append((name, method, csize, usize, lho))
    pos += 46 + nlen + elen + clen
for m in members:
    print(f"  {m[0]:28s} method={m[1]} csize={m[2]:,} usize={m[3]:,} data_off={m[4]:,}")

import json
open("/home/ubuntu/gbif_members.json","w").write(json.dumps(members))
print("saved gbif_members.json")
