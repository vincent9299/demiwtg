#!/usr/bin/env python3
# wave2.py — Wave2 增量并账:权威 images.v2(16,016,943) + 四块增量(si2/th1200/met/wm404)
# 用法: python3 wave2.py <prep|join|merge>
# 纪律: 只读 in/,只写 work/ 与 out/,绝不触碰 COS 权威键。
# 依据: FINAL_MERGE_HANDOFF_20260924.md + MERGE_SPEC.md(字段冲突策略/增量并账机制)
import gzip, json, os, re, sys, time, tarfile, pickle
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

HOME = "/home/ubuntu"
W2 = f"{HOME}/merge_wave2"
IN, WK, OUT = f"{W2}/in", f"{W2}/work", f"{W2}/out"
BUCK = f"{WK}/buckets"
V1 = f"{HOME}/merge_input/qid_images.jsonl.gz"   # 唯一源,只读
TIER_RANK = {"orig": 3, "thumb1920": 2, "thumb1200": 1, None: 0, "": 0}
NC_RE = re.compile(r'nc|non[- ]?commercial', re.I)
# ---- wh 转正字段(2026-09-24 起 canonical 已带):合并时的 coalesce 规则 ----
# 规则(wh_backfill 会话 2.3 定义,Wave2 会话落码):
#   format / ext_match / size_actual / truncated:非空保留,双非空取 blob_last_modified 新者
#   blob_last_modified:时间新者胜(字符串 RFC1123 比较转 epoch)
#   width / height:null 才填(非空不覆盖)
#   blob_path_fixed / ext_fixed:为 None(未修复时)——转正后已直接写进 blob_path/ext,
#       合并时按客观字段(blob_path/ext)随胜出行带走,不再单独处理
#   wh_status(missing/bad):保留标记,双有取"更坏"(missing 优先)
WH_TS_FIELDS = ("format", "ext_match", "size_actual", "truncated", "blob_last_modified")
WH_NULL_FILL = ("width", "height")

def _lm_epoch(s):
    if not s: return 0
    import email.utils
    try: return email.utils.mktime_tz(email.utils.parsedate_tz(s))
    except Exception: return 0

def coalesce_wh(w, v):
    """桶内同 sha 吸收时的 wh 字段合并(w=胜者行,v=被吸收行),原位改 w。"""
    wl, vl = _lm_epoch(w.get("blob_last_modified")), _lm_epoch(v.get("blob_last_modified"))
    newer = v if vl > wl else w
    for k in WH_TS_FIELDS:
        if w.get(k) is None and v.get(k) is not None:
            w[k] = v[k]
        elif w.get(k) is not None and v.get(k) is not None and newer is v:
            w[k] = v[k]
    for k in WH_NULL_FILL:
        if w.get(k) is None and v.get(k) is not None:
            w[k] = v[k]
    if v.get("wh_status") == "missing" and w.get("wh_status") != "missing":
        w["wh_status"] = "missing"
MACHINES = ["VM-12-10","VM-12-11","VM-12-15","VM-12-2","VM-12-4","VM-12-5","VM-12-7",
            "VM-4-11","VM-4-12","VM-4-13","VM-4-15","VM-4-17","VM-4-2","VM-4-3","VM-4-6",
            "VM-4-7","VM-4-8","VM-8-11","VM-8-2","VM-8-4"]

def lic_rank(s):
    if not s: return 1
    s = s.lower()
    if "cc0" in s or "public domain" in s or s.strip() == "pd": return 0
    if "by-nc-sa" in s: return 4
    if "by-nc" in s: return 3
    if "by-sa" in s: return 2
    if "fair use" in s or "copyrighted" in s: return 5
    if "by" in s: return 1
    return 1

def norm_ext(e):
    e = (e or "").lower().lstrip(".")
    return {"jpeg": "jpg", "tiff": "tif"}.get(e, e) or "jpg"

def iter_gz(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", errors="replace") as f:
        for l in f: yield l

def log(*a): print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)

def jdump(o, p):
    with open(p, "w") as f: json.dump(o, f, ensure_ascii=False, indent=1)

def best_rec(a, b):
    if a is None: return b, False
    if b is None: return a, False
    a_ok = bool(a.get("sha")) and not a.get("miss")
    b_ok = bool(b.get("sha")) and not b.get("miss")
    if a_ok != b_ok: return (a if a_ok else b), True
    if not a_ok: return a, False
    ra = TIER_RANK.get(a.get("tier"), 0); rb = TIER_RANK.get(b.get("tier"), 0)
    if ra != rb: return (a if ra > rb else b), True
    return (a if a.get("ts", 0) >= b.get("ts", 0) else b), True

def cos_io():
    sys.path.insert(0, f"{HOME}/demiflow_collect")
    from cosio import COSCreds, COSIO, build_host
    return COSIO(COSCreds.discover(paths=(f"{HOME}/.cos_creds",)),
                 build_host("lhcos-368f6-1256345599", "ap-singapore"))

# ---------------- stage prep: 解包 + 归一化四块增量 ----------------
def stage_prep():
    os.makedirs(WK, exist_ok=True); os.makedirs(OUT, exist_ok=True)
    si2 = {}; si2_stat = Counter()
    for m in MACHINES:
        with tarfile.open(f"{IN}/si2/{m}-ubuntu.tgz", "r:gz") as tf:
            for mem in tf:
                if not mem.name.endswith(("ledger.jsonl", "dead.jsonl")): continue
                dead = mem.name.endswith("dead.jsonl")
                fh = tf.extractfile(mem)
                if fh is None: continue
                for bl in fh:
                    try: o = json.loads(bl)
                    except Exception: si2_stat["bad_json"] += 1; continue
                    if dead: si2_stat["si2_dead_rows"] += 1; continue
                    if o.get("src") not in ("si", "smithsonian", "SI"): continue
                    eid = o.get("extid")
                    if not eid: continue
                    ok = bool(o.get("sha256")) and not o.get("miss")
                    qids = o.get("qids") or ([o["qid"]] if o.get("qid") else [])
                    rec = {"sha": o.get("sha256"), "ext": o.get("ext"),
                           "bytes": o.get("bytes") or 0, "lic": o.get("license") or "",
                           "url": o.get("url") or "", "ts": int(o.get("ts") or 0),
                           "tier": "thumb1920" if ok else None, "qids": qids}
                    if not ok: rec["miss"] = str(o.get("miss"))[:40]
                    nb, _ = best_rec(si2.get(eid), rec)
                    si2[eid] = nb
                    si2_stat["rows"] += 1
        log("si2 tar", m, dict(si2_stat))
    si2_stat["unique_extid"] = len(si2)
    si2_stat["ok_extid"] = sum(1 for v in si2.values() if v.get("sha"))
    with open(f"{WK}/si2_map.pkl", "wb") as f: pickle.dump(si2, f, 2)
    jdump({"si2": dict(si2_stat),
           "si2_lic_top": Counter(v["lic"][:40] for v in si2.values() if v.get("sha")).most_common(10),
           "si2_ext_top": Counter(norm_ext(v.get("ext")) for v in si2.values() if v.get("sha")).most_common(10)},
          f"{WK}/prep_si2.json")

    th = {}; th_stat = Counter()
    for m in MACHINES:
        with tarfile.open(f"{IN}/th1200/{m}-ubuntu.tgz", "r:gz") as tf:
            for mem in tf:
                if not mem.name.endswith("manifest.jsonl"): continue
                fh = tf.extractfile(mem)
                if fh is None: continue
                for bl in fh:
                    try: o = json.loads(bl)
                    except Exception: th_stat["bad_json"] += 1; continue
                    cf = o.get("commons_file")
                    if not cf: continue
                    th_stat["rows"] += 1
                    if o.get("miss") or not o.get("sha256"):
                        th_stat[f"miss_{str(o.get('miss'))[:24]}"] += 1
                        continue
                    rec = {"sha": o["sha256"], "bytes": o.get("page_bytes") or 0,
                           "w": o.get("width"), "h": o.get("height"),
                           "ts": int(o.get("fetched_at") or 0)}
                    cur = th.get(cf)
                    if cur is None or rec["ts"] >= cur["ts"]: th[cf] = rec
        log("th1200 tar", m, dict(th_stat))
    th_stat["unique_cf_ok"] = len(th)
    with open(f"{WK}/th_map.pkl", "wb") as f: pickle.dump(th, f, 2)
    jdump({"th1200": dict(th_stat)}, f"{WK}/prep_th1200.json")

    met = {}
    for l in open(f"{IN}/met_redo_fix.jsonl"):
        try: o = json.loads(l)
        except Exception: continue
        if o.get("verdict") == "replaced":
            met[str(o["extid"])] = {"old_sha": o.get("old_sha"), "sha": o.get("sha"),
                                    "bytes": o.get("bytes"), "w": o.get("w"), "h": o.get("h"),
                                    "ext": o.get("ext")}
    with open(f"{WK}/met_map.pkl", "wb") as f: pickle.dump(met, f, 2)
    jdump({"met_fix_rows": len(met)}, f"{WK}/prep_met.json")

    tasks = [json.loads(l) for l in iter_gz(f"{IN}/wm404_tasks.jsonl.gz")]
    lake_fix = [json.loads(l) for l in open(f"{IN}/wm404/lake_fix_13.jsonl")]
    jdump({"tasks": len(tasks),
           "tasks_q": sum(1 for t in tasks if not t["qid"].startswith("pid:")),
           "tasks_pid": sum(1 for t in tasks if t["qid"].startswith("pid:")),
           "lake_fix": len(lake_fix),
           "task_key_dup": len(tasks) - len({(t['qid'], t['commons_file']) for t in tasks})},
          f"{WK}/prep_wm404.json")
    log("PREP_DONE", {"si2": len(si2), "th": len(th), "met": len(met), "tasks": len(tasks)})

# ---------------- stage join: fleet 恢复抽取 + 在库/落点/blob 实存 ----------------
def stage_join():
    tasks = [json.loads(l) for l in iter_gz(f"{IN}/wm404_tasks.jsonl.gz")]
    tkeys = {(t["qid"], t["commons_file"]): t for t in tasks}
    lake_fix = [json.loads(l) for l in open(f"{IN}/wm404/lake_fix_13.jsonl")]
    lake_alive_keys = {(o["qid"], o["commons_file"]) for o in lake_fix}

    # fleet 恢复:快照 0923 的 wk_backfill manifests(注意成员名无前导 ./)
    fleet = {}; scan = Counter()
    for m in MACHINES:
        with tarfile.open(f"{IN}/snap0923/{m}-ubuntu.tgz", "r:gz") as tf:
            for mem in tf:
                nm = mem.name.lstrip("./")
                if not nm.startswith("wk_backfill/"): continue
                if not nm.endswith("manifest.jsonl"): continue
                fh = tf.extractfile(mem)
                if fh is None: continue
                for bl in fh:
                    if b'"sha256"' not in bl: continue
                    try: o = json.loads(bl)
                    except Exception: continue
                    k = (o.get("qid") or "", o.get("commons_file") or "")
                    if k not in tkeys: continue
                    if not o.get("sha256") or o.get("miss"): continue
                    if not ((o.get("tier") is not None) or (o.get("page_bytes") or 0) >= 3000):
                        scan["suspect_small"] += 1; continue
                    rec = {"qid": k[0], "commons_file": k[1], "sha256": o["sha256"],
                           "ext": o.get("ext"), "tier": o.get("tier") or "orig",
                           "license": o.get("license") or tkeys[k].get("license") or "",
                           "bytes": o.get("page_bytes") or 0,
                           "ts": int(o.get("fetched_at") or 0), "from": "fleet"}
                    cur = fleet.get(k)
                    if cur is None or rec["ts"] >= cur["ts"]: fleet[k] = rec
                    scan["hit"] += 1
                    scan[f"hit_{nm.split('/')[1].split('-VM')[0]}"] += 1
        log("fleet tar", m, dict(scan))

    cand = dict(fleet)
    for o in lake_fix:
        k = (o["qid"], o["commons_file"])
        rec = {"qid": o["qid"], "commons_file": o["commons_file"], "sha256": o["sha256"],
               "ext": o.get("ext"), "tier": o.get("tier") or "orig",
               "license": tkeys[k].get("license") or "", "bytes": o.get("bytes") or 0,
               "ts": int(time.time()), "from": "lake"}
        if k in cand: scan["lake_over_fleet"] += 1
        cand[k] = rec

    # 在库检查:(qid,cf) 级;同时收集 死键在主表 的行 + 死键cf→v2现状
    dead_all = set(tkeys.keys()) - set(cand.keys()) - lake_alive_keys
    cand_cf = {c for (_, c) in cand}
    dead_cf = {c for (_, c) in dead_all}
    cf2qids = defaultdict(set)          # 候选cf -> 主表已有 qids
    dead_main = {}                      # sha -> 行(死键命中主表)
    pstat = Counter()
    for l in iter_gz(f"{IN}/images.v2.jsonl.gz"):
        o = json.loads(l)
        refs = o.get("refs") or []
        hit_c = [r.get("external_id") for r in refs if r.get("external_id") in cand_cf]
        hit_d = [r.get("external_id") for r in refs if r.get("external_id") in dead_cf]
        if hit_c:
            for c in hit_c: cf2qids[c] |= set(o.get("qids") or [])
            pstat["cand_cf_rows"] += 1
        if hit_d:
            dk = [(q, c) for c in hit_d for q in (o.get("qids") or []) if (q, c) in dead_all]
            if dk:
                dead_main[o["sha256"]] = {"row": o, "dead_keys": dk}
                pstat["dead_in_main_rows"] += 1
    # add40 = 候选 - 已在库((qid,cf) 已存在)
    add40 = {}
    for k, v in cand.items():
        if v["qid"] in cf2qids.get(k[1], ()):
            scan["already_in_v2"] += 1
            continue
        add40[k] = v
    scan["candidate_keys"] = len(cand)
    scan["add40_after_presence_filter"] = len(add40)
    with open(f"{WK}/wm404_add40.pkl", "wb") as f: pickle.dump(add40, f, 2)
    with open(f"{WK}/wm404_cand_all.pkl", "wb") as f: pickle.dump(cand, f, 2)  # 全部恢复候选(含已在库)

    # 死键在主表行 → COS blob 实存 HEAD
    blobcheck = {}
    if dead_main:
        io = cos_io()
        def head_one(sha_bp):
            sha, bp = sha_bp
            try:
                sz = io.head("lhcos-data/demiwtg-data/datasets/demiwtg/kb/" + bp)
            except Exception:
                sz = None
            return sha, sz
        with ThreadPoolExecutor(8) as ex:
            for sha, sz in ex.map(head_one, [(s, d["row"]["blob_path"]) for s, d in dead_main.items()]):
                blobcheck[sha] = {"exists": bool(sz), "size": sz}
    bc_stat = Counter(("blob_ok" if v["exists"] else "blob_missing") for v in blobcheck.values())
    jdump({"dead_main": {s: {"dead_keys": d["dead_keys"], "blob_path": d["row"]["blob_path"],
                             "qids": d["row"]["qids"], "exists": blobcheck.get(s, {}).get("exists"),
                             "size": blobcheck.get(s, {}).get("size")}
                          for s, d in dead_main.items()},
           "blobcheck_stat": dict(bc_stat)},
          f"{WK}/wm404_main_blobcheck.json")

    # 落点鉴定(deadletter/pid/v1)
    dl_hits = {}
    for l in iter_gz(f"{IN}/deadletter.v2.jsonl.gz"):
        try: o = json.loads(l)
        except Exception: continue
        k = (o.get("qid") or "", o.get("external_id") or "")
        if k in tkeys: dl_hits[k] = o.get("dead_class")
    pid_hit = set()
    for l in iter_gz(f"{IN}/qid_images_v2_pid.jsonl.gz"):
        if '"commons_file"' not in l: continue
        try: o = json.loads(l)
        except Exception: continue
        k = (o.get("qid") or "", o.get("commons_file") or "")
        if k in tkeys: pid_hit.add(k)
    v1_hit = {}
    for l in iter_gz(V1):
        try: o = json.loads(l)
        except Exception: continue
        k = (o.get("qid") or "", o.get("commons_file") or "")
        if k in tkeys: v1_hit[k] = o
    where = defaultdict(Counter)
    for k in tkeys:
        ns = "pid" if k[0].startswith("pid:") else "q"
        w = where[ns]
        if k in dl_hits: w["deadletter"] += 1
        if k in pid_hit: w["pid_file"] += 1
        if k not in dl_hits and k not in pid_hit:
            w["v1_only" if k in v1_hit else "nowhere"] += 1
        if k in lake_alive_keys or k in add40: w["alive"] += 1
    rep = {"scan": dict(scan), "fleet_found": len(fleet), "add40": len(add40),
           "presence_scan": dict(pstat), "blobcheck": dict(bc_stat),
           "dl_hit_classes": Counter(dl_hits.values()).most_common(),
           "v1_hit_rows": len(v1_hit),
           "where": {k: dict(v) for k, v in where.items()},
           "sample_fleet": list(fleet.values())[:2]}
    jdump(rep, f"{WK}/join_wm404.json")
    log("JOIN_DONE", json.dumps(rep)[:1200])

# ---------------- stage merge: 应用四块增量 ----------------
def stage_merge():
    for p in [f"{WK}/si2_map.pkl", f"{WK}/th_map.pkl", f"{WK}/met_map.pkl",
              f"{WK}/wm404_add40.pkl", f"{WK}/wm404_cand_all.pkl",
              f"{WK}/wm404_main_blobcheck.json"]:
        if not os.path.exists(p): sys.exit(f"missing {p}; run prep/join first")
    with open(f"{WK}/si2_map.pkl", "rb") as f: si2 = pickle.load(f)
    with open(f"{WK}/th_map.pkl", "rb") as f: th = pickle.load(f)
    with open(f"{WK}/met_map.pkl", "rb") as f: met = pickle.load(f)
    with open(f"{WK}/wm404_add40.pkl", "rb") as f: add40 = pickle.load(f)
    with open(f"{WK}/wm404_cand_all.pkl", "rb") as f: cand_all = pickle.load(f)
    bc = json.load(open(f"{WK}/wm404_main_blobcheck.json"))
    dead_main_rows = {s: d for s, d in bc["dead_main"].items()}
    drop_shas = {s for s, d in dead_main_rows.items() if not d["exists"]}  # 挂空账才移除

    tasks = [json.loads(l) for l in iter_gz(f"{IN}/wm404_tasks.jsonl.gz")]
    tlicense = {(t["qid"], t["commons_file"]): t.get("license") for t in tasks}
    alive_keys = set(cand_all.keys())   # 全部恢复候选(含已在库的)都算活,不标死
    dead_keys = {(t["qid"], t["commons_file"]) for t in tasks} - alive_keys
    cf404 = {c for (_, c) in dead_keys}

    # ---- pass A:基线画像 + si 已有 extid ----
    si_ext = set(); prof = Counter()
    for l in iter_gz(f"{IN}/images.v2.jsonl.gz"):
        o = json.loads(l)
        prof["rows"] += 1; prof[f"src_{o.get('source')}"] += 1
        for r in o.get("refs") or []:
            if r.get("source") == "si": si_ext.add(r.get("external_id"))
    prof["si_extids"] = len(si_ext)
    log("passA", dict(prof))

    # ---- pass B:流式套补丁,按最终 sha 路由进 256 桶 ----
    os.makedirs(BUCK, exist_ok=True)
    ws = {f"{b:02x}": gzip.open(f"{BUCK}/b{b:02x}.jsonl.gz", "wt", compresslevel=1) for b in range(256)}
    dropped = []
    st = Counter(); met_seen = set(); th_seen = set()
    for l in iter_gz(f"{IN}/images.v2.jsonl.gz"):
        o = json.loads(l)
        sha0 = o.get("sha256")
        if sha0 in drop_shas:
            dropped.append(o)
            st["wm404_dead_moved_to_deadletter"] += 1
            continue
        if sha0 in dead_main_rows and dead_main_rows[sha0]["exists"]:
            st["wm404_dead_link_blob_alive_kept"] += 1
        src = o.get("source"); refs = o.get("refs") or []
        if src == "met":
            for r in refs:
                fx = met.get(r.get("external_id")) if r.get("source") == "met" else None
                if fx is None: continue
                if fx["old_sha"] and o.get("sha256") != fx["old_sha"]:
                    st["met_old_sha_mismatch"] += 1
                    if st["met_old_sha_mismatch"] <= 10:
                        log("MET_MISMATCH", o.get("sha256"), fx["old_sha"], r.get("external_id"))
                    break
                met_seen.add(r["external_id"])
                o["sha256"] = fx["sha"]
                o["size_bytes"] = fx["bytes"] or o.get("size_bytes")
                o["blob_path"] = f"blobs/{fx['sha'][:2]}/{fx['sha']}.{norm_ext(fx.get('ext') or o.get('ext'))}"
                o["ext"] = norm_ext(fx.get("ext") or o.get("ext"))
                if fx.get("w") and not o.get("width"): o["width"], o["height"] = fx["w"], fx["h"]
                o["fix_state"] = "meta_refetch"
                st["met_applied"] += 1
                break
        hit_th = None
        for r in refs:
            if r.get("external_id") in th: hit_th = r["external_id"]; break
        if hit_th is not None:
            if o.get("tier") == "thumb1200":
                up = th[hit_th]; th_seen.add(hit_th)
                o["sha256"] = up["sha"]
                o["size_bytes"] = up["bytes"] or o.get("size_bytes")
                o["blob_path"] = f"blobs/{up['sha'][:2]}/{up['sha']}.{o.get('ext') or 'jpg'}"
                o["tier"] = "thumb1920"
                if up.get("w") and not o.get("width"): o["width"], o["height"] = up["w"], up["h"]
                o["fix_state"] = "th1920"
                st["th_applied"] += 1
            else:
                st["th_cf_wrong_tier"] += 1
        ws[o["sha256"][:2]].write(json.dumps(o, ensure_ascii=False) + "\n")
        st["v2_rows_out"] += 1
    st["met_missing"] = len(set(met) - met_seen)
    st["th_never_matched"] = len(set(th) - th_seen)

    # ---- 追加 si2 新行 ----
    for eid, rec in si2.items():
        if not rec.get("sha"): continue
        if eid in si_ext:
            st["si2_skip_si1_wins"] += 1; continue
        qids = sorted(set(rec.get("qids") or []))
        if not qids:
            st["si2_no_qids_dropped"] += 1; continue
        sha = rec["sha"]
        lz = "nc" if (rec.get("lic") and NC_RE.search(rec["lic"])) else ""
        row = {"sha256": sha,
               "blob_path": f"blobs/{sha[:2]}/{sha}.{norm_ext(rec.get('ext'))}",
               "ext": norm_ext(rec.get("ext")), "size_bytes": rec.get("bytes") or 0,
               "width": None, "height": None, "tier": "thumb1920",
               "license": rec.get("lic") or "", "license_url": "", "license_zone": lz,
               "source": "si", "fetched_at": rec.get("ts") or 0, "fix_state": "si2",
               "qids": qids,
               "refs": [{"source": "si", "external_id": eid, "orig_url": rec.get("url") or None,
                         "relation_type": "curated", "fetched_at": rec.get("ts") or 0}]}
        ws[sha[:2]].write(json.dumps(row, ensure_ascii=False) + "\n")
        st["si2_new"] += 1

    # ---- 追加 wm404 恢复(Q 入主表桶,pid 入 sidecar) ----
    with open(f"{OUT}/pid_additions.jsonl", "w") as pid_add:
        for (qid, cf), rec in add40.items():
            sha = rec["sha256"]
            if qid.startswith("pid:"):
                pid_add.write(json.dumps({"qid": qid, "commons_file": cf, "sha256": sha,
                                          "ext": rec.get("ext"), "tier": rec.get("tier"),
                                          "license": rec.get("license"),
                                          "page_bytes": rec.get("bytes") or 0,
                                          "note": "wm404_recovered"}, ensure_ascii=False) + "\n")
                st["wm404_add_pid"] += 1
                continue
            url = f"https://commons.wikimedia.org/wiki/File:{cf.replace(' ', '_')}"
            row = {"sha256": sha,
                   "blob_path": f"blobs/{sha[:2]}/{sha}.{norm_ext(rec.get('ext'))}",
                   "ext": norm_ext(rec.get("ext")), "size_bytes": rec.get("bytes") or 0,
                   "width": None, "height": None, "tier": rec.get("tier") or "orig",
                   "license": tlicense.get((qid, cf)) or rec.get("license") or "",
                   "license_url": "", "license_zone": "",
                   "source": "wm", "fetched_at": rec.get("ts") or 0,
                   "fix_state": "wm404_recovered", "qids": [qid],
                   "refs": [{"source": "wm", "external_id": cf, "orig_url": url,
                             "relation_type": "definitional", "fetched_at": rec.get("ts") or 0}]}
            ws[sha[:2]].write(json.dumps(row, ensure_ascii=False) + "\n")
            st["wm404_add_q"] += 1
    for w in ws.values(): w.close()
    log("passB done", dict(st))

    # ---- pass C:桶内按 sha 合并 ----
    cst = Counter()
    with gzip.open(f"{OUT}/images.v2.wave2.jsonl.gz", "wt", compresslevel=1) as out:
        for b in range(256):
            pre = f"{b:02x}"
            groups = {}
            for l in iter_gz(f"{BUCK}/b{pre}.jsonl.gz"):
                o = json.loads(l)
                sha = o["sha256"]
                g = groups.get(sha)
                if g is None:
                    groups[sha] = o; continue
                cst["absorbed"] += 1
                g["qids"] = sorted(set(g.get("qids") or []) | set(o.get("qids") or []))
                gr = {(r.get("source"), r.get("external_id")): r for r in g.get("refs") or []}
                for r in o.get("refs") or []:
                    kk = (r.get("source"), r.get("external_id"))
                    cur = gr.get(kk)
                    if cur is None or (r.get("fetched_at") or 0) > (cur.get("fetched_at") or 0):
                        gr[kk] = r
                g["refs"] = list(gr.values())
                if lic_rank(o.get("license") or "") > lic_rank(g.get("license") or ""):
                    g["license"] = o.get("license")
                if o.get("license_zone") == "nc": g["license_zone"] = "nc"
                if TIER_RANK.get(o.get("tier"), 0) > TIER_RANK.get(g.get("tier"), 0):
                    g["tier"] = o.get("tier")
                if (o.get("size_bytes") or 0) > (g.get("size_bytes") or 0): g["size_bytes"] = o["size_bytes"]
                if o.get("width") and not g.get("width"):
                    g["width"], g["height"] = o.get("width"), o.get("height")
                if g.get("fix_state") in (None, "ok") and o.get("fix_state") not in (None, "ok"):
                    g["fix_state"] = o.get("fix_state")
                if (o.get("fetched_at") or 0) > (g.get("fetched_at") or 0): g["fetched_at"] = o["fetched_at"]
                coalesce_wh(g, o)   # wh 转正字段:非空 coalesce、时间新者胜(见文件头规则)
            for g in groups.values():
                out.write(json.dumps(g, ensure_ascii=False) + "\n")
                cst["final_rows"] += 1
                cst[f"src_{g.get('source')}"] += 1
            if b % 32 == 0: log("passC", pre, dict(cst))
    log("passC done", dict(cst))

    # ---- deadletter 重写 + pid 死行 sidecar ----
    v1 = {}
    for l in iter_gz(V1):
        try: o = json.loads(l)
        except Exception: continue
        k = (o.get("qid") or "", o.get("commons_file") or "")
        if k in dead_keys: v1[k] = o
    dst = Counter(); dead_written = set()
    with gzip.open(f"{OUT}/deadletter.v2.wave2.jsonl.gz", "wt", compresslevel=1) as out, \
         open(f"{OUT}/wm404_pid_dead_final.jsonl", "w") as pid_dead:
        for l in iter_gz(f"{IN}/deadletter.v2.jsonl.gz"):
            o = json.loads(l)
            k = (o.get("qid") or "", o.get("external_id") or "")
            if k in dead_keys:
                if o.get("dead_class") != "dead_404_final":
                    o["prev_dead_class"] = o.get("dead_class")
                o["dead_class"] = "dead_404_final"
                dst["reclassified"] += 1
                dead_written.add(k)
            out.write(json.dumps(o, ensure_ascii=False) + "\n")
            dst["rows"] += 1
        for o in dropped:  # 主表移除的挂空账行,整行进死信(保留 qids/refs 供重载)
            dead_written |= {(q, c) for (q, c) in dead_main_rows[o["sha256"]]["dead_keys"]}
            o2 = {"qid": (o.get("qids") or [""])[0], "source": "wm",
                  "external_id": "", "orig_url": "", "dead_class": "dead_404_final",
                  "fetched_at": o.get("fetched_at") or 0, "moved_row": o}
            out.write(json.dumps(o2, ensure_ascii=False) + "\n")
            dst["rows"] += 1; dst["appended_moved_rows"] += 1
        for k in dead_keys:
            if k in dead_written: continue
            v = v1.get(k)
            rec = {"qid": k[0], "source": "wm", "external_id": k[1],
                   "orig_url": (v or {}).get("content_url") or "",
                   "dead_class": "dead_404_final",
                   "fetched_at": int((v or {}).get("fetched_at") or 0),
                   "sha256_v1": (v or {}).get("sha256")}
            if k[0].startswith("pid:"):
                pid_dead.write(json.dumps(rec, ensure_ascii=False) + "\n")
                dst["pid_dead_sidecar"] += 1
            else:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                dst["rows"] += 1; dst["appended_from_v1"] += 1
    rep = {"baseline": dict(prof), "merge": dict(st), "bucket_merge": dict(cst),
           "deadletter": dict(dst), "dead_keys_total": len(dead_keys),
           "alive40_used": len(add40), "drop_shas": len(drop_shas),
           "dead_main_stat": bc["blobcheck_stat"],
           "ts": time.time(),
           "watermark": "manifest_snapshots_0923+final_20260924+met_fix+wm404_final"}
    jdump(rep, f"{OUT}/wave2_report.json")
    log("MERGE_DONE", json.dumps(rep)[:1500])

if __name__ == "__main__":
    {"prep": stage_prep, "join": stage_join, "merge": stage_merge}[sys.argv[1]]()
