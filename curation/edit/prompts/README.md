# Edit prompts

- `tasks.yaml` → `design_edit.template`：学习方向×编辑类型，联合选择已有图角色、合成指令、训练指令、参考和判据。
- `tasks.yaml` → `review_edit.template`：对真实原图/目标逐项审核，全部通过才交付，不改写题目迁就结果。
- 同一 YAML 包含完整正文、本地 VLM 配置和结构化响应协议，供标准 `map_prompt_async` 调用；每行只绑定材料与图片。
