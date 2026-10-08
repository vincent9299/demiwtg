# 图片获取平台化与概念补图交付（2026-10-03）

## 最新配置与执行：每概念 30 张正例

用户确认 Google 主搜索，Bing Images / Yandex Images 可作备用并走会话池；网站过滤、搜索源和代理均归业务配置。当前模块模板为 `preparation/concept_image_backfill/configs/web_policy.json`，notebook 第 2 格明确加载和展示，run 固化完整参数。来源筛选参考 concepts 最新模块模板，共 113 个域名和 24 条路径规则，同时检查来源页、原图和下载跳转；排除原因进入 links_rNN。平台 URLPolicy 与 fetch 共用实现，不硬编码业务网站名单。

固定采纳范围 2261，用户将目标由 10 改为 30：初始 52 已达标、2209 待补、还缺 56265 条审核正例关系。正式 run `adopted2261_target30_20261003` 已于 2026-10-03 08:21 UTC 提交，PID 45895；请以 submission.json 的状态核对进程，PID 不是完成证据。预览为 `adopted2261_target30_plan_20261003`。每轮最多 40 候选，最多 6 轮，总查询/下载/审核上限 15000/200000/30000；搜索并发 24、图片获取 64、审核 64。既有成功跨 URL 索引复用，审核只复用相同身份定义的关系。

参考主池的 60 秒是单 Google SID 间隔，配 32–128 个会话和并发 12–24；不是全局每分钟一次。之前本会话将其套到浏览器 JS 子请求且使用小型探测池，导致超时，已说明并保留失败。当前 Google HTTP→Bing/Yandex 的配置不使用浏览器。原图获取先 5 个静态出口，每出口并发上限 16，失败后最多一次会话池获取；全局图片上限 64。测试证实并发上限、发起间隔、取消归还、冷却重查和既有回执身份保持；当前静态健康作用于声明的域名池，通配池里某网站拒绝可能使该路线在其他网站暂时冷却，是当前效率限制，未隐瞒为供应商普遍故障。

真实 4 查询全部取得 Bing/Yandex 原图线索，Google 仍无成功图片结果。80 个结果中按来源政策排除 12 个，选最多 32 个允许原图下载，21 成功/11 失败，成功均为 download；部分通过静态→会话恢复。搜索约 58 秒、获取约 62 秒，不能外推为两小时保证。证据 `_demiflow/image_acquisition_validation_20261003/multisource_parallel/{search.json,download.json}`，无模型调用；首次单独注入静态故障的会话下载探测 ConnectTimeout 仍保留，后来真实批次提供了会话成功证据。来源、代理、图片和正式 pipeline 回归 77 项通过。先前全项目布局检查 49 项通过后因无关全库源码清单遍历过慢中止，不能记为完整布局检查通过。

本次 Google JS 新探测还遇到 adapter_returned_before_http_completed，之前的 captcha 回执也保留；仅本地后端测试通过不代表线上稳定。当前生产未使用该后端。Wikipedia 导入已交其他会话监控，本会话未接管。

## 已实现的分工

[补图 pipeline](../preparation/concept_image_backfill/README.md) 负责固定 ready 范围、已有正例缺口、逐轮搜索、SHA 去重、总预算及覆盖交付；[正式概念图片审核](../preparation/concept_positive_images/README.md) 负责视觉与综合判断。demiflow 的 `Dataset.search_web` 返回图片线索，新增 `Dataset.fetch_images` 获取、验证并保存原始像素。平台 API 和资源边界见 [image-acquisition.md](../../demiflow/docs/image-acquisition.md)。

入口、四个 cell 的 notebook、operators、prompts 归属说明、测试和 README 已完整落在 `preparation/concept_image_backfill`，并登记项目布局检查。未删除旧 collect 生产者或其账本，未把新平台接口的交付等同于旧所有消费者均已改造。

## 物理存储

迁移前，名称目录使用工作区 `objects`；QID 公共宽表和 100k subset 已落地的图片共用 `collect/download/blobs`，两组物理存储并不统一。

现在本地已有内容统一接入 `preparation/datasets/images/objects/<sha[:2]>/<sha>`。源目录分别扫描 2,135,967 和 491,054 个文件，共 2,627,021；错误／异常文件均为 0，新增 payload 字节为 0。不同 inode 的 1581 份同 SHA 重复文件经过两边完整 SHA 和长度核对后收敛；其余由硬链接保持原 inode，不宣称重新解码过全库。旧路径仍可读取同一份文件。

DPC 共享文件系统不支持目录与链接的原子交换，因此采用保留目录的硬链接方案，避免正在读旧 URI 的消费者出现空窗。新 LocalObjectStore 的旧根声明通过精确 artifact_locations 映射到新根；新图片获取直接使用新位置。没有本地 URI 的 QID 行仍保持未落地状态，不冒称全集图片都已下载。

回执：工作区 `_demiflow/image_library_unification_20261003/consolidated.json`、`catalog_progress.json`、`downloads_progress.json`；固定表引用抽查为 `fixed_table_reference_check.json`。旧宽表 URI／版本未改写。

URL 索引为 `preparation/datasets/images/library/index.sqlite`。历史 CAS 已能按真实已知 SHA 复用；没有历史 URL→SHA 记录时不会猜映射。新获取成功后登记 URL，再次请求先本地校验复用。

## 代理策略与首轮实测

用户最新要求为“外网搜索用会话池 IP，外网下载用静态代理 IP”。本轮配置已分别通过 `search_options.search_session_pool` 和 `fetch_options.fetch_proxy_routes` 接入正式 WebSession。账号、供应商格式与 IP 在业务配置／环境中；平台只维护会话、路由、限流、冷却和传输。

平台新增显式 `'*'` 默认下载路由和代理链 `allowed_domains=['*']` 声明，具体域名优先、重定向逐跳重选。旧未声明通配的配置保持原行为。当前静态池使用 concepts 已有的 5 个 Secret 引用，经配置中的入口代理连接；不把图片下载绑定到搜索租约。

最新策略的小样本记录在 `_demiflow/image_acquisition_validation_20261003/`：

| 环节 | 实际结果 | 回执 |
| --- | --- | --- |
| 搜索会话池 | 创建 2 个租约；Commons 图片查询成功并保留原图／缩略图／页面；同次 Bing 查询超时 | session_pool_images.json |
| 静态下载池 | 2 个真实 CONNECT 隧道；静态出口 1 的 Commons 原图返回 429，静态出口 2 下载花瓣原图成功，245903 字节 | static_downloads.json |
| 本地复用 | 两张已验证图片在新账本下复用，HTTP hops=0、wire_bytes=0 | reuse_success_only.json |

这一轮曾将待运行配置设为 `wikicommons.images`；后续按用户要求改为 Google Images 优先，当前配置见下节。Bing 在公司出口成功的历史结果不作为当前会话池可用性的证明。Google 图片在前期探测返回 403。来源与出口的失败都保存。

前期独立下载探测的第二轮曾再次请求一个上一轮 429 的 URL，该次也返回 429；完整失败保留在 local_reuse_results.json。最终“零 HTTP 本地复用”结论只依据之后针对两张成功图片的 reuse_success_only.json，不把失败请求算进去。

## 早期状态：Google 图片平台化与图数分布（已由下述业务配置替代）

用户随后明确：以 Google 图片质量和 demiflow 平台能力为主线，业务 collect 旧代码不参与实现；Wikimedia Commons 不作自动备用，确需使用其搜索时先询问 UA。当前完整配置为 `concept_image_backfill/runs/adopted2261_google_images_js_20261003/config.json`，当前预览为 `adopted2261_google_images_plan_v2_20261003`，均使用固定采纳的 2,261 条；全量补图仍未启动。

Google Images 已接入平台显式 `backend=http/browser`，由浏览器执行 JS，解析原图／来源页面绑定并保留缩略图线索。HTTP 失败后，显式备用为同一 Google Images 的浏览器后端。两个池各最多 4 个会话、TTL 300 秒；HTTP 间隔 60 秒、JS 子请求间隔 1 秒。此前把 60 秒套给每个 JS 子请求导致两次超时，已按该原因调整配置，旧失败保留。修正后中英文请求均进入浏览器并在约 8.1/9.4 秒返回 captcha；没有得到真实 Google 图片候选，不能宣称线上成功。

下载侧已新增平台 `fetch_proxy_routes['*'].fallback_session_pool`：静态池出现 403/429/5xx、网络失败或全池冷却后，最多再用会话池获取一次；完整 attempts 区分 static/session，永久错误、内容错误和成功不触发额外尝试。创建额度在原生 journal 中持久记录，过期先释放资源再换代，取消释放租约。当前下载池最多 4 个会话，每 60 秒创建不超过 12 个。业务声明供应商和 Secret，平台不引用项目 collect 代码。

当前 notebook 最后一格已提供独立只读的精确分布、分桶柱状图、最低正例图数对应概念量及名单预览。按当前审定和不同 SHA 的综合机审 match 统计：0 张为 486 个；至少 1/2/3/5/10 张为 1775/1560/1342/908/282 个。`MIN_POSITIVE_IMAGES` 是查看门槛，不启动出题。完整图数分布在 `_demiflow/image_acquisition_validation_20261003/adopted2261_image_count_distribution.json`，实际 Notebook 输出来自固定 coverage 表。

相关测试：Google 图片及已有 HTTP／真实 Chromium 后端 35 项通过；下载会话降级、静态池、图片/文档 Dataset 与正式补图入口 65 项通过。采纳范围与旧身份不误复用等补图流程首轮 15 项通过，一项暴露 DataFusion 监控临时目录删除竞态；修复为有界逐目录扫描后，该预算行为回归通过，目录监控 4 项及其余原生执行 19 项通过。只忽略扫描期间消失的路径，其他 I/O 和扫描超限继续报错；每次最多 100 万条目、64 层、5 秒检查预算，不把文件系统阻塞说成可强制中断。

真实记录位于 `_demiflow/image_acquisition_validation_20261003/google_images_platform_paced/`：本轮未查询 Commons，也未调用模型。两张固定历史 Bing 候选通过静态代理实际下载成功，独立于 Google 失败；不作为新 Google 端到端成功的证据。

## Google 优先与固定采纳范围（前一轮配置，已被上节替代）

概念会话完成修复和发布后，补图直接读取 `concepts/datasets/adopted__taxonomy3315_adopted_20261003_v1.lance@1`，共 2,261 条。新增正式 `adopted_source` 输入，与历史 `concept_sources` 互斥；采纳状态、ready 身份及主键检查失败时中止，不静默丢行。基线仍按 concept_id、assessment_id、definition_digest 精确匹配，修复后的 6 条不能继承旧身份的图片判断。

最终缺口预览 `adopted2261_image_supply_plan_20261003` 已提交：2,261 个概念、12,218 条已有正例关系；282 个已满足至少 10 张，1,979 个尚有缺口，最低还缺 13,597 条正例关系。全量执行配置为 `runs/adopted2261_google_first_20261003/config.json`，**尚未启动**。Notebook 的默认配置及独立只读格已切到该固定范围。旧 head15663 配置和结果保留为历史，不再作为当前范围。

代理声明参考 concepts 已发布的 `network_pool_policy_20261003.json`：主搜索 Google Images 使用 8 个会话、每会话间隔 60 秒、每查询最多一次路由尝试；失败后 Commons 图片搜索使用独立 4 会话池。两池寿命 300 秒、无后台扩池；下载使用 5 个静态出口及已发布的国内域名公司代理例外，冷却等待上限为 30 秒。文档搜索的网页备用源没有被当作图片来源使用。

真实 Dataset 验证的两条查询为“足球场”和“Ginkgo biloba”：Google Images 均实际发出 HTTP 后返回 403/access_denied，Commons 备用查询均成功。抽取两张原图经静态代理下载，均返回 429/rate_limited；没有新增模型请求。共 4 次搜索 HTTP、2 次下载 HTTP。主源失败与备用选择均保留在原生 journal；不能把最终搜索 status=ok 解释为 Google 成功。

证据：工作区 `_demiflow/image_acquisition_validation_20261003/google_first/{search.json,search.sqlite,download.json,download.sqlite,scope.json}`。已验证代理选路和 Google 优先顺序，Google 有效图片结果及新策略完整补图链路仍未通过线上验收；已有 Google 网页浏览器后端不支持图片搜索。

## 首轮正式审核和范围验证（历史）

固定历史 head15663 共 2265 个模型 ready，排除 10 个已知质量待复核审定后为 2255。其已有正例 12215 个关系，281 个概念已达 10 张、1974 个未达标，最低缺口 13539。固定历史由 26 个版本合成，审核来源由 30 个纠正／恢复后的固定版本合成；不能只读最后一个增量表头。

“避暑山庄”小样本按已有正例直接达标，搜索／下载／模型请求均为 0。“足球场”按 8 张已有正例开始，经正式 pipeline 获取 3 张候选、使用既有 `malasci/gpt-6.1-sol` 正式图片审核发出 1 次模型请求，3 张均为 match，累计 11 张，达到目标。重复运行后模型 journal 仍只有 1 条调用。这个端到端语义验收使用前期公司出口；新代理策略的网络实测单独列于上节。

足球场首次试跑因启动脚本缺少本地网关的 MODELHUB_API_KEY 占位环境而产生 model_error，没有模型 journal 调用；失败结果保留。已在采集前加入模型配置及环境校验，修正入口环境后用独立明确的运行完成验证，没有删除失败回执或悄悄换模型。

正式可消费小样本：`runs/ready_backfill_football_verified_20261003/state.json`；完整新增判断为 `concept_positive_images/datasets/reviews_v3__ready_backfill_football_verified_20261003_r01.lance@1`。汇总在 `_demiflow/image_acquisition_validation_20261003/canary_summary.json` 和 `journal_counts_after_replay.json`。

合入这 3 个新增关系后，当时的固定交付范围为 2255 个，正例关系 12218 个；282 个已达标、1973 个未达标，剩余最低缺口 13537。该旧预览为 `ready_head15663_image_supply_plan_20261003`，未启动全量补图。后续已按上节切换到固定采纳的新 run，没有改写这些历史结果。

开发中的首个真实范围预览暴露了惰性 Dataset 回调闭包绑定错误：只读到最后一份增量键，错误交付了空范围。已修复版本绑定并增加“旧 ready＋仅新尾批”回归，重新核对 2255 个范围。旧实验 `ready_head15663_plan_20261003` 保留作失败开发记录，当前 notebook／生产配置不引用它。

## 验证记录

- wiki pipeline 与 document library：22 项通过后恢复导入。
- 图片获取／网页 Dataset／原生检索联测：69 项；新增清理行为后图片／网页 29 项通过。
- 搜索会话池、静态池、代理链、图片获取及配置接线：76 项通过。
- 补图流程正式入口：9 项通过，另通过模型环境前置检查和网络配置接线；覆盖达标停搜、已有正例、最新 hold、空结果／坏图／uncertain、增量尾批、预算停止与中断恢复。
- 存储迁移：3 项通过，包含并行全局文件预算先准入、旧路径持续读取、重复文件和恢复。
- 新 pipeline CLI／布局／登记检查通过；最终入口回归记录 final_entry_checks.log。

以上测试日志、真实请求回执与参数都保存在 `_demiflow/image_acquisition_validation_20261003/`。小样本通过不等于所有来源的长期并发可用性已验证，也不保证每个概念在三轮预算内一定找到十张正例。

## Wikipedia 导入

用户已将此任务的持续监控移交其他会话；本次后续工作没有查询、重启或终止导入，下面保留移交前的状态记录。

用户授权后，attempt06 从固定 summary@10 / documents@1310 的 18,084,352 条成功回执恢复；原 SQLite 索引没有替换或重建。保持索引锁、磁盘与导入进程树内存保护。14:51 北京时间已进入新增登记，累计提交 18,868,736 / 22,269,874，本次净增 784,384 条；最近锁探测无错误，继续后台运行。

最新实时状态为工作区 `_demiflow/wiki_library_import_20261002/live_status.json`，本次交付快照为 `_demiflow/image_acquisition_validation_20261003/wiki_resume_snapshot.json`。全部导入是否完成仍以终态固定结果及观察器验收为准。

## 百度／Brave 与中断恢复（2026-10-03 后续）

用户追加开启百度与 Brave。模块级 `configs/web_policy.json` 和 notebook 同步声明 Google Images HTTP 优先，失败后 Bing Images、Yandex Images、标准 Baidu Images、Brave Images；对应会话池放行百度／Brave 域名，外网原图仍优先静态住宅出口。没有调用 legacy collect 适配器或 Commons 搜索。

Brave 真实页面已采用 Svelte 的标量替换函数，原首个 script／固定 JSON 截取失败。平台新增有限字面量解析，处理多个 script、普通对象和标量变量引用，不执行远端 JavaScript。修复后中英文两次查询都返回 20 个显式原图候选；四概念完整配置验证 3 次 ok、1 次 partial，四个备用源均贡献候选。Brave 不支持后续图片页，明确保留 unsupported_parameters；百度独立复测有过请求收尾错误，源可用性不等于长期无故障。原始记录：`_demiflow/image_acquisition_validation_20261003/{baidu_brave,baidu_brave_fixed,five_sources}/`。

原 target30 run 于北京时间 16:40:59 因备用连续连接失败保护退出，尚未正式下载或审核。没有删除失败账本：journal 有 1,385 个已选结果，搜索表 @189 有 1,370 行。正式入口增加显式 `prior_searches` 固定表输入，核对当前概念／审定／定义及实际请求，保留旧来源和失败，仅为缺失项请求新来源。新搜索先保存 fresh_searches，再与固定旧表合并；不在同步 union 中执行异步搜索。

恢复配置 `adopted2261_target30_five_sources_20261003` 保持 2,261 概念和每概念 30 正例目标，复用上述 1,370 行，剩余 839 项查询。查询总授权不增加：旧 2,209 个预留仍计入，剩余上限为 12,791；下载 200,000、审核 30,000 保持。备用增加平台已有的有限暂停／探测恢复配置，持续故障仍停止。实际提交状态以该 run 的 submission/state 为准。

独立只读监控每 60 秒采样，最多 24 小时，保存在 run 的 `monitor/latest.json`；notebook 第 4 格展示采样时间、阶段、复用和新查询数、下载成功／本地复用及速率、审核调用和异常。监控不擅自重试或重置预算，旧快照须结合采样时间判断。


## 流式下载、静态出口轮换与实际切换（2026-10-03 后续）

用户明确：搜索继续使用会话池；下载只允许公司代理→静态住宅代理，或公司代理→目标网站。出口名单、尝试额度和调度策略在补图业务 `configs/web_policy.json`；连接、动态租约、HTTP 转发、按 host 的健康状态及失败结果由 demiflow 实现，不写入供应商／业务判断。

旧 five_sources 的两次 12→6→4 降并发均为 http_latency，窗口失败率为 0、p95 约 19 秒超过配置 15 秒。旧搜索 HTTP 429 来自 search.brave.com，共 4 次；对应失效会话没有再次使用。此前下载 HTTP 429 为 upload.wikimedia.org 共 68 次（静态 4、下载会话 64），另 live.staticflickr.com、ipfs.io、i3.wp.com、i0.wp.com 各 1 次；这是 HTTP 次数，非唯一 URL 数。

平台／业务修复：

- 搜索提交、链接筛选和下载接成单个有界 Dataset 流。save_lance 逐批附固定版本，flat_map 逐项背压，不等待整轮搜索才下载。初始 1,541 条固定搜索可复用，剩余 668 查询按同一接口获取。
- 代理链支持原样 HTTP GET/HEAD；此前 502 的三个原图 URL 经正式 Dataset 验证均成功，不改协议、不取缩略图。
- 静态池显式配置 health_scope=host，有限健康表不复制连接并发容量。429 时只隔离当前出口对该目标网站；rotate_on_rate_limit 在配置重试额度内立即用另一可用出口。所有出口对该网站不可用才执行该网站的等待／route_unavailable。重定向进入冷却池也保留单 URL 失败，不中止整批。
- 当前 5 个静态住宅出口＋1 个公司出口，均经过公司代理；下载不配置 fallback_session_pool。池并发每出口 16、全局 64、host 2，每 URL 最多 6 次尝试，来源规则仍为 113 域名＋24 URL 规则。

受控切换：stream run 于 17:47:24 因 all_fetch_routes_cooling_down:* 退出；static_downloads 于 17:53:34 启动，随后为用户即时轮换要求于 17:59:22 受控退出；当前为 adopted2261_target30_static_rotation_20261003。旧日志与数据不改写。2,261 个采纳概念、每概念 30 张审核正例、原固定 baseline 和审核模型均保持。

两次流式运行的完整轮次预留不能直接视为实际请求消耗。它们均已退出，持有原 run 锁固定 links@59/@53 后，按“只有已提交 eligible links 才可能进入 fetch_images”取得保守上界 1,210＋968；未开始、失败和未知结果均包含在该上界。另计 3 个诊断图片请求，剩余图片请求行额度为 197,819；旧查询预留全部保留，剩余 10,616。没有按成功数返还预算，也没有清除旧未知记录。固定引用与结算证明在当前 continuation.json，只适用于这两次单次执行。

验证：业务全套 24 项、提交引用／流式背压等 22 项、搜索会话池 33 项；最新静态池／代理链／旧降级兼容 51 项、图片／过滤／文档库 36 项通过。真实出口验证逐条使用平台已配置路线访问 IP 回显服务，6/6 成功且返回 6 个不同出口 IP；报告只保存地址摘要，不打印代理凭据。证据位于 _demiflow/image_acquisition_validation_20261003/{stream_cutover,http_originals_probe,static_exit_verification}。

18:02:10 当前 run 采样：526 个 URL 获取结果，97 个新下载成功、367 个本地复用；实际日志六条路线均已使用，没有下载会话池表。此为阶段性 URL 获取统计，不是去重 SHA 或审核正例统计。实时进展继续以当前 run 的 monitor/latest.json 和 notebook 第 4 格为准；不能据此宣称达到目标或两小时可完成。
