---
name: xobi-img
description: 跨 Codex、OpenClaw 与 AgentSkills 兼容宿主的通用位图生产技能，默认使用当前宿主的原生图片模型以不传参考图的纯生图方式完成生成、翻译、文字替换、换背景、增删对象、换色、合成、修图、透明背景、比例转换、多变体、ZIP 和最多四路批处理；普通编辑只改用户点名项，翻译只替换已有文字。唯一例外是用户明确要求添加 Logo：无冲突时本地确定性叠加真实 Logo，有冲突时允许 Logo 专属局部参考重排后再叠加。用户只给图片、文件夹或压缩包但没说改什么时先询问；所有图片任务的输出比例未明确时先询问；翻译缺少目标语言时先询问。信息完整后直接执行。
---

# xobi-img

把本 skill 作为通用位图生产入口。SVG、代码原生图形、流程图、数据图表和视频使用相应专用能力。

## 开工门禁

任何生成、编辑、预检建目录或正式输出之前，确认本次任务缺一不可的信息：

- `generate`：生成目标和输出比例。用途仅在无法合理推断且会改变结果时询问。
- `edit`：目标图、明确修改项、输出比例或“保持原比例”。
- `localization`：目标图、目标语言、输出比例或“保持原比例”。默认修改项只有“替换原图已有文字”。
- 多图：确认每张是 `target`、`logo`、`asset`、`style_reference` 还是 `layout_reference`，并确认整批操作是否相同。

门禁前允许只读列目录、读取尺寸/格式和查看输入，以识别素材角色；不得调用图片工具、创建任务目录或产生正式输出。

- 缺什么只问什么，已经明确的信息不得重复询问。
- 操作和比例都缺时问：`亲亲，请问这些图片需要怎么处理，以及需要什么输出比例？`
- 只缺操作时只问怎么处理；只缺比例时只问比例；翻译只缺语言时只问目标语言。
- `1:1`、`4:5`、`3:4`、`9:16`、`16:9`、具体宽高和“保持原比例”都算已明确。
- 任何比例只要用户明确要求“精确像素”，就必须已有具体 `宽×高`；缺少时只追问尺寸。用户只要求比例而未要求精确像素时不补问宽高。把用户确认的比例或尺寸直接写入纯生图要求；宿主只能生成近似画幅且用户不接受时如实报告或询问，不得默认用本地拉伸、补边或裁切把近似画幅伪装成成品。

## 模式与改动边界

1. `generate`：从文字创建新图。未要求文字时禁止生成文字、Logo、水印或伪字。
2. `edit`：默认执行 `pure_generation_edit`。先查看目标图并建立内容清单，再调用原生图片模型纯生图重建；不把目标图作为工具参考输入。只修改用户点名内容，其余内容、数量、商品、背景和版式全部锁定。
3. `localization`：默认执行 `pure_generation_localization`。只翻译 plan 中确认的源图已有文字，不新增、删除、重排或美化其他内容；不把源图作为工具参考输入，也不做文字框本地合成。完整规则见 [references/localization.md](references/localization.md)。
4. `batch`：把独立的 generate/edit/localization 任务分给不同 worker；不是四个人重复处理同一张图。

用户已给同语言精确替换文案（如 `SALE → SOLD OUT`）时路由为 `edit/text_replacement`，不强制追问目标语言；只有需要跨语言翻译时才使用 localization 门禁。

用户说“翻译”时是死命令：执行方式虽然是整张纯生图重建，但唯一授权变化仍只有“把已有文字替换为准确译文”。商品、照片、人物、图标、Logo、边框、色块、背景、阴影、纹理、顺序、数量、位置、间距、构图和版式一律不许改；无字区域继续无字。译文只能在原文字模块内做必要换行、字号和字距适配，不能移动模块、扩建底板、添加角标、卖点、装饰或伪字。任何未点名变化都判质量失败并从同一纯生图模式重试。

用户若同时明确指定新比例，只有 `minimal_canvas_adaptation`、`proportional_subject_scaling` 和 `necessary_text_reflow` 属于额外的最小几何适配授权；它们不是“翻译”自动附带的权限，也不允许顺便改内容、商品、背景风格或信息层级。

普通 edit 若同时明确指定新比例，也必须把 `minimal_canvas_adaptation`、`proportional_subject_scaling` 和确有必要的布局适配逐项写进本次 `allowed_changes`；未写入就继续锁定原位置和版式。新比例不授权拉伸商品、自由改版或扩大其他修改范围。

`pure_generation_localization` 是默认模式，不需要先失败、不需要额外授权，也不存在“参考编辑失败三次后才允许纯生图”的切换逻辑。每张图最多 3 次质量尝试；3 次都不合格就报告失败，不得交付添油加醋的结果。

## 宿主图片能力

- 使用当前会话实际存在且已配置的原生图片生成/编辑能力；不写死工具名、模型、provider、密钥或某台电脑路径。
- 不要求用户临时提供 API key，不安装或切换到未授权图片服务。
- 调用前读取 [references/runtimes.md](references/runtimes.md)，按实际 schema 传参。
- generate、edit 和 localization 的原生图片模型调用默认都不传参考图、最近会话图片或参考图参数；edit/localization 先由协调者查看源图并把必要内容完整写入当前 task 的纯生图 prompt。只有用户明确要求添加 Logo 并确认本次 `logo` 资产时，才可启用 Logo 专属例外；源图本来含有 Logo、盘点清单登记了 Logo 或用户只要求翻译/普通编辑，都不构成例外。唯一可使用参考编辑的是该 Logo 添加任务中的冲突底图重排，且只在 [references/logo.md](references/logo.md) 规定的冲突任务内使用。
- 每个 attempt 只调用一次图片工具，至多产生当前任务的一个候选；候选通过验收后直接作为成品视觉内容。除已明确启用的最终真实 Logo 确定性叠加外，不得再用本地蒙版、裁贴或局部合成修改。基础设施失败可以没有候选。质量或基础设施失败可按重试策略新建 attempt；不同任务不得共享会话图片、prompt、输出路径或图片上下文。

## 原图、输出与比例保护

- 原图只读，禁止覆盖。成品使用 manifest 预分配的新路径。
- 用户未指定输出格式时，输入型任务默认 PNG 并保持源图是否含透明像素；用户明确要求 JPG/WebP/BMP/TIFF、保留源格式或透明/不透明时，写入 manifest 的 `expected_format` 与 `expected_alpha` 并硬验收，不得只改扩展名。
- 用户指定输出目录时严格遵守；否则在输入旁创建 `xobi-img-output/<任务名>-<时间>/`。纯文字生图在当前工作目录使用同结构。
- 任务根目录只放最终成品；`.xobi/source/`、`.xobi/work/`、`.xobi/manifest.json`、`.xobi/report.md` 保存源路径、中间产物和记录。ZIP 只包含任务根目录最终成品。
- 比例转换锁定商品自然纵横比、轮廓、厚薄、部件比例、材质和透视；只能等比缩放、自然扩展背景或重新排布，禁止拉伸、压扁或局部变形。
- 长文字先确定忠实且不增删事实的目标文案，再锁定逐字内容并采用自然换行、调整文字框、适度缩小字号；用户给出的精确目标文案禁止精简或改写。禁止压缩字宽、拉长字高、粘连、重叠、裁切、溢出或扭曲。
- 未授权修改的品牌、Logo、型号、数量、尺寸、单位和原有信息必须保持。

## Logo 硬规则

- 本节只在用户明确要求添加 Logo 时启用。源图已有 Logo 不等于用户要求添加 Logo，也不能据此放宽普通 edit/localization 的无参考纯生图规则。
- 添加 Logo 的开工必填项包括本次 Logo 资产及其 `logo` 角色。只有用户明确说“使用默认 Logo”时才能启用技能内置模板；不得因用户没给 Logo 就静默套用旧 Logo。
- 用户只要求添加 Logo 时，不先执行 `pure_generation_edit`：源图或用户明确要求的尺寸/格式转换结果就是待叠加基底。只有同一请求还点名了其他视觉修改时，才先完成并验收相应的无参考纯生图阶段。无论哪种组合，第一阶段都禁止让模型生成、临摹或加入本次 active Logo。
- Logo 必须使用本次真实素材做最后一步确定性叠加，禁止 AI 重绘、仿制、改字、改色或拉伸。
- 批量前看完全部目标图。只有 Logo 的 `visible_bbox` 真正碰到文字、小图、角标、徽章、图标、脸部等信息模块时才重生底图；只进入 `safe_zone` 缓冲圈不算遮挡，普通背景或无信息商品边缘也不要求重生。
- 需要重排时进入 Logo 专属 `logo_conflict` 例外，可把尚未叠加本次 active Logo 的基底作为唯一参考进行局部重排；源图中原有 Logo 仍属于必须保留的内容。只移动完整冲突信息模块，并用 `safe_zone` 控制刚好舒适的间距。除原/目标模块 bbox 的固定 2px 羽化边界外，移动前后其他 RGBA 像素必须逐像素相同，禁止顺手删除或改动远处商品、背景和其他模块，也禁止整条空白带、顶栏、底板或过度留白。
- 只有同系列中存在 `regenerate_for_conflict` 时才先做一张 family pilot，验收并冻结重排布局后其他成员并行；全为 `direct_overlay` 的系列完成逐图 dry-run/plan 后可直接并行确定性叠加。最终做 source/conflict_reference_base/prepared_base/final 分阶段对照检查。
- 同一批必须使用同一 Logo；不同 Logo 拆成独立批次。所有画幅按短边 4000 基准与 `1036×309` 参考框保持统一相对视觉大小和左上角位置。

执行 Logo 任务前必须读取 [references/logo.md](references/logo.md)，并以该文件作为唯一详细规则源。

## 批量、四路与降级

- 批量先运行 `scripts/preflight_images.py`。纯文字 generate 使用 `--mode generate --variants <数量>` 且不传 `--input`，需要随后添加 Logo 时同时传 `--logo` 或用户明确选择的 `--use-default-logo`；输入型任务用相同 Logo 参数、重复 `--exclude` 或 `--roles-file` 排除 Logo/参考素材，PSD/PSB 明确记录为不支持并跳过，不增加强制依赖。
- 默认 `MAX_WORKERS = min(4, 宿主可用槽位, 待处理数, 宿主并发上限)`。宿主明确不支持并行时直接使用 1；未知时默认最多 4。
- 主协调者兼任 worker-1，最多另启三个 worker。一个 task 只归属一个 worker、只对应一个输出；每个 worker 内逐张串行。
- worker 写独立 `.xobi/work/task-state/<task_id>.json`；共享 manifest 只通过 `scripts/update_manifest.py` 的锁定合并更新。
- Localization 的每次实际图片调用都连续登记唯一 attempt，返回候选即占用当前阶段一次质量 attempt；只有未通过验收才触发重试，初次结果后最多 2 次针对性重试，共 3 个，只重试当前失败图。翻译的计划模式固定为 `pure_generation_localization`，调用登记 `attempt_stage=pure_generation`。禁止 0 次成功、重复、跳号、漏记或三次失败后的第 4 次成功。普通 edit 的纯生图约束由 manifest `image_model_policy` 执行，不使用 localization 专属 `--attempt-stage`。没有可用候选的限流、连接、附件或落盘错误计基础设施 attempt；初次调用后最多重试 3 次，依次按 2/5/10 秒退避，共 4 个。翻译候选后还需 Logo 冲突参考编辑时，先登记 pending 的已接受翻译候选，再为 `logo_conflict` 使用下一 attempt；确定性 Logo 叠加不增加图片 attempt。
- 普通 generate/edit 的第一阶段不使用 localization 的 `attempt_stage`；但任何模式一旦真实进入 Logo 冲突参考重排，都必须在已冻结 Logo plan、geometry、`conflict_reference_base` 和已接受前序基底之后，单独登记 `attempt_stage=logo_conflict`。无 Logo、`direct_overlay`、无真实冲突或首个图片 attempt 一律拒绝该阶段。
- 两个 worker 出现同类基础设施错误时取消尚未执行的并行退避重试、暂停新任务，并按 [references/quality.md](references/quality.md) 选一个原始 pending task 做单路探针。探针必须沿用该 task 的冻结 prompt、无参考输入策略和输出隔离，且按实际结果计入该 task；只有 `logo_conflict` 探针才沿用其唯一底图参考。不得创建无归属调用。探针候选验收成功后降为 1 路补 pending。
- 成功任务默认不重跑。仅当用户明确要求重做，或共同的 family/style lock 被证明错误时，才重做明确受影响范围。
- `batch_style_lock` 只在用户明确要求整批统一时启用；自动识别的 `layout_family_lock` 只约束同系列，两者不是一回事。
- 最后运行 `scripts/verify_manifest.py`，确认任务、文件、尺寸、哈希、遗漏和重复全部一致。

## 资源路由

- 开工与目录流程：[references/workflow.md](references/workflow.md)
- 图片提示词：[references/prompts.md](references/prompts.md)
- 宿主适配：[references/runtimes.md](references/runtimes.md)
- 质量、重试与交付：[references/quality.md](references/quality.md)
- 翻译唯一规则：[references/localization.md](references/localization.md)
- Logo 唯一规则：[references/logo.md](references/logo.md)
- 术语：[references/glossary.md](references/glossary.md)
- `scripts/preflight_images.py`：无输入 generate 变体预分配、输入角色过滤、唯一命名、worker 分配和 manifest v4；v1-v3 旧 manifest 只读，继续执行前必须迁移到当前纯生图策略。
- `scripts/update_manifest.py`：独立 task state、锁定合并和成功校验。
- `scripts/verify_manifest.py`：最终完整性、重复和哈希复核。
- `scripts/apply_logo.py`：Logo 几何 dry-run 与最终确定性叠加。
- `scripts/logo_relocation.py`：重排 Logo 冲突模块时，复算移动前后 ROI 指纹、原位清除、多模块一一对应，以及原/目标 ROI 外逐像素不变证据。
- `scripts/normalize_logo.py`：保守清理透明边或经视觉确认的纯色外围边，并把原始/规范化 Logo 的路径、哈希与裁切谱系原子登记到 manifest。
- `scripts/create_contact_sheet.py`：普通总览；翻译使用 source/pure_generation_candidate/final，Logo 使用 source/conflict_reference_base/prepared_base/final 分阶段对照。
- `scripts/compose_localization.py`：仅用于离线读取、诊断或导出旧版 `text_only_reference_edit` 任务；旧 manifest 不得再发起新的 reference-edit/pure-rebuild 图片调用，新任务也禁止用它把纯生图候选局部贴回原图。需要继续旧任务时先迁移到当前无参考纯生图策略。
- `scripts/resample_image.py`：仅在用户明确要求额外文件尺寸/格式转换时使用，不是 generate/edit/localization 的默认视觉生产步骤，禁止用它拉伸、补边或裁切主体。
- `scripts/optimize_images.py`：仅按用户明确规格做离线尺寸、格式和体积优化。
- `scripts/install_skill.py`：安装到 Codex/OpenClaw，支持跨电脑路径。

## 执行顺序

1. 只读查看输入并通过开工门禁。
2. 读取当前模式所需规则文件；批量创建 manifest 并锁定输入角色。
3. 建立逐图内容锁；翻译在 attempts=0 时用独立 pending 更新登记并冻结 `pure_generation_localization` plan，Logo 写 logo plan/family pilot。
4. 按 task 分工执行。默认原生图片调用不传参考图，每个 attempt 只产一个完整纯生图候选并立即记录；只有 Logo 冲突底图可进入专属参考编辑例外。
5. 逐图查看 source/candidate/final；普通编辑核对只改点名项，翻译核对只换已有文字，失败项按精确原因重试。
6. 生成联系表并运行 manifest 验证；核对成功、跳过、失败、遗漏和重复。
7. 只交付任务根目录最终成品或不含 `.xobi/` 的 ZIP，并报告绝对路径。
