# focus1000 V2 生图判分汇总

已判 **59** 题。总体 generic 均分 **86.74**；对齐 **76.82**、质量 **88.83**、美感 **94.58**。所有分数均按 φ（0→0、1→60、2→100，N/A 剔除）计算，generic 为三条维度线等权均值。

## 0 分轴 Top

- `alignment.spatial_relation`：13 题（22.0%）
- `alignment.quantity_scale`：12 题（20.3%）
- `alignment.text_symbol`：10 题（17.0%）
- `alignment.form_structure`：8 题（13.6%）
- `alignment.state_context`：5 题（8.5%）
- `alignment.subject_presence`：5 题（8.5%）
- `alignment.action_interaction`：4 题（6.8%）
- `alignment.color_material`：3 题（5.1%）
- `quality.physical_logic`：1 题（1.7%）

## 代表性低分题

- `万年青#1`：generic 62.86；`alignment.quantity_scale`=0：左侧架上可点数出远多于五组的杠铃片，明显违反五组不同重量的明确数量。
- `姬松茸#1`：generic 67.5；`alignment.form_structure`=0：菌盖下的菌褶已经大面积暴露且内菌幕明显撕裂下垂，违反菌褶仍被完整膜质内菌幕包裹的明确构型。
- `鸡腿菇#1`：generic 71.32；`alignment.subject_presence`=0：幼嫩、半开和自溶鸡腿菇及藤篮清晰在场，但完整可见的草地中未见题面要求的独立竹筛。
- `鳞毛蕨#1`：generic 73.65；`alignment.quantity_scale`=0：画面叶尖和空中可数出明显超过三颗水滴，违反题面锁定的三颗滴落数量。
- `卫生统计学#0`：generic 74.33；`alignment.text_symbol`=0：三张图标题可读，但多个坐标轴刻度出现“4、3、5、9”等非单调顺序和乱码式数值，明显不符合学术出版规范。
- `番茄红#1`：generic 76.99；`alignment.color_material`=0：最高饱和红主要集中在种子腔胶液，胎座与中果皮反而呈淡粉，明显违背题面指定的红素分布梯度。
- `软银 NAO#1`：generic 78.67；`alignment.spatial_relation`=0：主机器人仅一只手伸到水盘和薄荷之间，另一手未稳住水盘，未落实题面的双手分工关系。
- `火把节#0`：generic 78.89；`alignment.form_structure`=0：长者头部是包裹式黑头巾而非题面明确要求的凉山彝族英雄结，服饰关键构型缺失。
- `战国时期#1`：generic 80.67；`alignment.form_structure`=0：背景仓房为完整地上草屋，地面与墙脚均未显示题面要求的半地穴式仓储结构。
- `软银 NAO#0`：generic 80.67；无 0 分轴，因多项 Pass 档进入低分样本
- `精灵鼠小弟（Stuart Little）#0`：generic 80.83；`alignment.form_structure`=0：完整可见的船体是带厚木板船帮和座板的普通小木船，既非胡桃壳船也没有题面指定的小帆船结构。
- `马尾辫#1`：generic 80.83；`alignment.spatial_relation`=0：跑道在人物与发束下方完整可见，细长发辫状阴影却落在人物右前方且轮廓、方向均未对应向左后上方展开的实际马尾。

详细逐项 reason 与打分在 `gen_scores.jsonl` 同一行；level/combo_type 分桶、各轴均值、N/A 率及完整分布见 `gen_scores.report.json`。
