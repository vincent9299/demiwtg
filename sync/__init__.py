"""demiwtg 冷备同步模块（业务策略层；机制见 demiflow.collect.relay/cosio/pan123）。

链路：SG 桶（源，中继只读、永不回写）→ sg1/sg2 分片中继 → GZ 桶
``pan123-relay/`` 前缀（队列，消费后删）→ cn1 → 123pan ``demiwtg-data/`` 镜像。

本包只放 demiwtg 的**策略**：闭集子树、毒闸门、分片约定、镜像布局、
预算阈值；一切机制（账本/背压/封卷/看门狗/客户端）来自 demiflow。
"""
