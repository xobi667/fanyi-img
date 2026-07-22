# 宿主运行时适配

先检查当前会话实际提供的原生图片生成能力、输出落盘方式和并发限制。核心规则不得硬编码 provider、模型、密钥、用户名或某台电脑路径。

本 skill 的预检、manifest、Logo、联系表与安装脚本要求 Python 3.10+ 和 Pillow 9.1+；`install_skill.py` 会在安装前硬检查版本。这些本地脚本不得代替原生图片模型完成普通编辑或翻译。

## 通用调用契约

每个新任务都必须登记并保持 `image_model_policy.default=pure_generation`、`reference_images_allowed=false`，且 Logo 例外列表只能包含本地确定性叠加和真实冲突局部重排。调用审计记录必须明确 source/target/reference/attachment/recent-image 及宿主等价参考字段均为空；只有已通过门禁的 `attempt_stage=logo_conflict` 可以记录唯一 `conflict_reference_base`。

- `generate`：原生纯生图，不传参考图。
- `edit`：执行 `pure_generation_edit`。协调者先查看目标图并冻结完整内容清单，图片模型调用只接收文字 prompt，不传 target、reference、attachment、最近会话图片或隐式图片上下文。
- `localization`：执行 `pure_generation_localization`。协调者先查看源图并冻结逐块译文及完整内容锁，图片模型调用只接收文字 prompt，不传源图或任何参考图。
- Logo 例外只在用户明确要求添加 Logo 并确认本次 `logo` 资产时生效；源图已有 Logo 或清单中出现 Logo 不会自动启用例外。
- Logo `direct_overlay`：不调用图片模型，最后使用本次真实 Logo 资产做本地确定性叠加。
- Logo `logo_conflict`：唯一参考编辑例外。只有本次 active Logo 可见像素会遮挡信息模块时，才可把尚未叠加本次 active Logo 的 `conflict_reference_base` 作为唯一参考，局部重排冲突模块；源图原有 Logo 仍保留，不得夹带其他图片。
- 每个 attempt 只调用一次图片工具并最多生成一个完整候选。除最终 Logo 叠加外，候选通过验收后直接作为成品视觉内容；禁止本地蒙版、文字框合成、局部裁贴或像素回填。
- 不同 worker 的 prompt、文字清单、任务状态和输出路径必须隔离，禁止串图、串文案或串会话图片。
- 宿主只能生成近似画幅时，不得默认本地拉伸、补边或裁切。用户明确要求精确规格而宿主无法直接满足时，如实报告或先确认额外转换方案。

## Codex

- 使用当前会话提供的原生图片生成能力，并严格按实际 schema 传参。
- generate/edit/localization 都调用全新生图路径：省略 `referenced_image_paths`、`num_last_images_to_include` 以及任何等价参考输入字段。即使源图已有本地路径或出现在最近对话，也不得附带。
- 源图只供调用前的人工查看、文字盘点、内容清单和最终对照验收使用。
- 只有 `attempt_stage=logo_conflict` 可使用 `conflict_reference_base` 的本地参考路径；宿主必须支持把编辑限制在冻结 ROI/蒙版内，否则直接报告该冲突任务不可执行。调用完成后仍必须运行 Logo 专属冲突验证，再确定性叠加真实 Logo。
- 工具返回结果后立即保存到当前 task 隔离路径并更新 task state。

## OpenClaw / Clawdbot

- 使用当前会话已暴露且配置完成的原生图片工具；工具名和参数以实际 schema 为准。
- generate/edit/localization 选择“新建/生成”语义，不传当前 target、source、附件、引用消息图片或等价参考字段。只返回远端附件时，用宿主合法附件保存能力落入预分配路径；无法落盘就记录基础设施失败。
- 只有 Logo 冲突任务可选择明确支持的局部参考编辑语义，并只传尚未叠加本次 active Logo 的 `conflict_reference_base`；不支持局部 ROI 锁时停止该任务。
- 不指定固定 Gemini、Nano Banana、OpenAI API 或其他 provider，不索取临时 API key。

## 其他 AgentSkills 宿主

- 只有会话确实存在原生纯生图能力时执行 generate/edit/localization；将冻结计划转成文字 prompt，不猜测不存在的参数。
- 宿主只支持参考编辑、不支持无参考纯生图时，报告能力限制；不得把普通 edit/localization 静默改成参考编辑，也不得用本地脚本冒充完成。
- 宿主无法隔离最近图片上下文时使用新的独立图片会话；仍无法保证无参考输入时停止该 task。
- Logo 冲突参考编辑和最终本地 Logo 叠加继续遵守 [logo.md](logo.md)。

## 并发契约

1. 工具/schema 明确禁止并发、只有一个图片槽位或共享会话状态不可隔离：直接 `workers=1`。
2. 工具允许并发：使用 `min(4, 可用槽位, task 数, 宿主上限)`。
3. 并发能力未知：默认最多 4；基础设施错误按 [quality.md](quality.md) 独立计数并在初次调用后最多重试 3 次（等待 2/5/10 秒）。出现两个 worker 同类限流、连接、附件或会话冲突时，取消尚未执行的并行退避重试并停止新派发。
4. 单路探针必须选一个原始 pending task，沿用该 task 的冻结 prompt 与隔离输出，并按实际结果计入它的质量或基础设施预算；不得发起无 task 归属的空探针。探针候选验收成功后把 manifest 标记为降级、设置 `workers=1` 并只补 pending；失败则停止。

## 交付

宿主支持附件时按原生方式交付；不支持时报告本地绝对路径。无论宿主如何交付，原图只读、任务根目录最终成品和 `.xobi/` 元数据分离规则不变。除 Logo 最后一步确定性叠加外，默认不对已验收候选做视觉后处理。
