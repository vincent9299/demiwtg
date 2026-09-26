import re
HEX = re.compile(r'u([0-9a-fA-F]{4})')

def unesc(s):
    s = HEX.sub(lambda m: chr(int(m.group(1), 16)), s)
    try:
        s = s.encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeDecodeError:
        s = "".join(c for c in s if not (0xD800 <= ord(c) <= 0xDFFF))
    return s

src = "/home/ubuntu/qid_class_labels.raw_escape.tsv"
dst = "/home/ubuntu/qid_class_labels.tsv"
n = bad = 0
with open(src, encoding="utf-8") as f, open(dst, "w", encoding="utf-8",
                                            errors="replace") as o:
    for l in f:
        p = l.rstrip("\n").split("\t")
        if len(p) >= 3:
            zh = unesc(p[2])
            if zh != p[2]:
                bad += 1
            o.write(f"{p[0]}\t{unesc(p[1])}\t{zh}\n")
        else:
            o.write(l)
        n += 1
print("rows", n, "zh_decoded", bad)
