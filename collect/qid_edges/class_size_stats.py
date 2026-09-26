import collections
have=set()
for l in open("/home/ubuntu/haveimg_final.txt"):
    l=l.strip()
    if l.startswith("Q") and l[1:].isdigit(): have.add(l)
direct=collections.Counter(); seen=set()
for l in open("/home/ubuntu/p31_all.tsv"):
    q,_,cls=l.rstrip("\n").partition("\t")
    if q in have and q not in seen:
        seen.add(q); direct["Q"+cls.split(",")[0]]+=1
sizes=sorted(direct.values(), reverse=True)
n=len(sizes); tot=sum(sizes)
def pct(p): return sizes[min(n-1,int(n*p))]
print(f"类数={n:,} 实体总数={tot:,}")
print(f"均值={tot/n:.1f} 中位数={pct(0.5)} p90={pct(0.1)} p99={pct(0.01)} max={sizes[0]:,}")
import bisect
for th in (1,2,5,10,50,100,500,1000,5000,10000,50000):
    c=sum(1 for s in sizes if s>=th)
    m=sum(s for s in sizes if s>=th)
    print(f"  >= {th:>6} 图的类: {c:>7,} 个, 覆盖实体 {m/tot:6.1%}")
print("top10:", sizes[:10])
