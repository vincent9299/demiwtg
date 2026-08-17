"""命令行入口。

用法：
  python3 collect/cli.py --taxonomy data/taxonomy/instances.json
  python3 collect/cli.py --taxonomy data/taxonomy/instances.json --consume-mode replay-rules
  python3 collect/cli.py --taxonomy data/taxonomy/instances.json \
      --sources wikimedia,wikimedia_zh,inaturalist,coco,hf_coco   # 启用可选数据集源
  python3 collect/cli.py --taxonomy data/taxonomy/instances.json --metadata-only
  python3 collect/cli.py --taxonomy data/taxonomy/instances.json --jobs 城市吉祥物

存储布局（数据湖风格，约束见 AGENTS.md 第 2 节）：
  data/dataset/
    blobs/              # 原始图片字节，内容寻址 <aa>/<sha256>.<ext>
    meta/
      images.jsonl      # 主清单（每张图一行，按 sha256 去重；instances 字段承载实体名↔图关系）
  state/collect/runs/<run_id>/    # 本批次过程产物（candidates/success/failed/rejected/stats）
  state/collect/runs/_latest      # -> 最新 <run_id>
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import sys

# 支持两种运行方式：
#   python3 collect/cli.py ...
#   python3 -m collect.cli ...
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from collect.config import EffectiveConfig, load_config, load_taxonomy
    from collect.pipeline import run
    from collect.registry import load_registry  # 触发手写适配器注册 + spec 源发现
    from collect.auto import gap as _auto_gap, discover as _auto_discover, probe as _auto_probe
    from collect.auto import synth as _auto_synth, verify as _auto_verify
    from collect.auto import govern as _auto_govern, repair as _auto_repair
    from collect.auto import orchestrate as _auto_orchestrate
    from collect import stream as _stream
    from collect import bulk as _bulk
    from collect import bench as _bench
else:
    from .config import EffectiveConfig, load_config, load_taxonomy
    from .pipeline import run
    from .registry import load_registry  # 触发手写适配器注册 + spec 源发现
    from .auto import gap as _auto_gap, discover as _auto_discover, probe as _auto_probe
    from .auto import synth as _auto_synth, verify as _auto_verify
    from .auto import govern as _auto_govern, repair as _auto_repair
    from .auto import orchestrate as _auto_orchestrate
    from . import stream as _stream
    from . import bulk as _bulk
    from . import bench as _bench

# L3 智能平面子命令（gap→discover→probe→synth→verify→govern/repair；产物人工过目，
# 晋升由闸门裁决）；orchestrate 为 L4 编排层（一轮缺口驱动闭环的确定性调度）；
# stream 为常驻流式采集入口（与批处理 run 并存）；
# bulk 为整包数据集摄入（数据集驱动反向打标，与搜索驱动 run/stream 并存的进水口）；
# bench 为带宽/下载速度压测（目标注册表 + 并发阶梯/Range 分片/镜像对比/小图画像）
_AUTO_SUBCOMMANDS = {"gap": _auto_gap, "discover": _auto_discover,
                     "probe": _auto_probe, "synth": _auto_synth,
                     "verify": _auto_verify, "govern": _auto_govern,
                     "repair": _auto_repair, "orchestrate": _auto_orchestrate,
                     "stream": _stream, "bulk": _bulk, "bench": _bench}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    # 子命令分发；非子命令首参维持原 run 参数面（向后兼容）
    if argv and argv[0] in _AUTO_SUBCOMMANDS:
        _AUTO_SUBCOMMANDS[argv[0]].main(argv[1:])
        return
    ap = argparse.ArgumentParser(description="IP 标签图片采集系统")
    ap.add_argument("--config", help="采集任务配置 JSON 路径（覆盖/临时任务用；"
                                      "常规采集建议用 --taxonomy 直读标签体系）")
    ap.add_argument("--taxonomy", help="统一标签体系实例元文件（如 data/taxonomy/instances.json）："
                                       "直接以标签体系为采集输入，实时派生全量 jobs")
    ap.add_argument("--aliases", default=None,
                    help="（已废弃）别名已并入 instances.json 的 instance.aliases；保留仅为兼容")

    # 数据湖风格布局：--meta 为 data/dataset/meta 根；--run-id 命名本批次，过程产物写
    # state/collect/runs/<run_id>（不落数据湖）；--out 默认由 --meta + --run-id 推导，
    # 可显式覆盖。运行时状态（死信队列/健康账本/LanceDB/COCO 缓存）同样在顶层 state/。
    ap.add_argument("--meta", default="data/dataset/meta",
                    help="元数据根目录（默认 data/dataset/meta），主清单 images.jsonl 写于此")
    ap.add_argument("--run-id", default=None,
                    help="本批次 ID（默认时间戳）；state/collect/runs/<run-id> 存放本批过程产物")
    ap.add_argument("--out", default=None,
                    help="本批次 JSONL 产物目录（默认 state/collect/runs/<run-id>）")
    ap.add_argument("--images-dir", default="data/dataset/blobs",
                    help="图片内容寻址存储根目录（默认 data/dataset/blobs），授权/未授权源统一落盘于此")
    ap.add_argument("--metadata-only", action="store_true",
                    help="仅跑阶段一（检索+候选 JSONL），不下载")
    ap.add_argument("--jobs", default=None,
                    help="只处理指定实例（逗号分隔）；缺省处理全部")
    ap.add_argument("--jobs-file", default=None,
                    help="只处理指定实例列表文件（逗号或换行分隔，实例名精确匹配；"
                         "与 --jobs 同时给出时取交集）")
    ap.add_argument("--source", default=None,
                    help="只处理指定来源（如 wikimedia）；缺省全部")
    ap.add_argument("--sources", default=None,
                    help="覆盖授权源列表（逗号分隔，整体替换）。默认 wikimedia,"
                         "wikimedia_zh,inaturalist；可选源如 coco/hf_coco/hf_laion/openverse")
    ap.add_argument("--unauthorized-sources", default=None,
                    help="覆盖未授权源列表（逗号分隔，整体替换）；传 none 表示全部关闭。"
                         "缺省使用内置 18 个中文源列表")
    ap.add_argument("--max-per-source", default=None, type=int,
                    help="每源每实例最多下载张数（覆盖默认；1=各活源各采 1 张最相关图）")
    ap.add_argument("--shard", default=None,
                    help="分片并发：形如 i/N（第 i 片/共 N 片，从 1 计）。多进程各跑一片，"
                         "共享同一 dataset（主清单写入有跨进程锁）；自动开启队列模式，"
                         "per-host 限速按 --rate-mult 放大")
    ap.add_argument("--queue", action="store_true",
                    help="队列模式：阶段二改为共享下载队列（多进程 worker 取件下载，"
                         "同一 URL 不并发重复下载，每候选最多 3 次重试后跳过）；"
                         "配合 --shard 时自动开启")
    ap.add_argument("--no-queue", action="store_true",
                    help="关闭 --shard 自动开启的队列模式（回退各片独立逐实例下载）")
    ap.add_argument("--queue-id", default=None,
                    help="共享队列标识（多分片必须一致；缺省由 run-id 去掉 _sN 后缀推导）")
    ap.add_argument("--rate-mult", type=float, default=None,
                    help="分片时 per-host 限速放大系数（默认 4.0；16 片聚合≈每 host 4 req/s，"
                         "调小更快但更易 429；1.0=完全不放大的单流间隔）")
    ap.add_argument("--threads", type=int, default=1,
                    help="队列模式下每进程下载线程数（默认 1；慢件/坏件不再阻塞其它下载）")
    ap.add_argument("--timeout", type=int, default=None,
                    help="检索/下载超时秒数（覆盖默认 30；网络黑洞主机多时调小可大幅提速）")
    ap.add_argument("--alias-stop", type=float, default=None,
                    help="多别名检索早停系数（覆盖默认 4；候选 >= target_count*K 即不再追加别名）")
    ap.add_argument("--ignore-dead-seed", action="store_true",
                    help="不跳过配置里 known_dead_sources 种子源（失败由候选 3 次重试兜底）")
    ap.add_argument("--reuse-phase1", action="store_true",
                    help="跳过阶段一检索，直接复用本 run 目录已有的 candidates.jsonl"
                         "（调参重启时避免重搜）")
    ap.add_argument("--consume-mode", default="replay",
                    choices=["delta", "replay", "replay-rules"],
                    help="增量消费模式：delta=只采新实例；replay=全量重放（达标跳过，默认）；"
                         "replay-rules=重放+两级身份匹配跳过（分支改名不误重下）+topup 补采缺口")
    ap.add_argument("--list-sources", action="store_true",
                    help="打印源注册表（手写模块 ∪ spec 源，含生命周期）后退出，不采集")
    args = ap.parse_args(argv)

    run_id = args.run_id or datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    meta_dir = args.meta
    # 仓库根由 --meta 向上三级推导（与 pipeline._state_dir 一致）
    _repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(os.path.normpath(meta_dir)))))
    out_dir = args.out or os.path.join(_repo_root, "state", "collect", "runs", run_id)

    reg = load_registry(meta_dir)
    if args.list_sources:
        print(f"{'名称':<16}{'mode':<6}{'provenance':<11}{'生命周期':<10}"
              f"{'语言':<6}{'授权':<5}{'类型':<10}来源定义")
        for n in reg.names():
            c = reg.card(n)
            print(f"{c.name:<16}{c.mode:<6}{c.provenance:<11}{c.lifecycle:<10}"
                  f"{c.lang:<6}{('是' if c.authorized else '否'):<5}"
                  f"{c.kind:<10}{c.spec_path or '(手写模块)'}")
        return

    if bool(args.config) == bool(args.taxonomy):
        ap.error("--config 与 --taxonomy 必须且只能提供一个")
    if args.taxonomy:
        jobs, taxonomy_name = load_taxonomy(args.taxonomy, args.aliases)
    else:
        jobs = load_config(args.config)
        taxonomy_name = os.path.basename(args.config)

    def _parse_sources(val: str) -> list:
        if val.strip().lower() == "none":
            return []
        names = [s.strip() for s in val.split(",") if s.strip()]
        bad = [n for n in names if n not in reg.names()]
        if bad:
            ap.error("未知来源 %s；已注册来源：%s" % (bad, reg.names()))
        # 非 usable（retired/degraded/candidate）源通过校验但不会参与采集，显式提示
        unusable = [(n, reg.card(n).lifecycle) for n in names
                    if n not in reg.usable_names()]
        if unusable:
            print("[warn] 以下来源不在可用生命周期（active/probation），将被跳过: %s"
                  % ["%s(%s)" % u for u in unusable], flush=True)
        return names

    if (args.sources is not None or args.unauthorized_sources is not None
            or args.max_per_source is not None or args.timeout is not None
            or args.alias_stop is not None):
        src = _parse_sources(args.sources) if args.sources is not None else None
        unauth = (_parse_sources(args.unauthorized_sources)
                  if args.unauthorized_sources is not None else None)
        for j in jobs:
            if src is not None:
                j.defaults["sources"] = src
            if unauth is not None:
                j.defaults["unauthorized_sources"] = unauth
            if args.max_per_source is not None:
                j.defaults["max_per_source"] = args.max_per_source
            if args.timeout is not None:
                j.defaults["timeout_sec"] = args.timeout
            if args.alias_stop is not None:
                j.defaults["alias_stop_factor"] = args.alias_stop
            j.effective = EffectiveConfig.resolve(j.defaults, j.overrides)

    _i = _n = 0
    if args.shard:
        try:
            _i, _n = (int(x) for x in args.shard.split("/"))
        except ValueError:
            _i, _n = 0, 0
        if _n < 1 or not (1 <= _i <= _n):
            ap.error("--shard 需要形如 i/N（1<=i<=N），如 --shard 2/4")
        jobs = [j for k, j in enumerate(jobs) if k % _n == (_i - 1)]
        mult = args.rate_mult if args.rate_mult is not None else 4.0
        if _n > 1 and jobs and mult > 0:
            jobs[0].defaults["per_host_min_interval_sec"] = (
                jobs[0].defaults.get("per_host_min_interval_sec", 1.0) * mult)
            for j in jobs:
                j.effective = EffectiveConfig.resolve(j.defaults, j.overrides)
        print(f"[shard] 本片 {_i}/{_n}：{len(jobs)} 个实例（per-host 限速放大 x{mult}）",
              flush=True)

    queue_mode = args.queue or (args.shard is not None and not args.no_queue)
    queue_id = None
    if queue_mode:
        queue_id = args.queue_id
        if not queue_id:
            m = re.match(r"^(.*)_s(\d+)$", run_id or "")
            queue_id = m.group(1) if m else (run_id or "run")

    if args.source:
        jobs = [j for j in jobs if j.source == args.source]
    only_instances = set(t.strip() for t in args.jobs.split(",")) if args.jobs else None
    exact_instances = None
    if args.jobs_file:
        with open(args.jobs_file, encoding="utf-8") as f:
            file_instances = {t.strip() for t in f.read().replace("\n", ",").split(",")
                         if t.strip()}
        exact_instances = file_instances if only_instances is None else (file_instances & only_instances)

    run(jobs, out_dir=out_dir, images_dir=args.images_dir,
        meta_dir=meta_dir, run_id=run_id,
        metadata_only=args.metadata_only, only_instances=only_instances,
        exact_instances=exact_instances,
        consume_mode=args.consume_mode,
        taxonomy_name=taxonomy_name,
        queue_mode=queue_mode, n_shards=_n or 1, shard_index=_i or 1,
        queue_id=queue_id, ignore_dead_seed=args.ignore_dead_seed,
        reuse_phase1=args.reuse_phase1,
        queue_threads=max(1, args.threads))


if __name__ == "__main__":
    main()
