# ImageRAG 固定原版对照与 messages 执行

固定官方提交 `16c9502a09b5a049f7c30f39b7f48998fd1e2526`：
[utils.py](https://github.com/rotem-shalev/ImageRAG/blob/16c9502a09b5a049f7c30f39b7f48998fd1e2526/utils.py)、
[OmniGen 入口](https://github.com/rotem-shalev/ImageRAG/blob/16c9502a09b5a049f7c30f39b7f48998fd1e2526/imageRAG_OmniGen.py)、
[retrieval.py](https://github.com/rotem-shalev/ImageRAG/blob/16c9502a09b5a049f7c30f39b7f48998fd1e2526/retrieval.py)。
本目录 `.py.txt` 是原始文件快照，业务代码不执行这些文件。

| 文件 | SHA256 |
| --- | --- |
| utils.py.txt | 42127923a96276330d8355fad1388ee6706b7331a9da5d59156a83aa01900595 |
| imageRAG_OmniGen.py.txt | 1763728bf1b95ae10c56a30b39fce1c0aa513c8d9e7a0a13f8bd352b638acbe4 |
| retrieval.py.txt | c57080b2d39c85a9a869798975677d0035f8e79d32c7135a9b73a4afbb0d211e |

## 复现范围与原版契约

选定 `omnigen_first`、无输入参考图、`only_rephrase=False`。
英文正文见 [imagerag_original.yaml](../imagerag_original.yaml)，同时包含原文及平台 call_config 执行声明。旧 `imagerag.yaml` 属于 JSON 改写协议，不能声称原文一致。

1. 只用原始生图题面和初图判断 yes/no；不读考点、评测标准、概念标签或图库 caption。
   `yes` 子串（忽略大小写）命中即保留初图。
2. 以先前 user/assistant 对话为上下文识别缺失内容和风格，输出逐行文本。
   入口不传 `k_concepts`，因此没有“最多 3 个概念”的追加正文。
3. 每个概念生成 1 条独立图像描述，仍使用对话历史，输出逐行纯文本。
4. 每条生成的描述编码成查询文本向量，与图库图片向量做相似度检索，每条取 1 张；
   检索完才按描述顺序取前 3 张。默认 CLIP 路径不使用图库 caption embedding。
5. 按原版 caption + 图片标记组合生成 prompt；初图不作为最终生成的参考图。

调用为 GPT-4o、temperature=0、response_format=text，没有 system、额外编号或 JSON 包装，
原图文件字节直接 base64，不经过当前业务的 JPEG 缩放编码器。
三个请求分别是 1、3、5 条 user/assistant 消息。
概念响应包含大小写敏感的 `unable` 或 `can't` 时最多请求 3 次；三次仍拒绝则以原题检索。
源码中的 temp 递增没有传入请求，实际一直为 0。
源码会原地向历史首条 user 追加图片：正常概念调用有 1 份初图，caption 调用有 2 份；
拒绝重试也会继续累积。严格复现必须明确保留此实际行为，不能静默修正后声称相同。
逐行清洗同样使用官方 `convert_res_to_captions` 的规则。

## 执行边界（2026-10-07）

用户已明确授权独立平台改造。demiflow新增显式input_mode=messages，每次接收完整消息并执行一次completion，
不注入system、模板或自动schema纠正轮次；默认模板路径保持。业务算子负责历史组装和条件推进，
主pipeline显式声明decision、最多三次concepts和captions节点，不自建HTTP客户端。
新Qwen/BAGEL预设接通上述原文协议；本次不提交正式评测。

初图按原始字节发送；持久化JSON包络保存raw_text和调用引用，不改变模型看到的原文或强制其回复JSON。
官方空白行/单字符编号的解析异常在本实现记为技术失败，不默默更改清洗语义。
每题最多32查询（可配置1..64）、上下文30000字符、输出4096tokens、初图8MiB是显式运行边界；
超限报错，不在原prompt追加上限、不静默截断。原版未设置这些有限预算。

当前预设GPT-6.1-sol / WeMM / Qwen或BAGEL、图库和题集有别于GPT-4o / CLIP / OmniGen实验；
原文一致不等于论文完整数值复现。最终生成文本保留OmniGen图片标记，Qwen/BAGEL原生image数组
按相同顺序绑定图像；这些文字不具备OmniGen专用token语义，真实模型效果尚未运行验证。
