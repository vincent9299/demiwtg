# Edit 业务算子

- materials.py：读取 preparation 已发布材料的业务记录，固定图片版本，逐图分批访问。
- prompting.py：设计/审核输入、材料选择校验、图对输入绑定和审核判定。
- synthesis.py：调用已加载的官方 QwenImage21Pipeline，返回生成字节与耗时。
- contracts.py：题目/训练表 Arrow schema 和行转换。

读写、模型加载和 Dataset 编排直接在 edit_train_pipeline.py，不在这里添加通用读写或调度封装。
