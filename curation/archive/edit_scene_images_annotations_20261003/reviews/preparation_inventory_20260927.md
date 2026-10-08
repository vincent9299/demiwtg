# Preparation 图片目录核对 · 2026-09-27

本次只读核对固定版本：`preparation/datasets/images.lance@5`、`collect/datasets/images.lance@4/@5`。没有改写历史表，也没有调用模型。

## SHA 能对上，但 preparation 不是全量采集目录

| 范围 | 去重 SHA 数 |
| --- | ---: |
| collect@4（preparation 交付引用绑定的版本） | 2,164,592 |
| collect@5（当前采集表） | 2,164,671 |
| preparation@5 | 900,883 |
| preparation SHA 在 collect@4/@5 中缺失 | 0 |
| collect@5 中未进入 preparation 的图片 | 1,263,788 |

三个版本的行数均等于去重 SHA 数。collect@5 中可用且有字节的图片为 2,127,760 张，其余记录不都能读到字节；不能把全部目录条数说成可用原图数。

原因已在历史迁移代码确认：[separate_raw_curated.py](../../../../../tools/lake_migration/separate_raw_curated.py) 的 `projected()` 只保留 descriptions / concept_matches / concept_assessments 至少一种非空的图片；没有这些结果的图片未进入 preparation。概念关系也由标注/审核投影产生，没有完整复制采集表的 concepts。因此两张表是按 SHA 关联的原图表与结果子集，不是镜像；差额不表示图片丢失。

preparation 当前 858,732 张图有描述，44,903 张图有审核记录，两者交集 2,752 张；并集恰为 900,883 张。本轮扫描中没有两者都为空的行。

## 197 是发布覆盖，不是目录规模

| preparation@5 范围 | 数量 |
| --- | ---: |
| concepts 去重候选概念 | 293,998 |
| concept_assessments 去重审核概念 | 200 |
| published_concepts 去重已发布概念 | 197 |
| 至少一个概念已发布的图片 | 8,043 |

餐桌、茶几、客厅、庭院、书桌分别有 4 / 3 / 7 / 6 / 7 张候选图，当前已发布图均为 0。建候选原图池使用 `published_only=False`；True 用于实验严格的已发布/keep 范围，两者最终输出都仍需审核。

## 尺寸保存位置与消费方式

preparation@5 没有独立 width/height 列；部分已处理图片的 `concept_assessments[].observation_json` 保存了同 SHA 的 verified_bytes、resolution.stored_width/stored_height。不能根据没有列就推断从未计算尺寸，也不能根据 197 个概念的发布结果推断全库尺寸齐全。

新增 preparation 行投影 `image_technical_fields` 提取这些已有技术结果；原图 pipeline 只消费投影后的字段，尺寸仍缺失才按交付 source_refs 读取 Blob 补齐，然后过滤严格短边 > 1024、按概念及全局限量、视觉标注。尺寸来源 preparation/blob 和结果一起写本次 image_checks，不修改原来源表。

source_refs 当前绑定 `raw/images.lance@4`；已有平台位置映射将该逻辑地址解析到采集表的物理位置，版本仍为 4。已实读验证可用，不替换成采集表 latest，也不新建映射或回退路径。

## 当前实验边界

原图 notebook 已切换 preparation@5，因此候选范围是这 90 万张，而非全部 216 万张。要覆盖全部采集图片，下一步应在 preparation 生产侧补全候选目录：按 SHA 保留全量图片引用、采集概念关系和已有技术字段，再关联已存在的标注/审核结果；未标注图片保留未标注状态，尺寸按实际候选补读。补目录不需要先对缺少的 126 万张调用模型。

全量目录扩充与补尺寸代码已实现，手工入口是 preparation/image_catalog_debug.ipynb；按 SHA 只更新基础列，已有标注/审核保持原值。本次未执行生产全量任务；运行后需要把输出新版本填入 Edit 的 image_source，不能继续用 @5 却宣称目录已经完整。

本次有界只读验证：3 张已审核样例均复用 preparation 历史尺寸；餐桌/茶几/客厅/庭院/书桌的 27 张候选均按交付引用成功补读，11 张短边 > 1024、16 张尺寸过滤，没有模型调用或来源表写入。
