---
name: xobi-img
description: 跨 Codex、OpenClaw 与 AgentSkills 兼容宿主的通用位图生产技能，使用当前宿主已配置的原生图片能力完成从零生图、严格参考图编辑、商品图翻译与文字替换、换背景、增删对象、换色、合成、Logo、修图、透明背景、比例转换、多变体、ZIP 和最多四路批量处理。用户只给图片、文件夹或压缩包但没说改什么时先询问；所有图片任务的输出比例未明确时先询问；翻译缺少目标语言时先询问。信息完整后直接执行。
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
- 任何比例只要用户明确要求“精确像素”，就必须已有具体 `宽×高`；缺少时只追问尺寸。用户只要求比例而未要求精确像素时不补问宽高。宿主最接近的原生画幅只能作为 `base`；`final` 必须确定性适配到用户已确认的准确宽高比并通过 manifest 验收；无法无损适配时如实报告或询问，不得把近似画幅当成品。

## 模式与改动边界

1. `generate`：从文字创建新图。未要求文字时禁止生成文字、Logo、水印或伪字。
2. `edit`：只修改用户点名内容；其余区域全部进入保持不变清单。
3. `localization`：只翻译 plan 中确认可编辑的源图文字，不新增、删除、重排或美化其他内容。当前硬像素锁只接受保持源宽高比；目标比例与源图不同时，在有结构化、可重算坐标映射之前必须停止并说明限制，不得借翻译重排画面。完整规则见 [references/localization.md](references/localization.md)。
4. `batch`：把独立的 generate/edit/localization 任务分给不同 worker；不是四个人重复处理同一张图。

用户已给同语言精确替换文案（如 `SALE → SOLD OUT`）时路由为 `edit/text_replacement`，不强制追问目标语言；只有需要跨语言翻译时才使用 localization 门禁。

用户说“翻译”时是死命令：商品、照片、图标、Logo、边框、色块、背景、顺序、数量和装饰一律不许改；所有非文字位置、间距、构图和画布也一律不许改。只有 plan 中逐块登记的目标文字可做必要换行、字号和 `target_bbox` 微调。`non_text_inventory` 必须把纯背景承载面与每个有界非文字元素分别登记；任何与文字框相交的小图、Logo、徽章、边框、商品或其他元素都必须由同 ID 的 `protected_non_text_regions` 完整覆盖交集并从可编辑掩膜扣除，漏报、缩小保护区或让 `target_bbox` 侵入元素均直接拒绝。相同宽高比的新精确尺寸只授权验收后整图等比确定性重采样，不授权重设计；新宽高比当前必须 fail closed，先让用户选择保持原比例，或另开明确授权的比例适配任务。默认把当前目标图作为参考执行 `text_only_reference_edit`。参考编辑质量失败后也不得自行切换纯生图；必须先询问并取得用户明确许可。

纯重建许可只对明确绑定的 `manifest_id + task_id + source_sha256 + 第三次失败记录` 生效，不能从旧对话、旧任务、同批其他图片或“继续/再试试/想办法”沿用。必须先记录当前图 3 次 `reference_edit` 质量失败，再记录用户针对该图的明确许可；许可一张不等于许可整批。

## 宿主图片能力

- 使用当前会话实际存在且已配置的原生图片生成/编辑能力；不写死工具名、模型、provider、密钥或某台电脑路径。
- 不要求用户临时提供 API key，不安装或切换到未授权图片服务。
- 调用前读取 [references/runtimes.md](references/runtimes.md)，按实际 schema 传参。
- 全新生图不传参考图；普通编辑传当前目标图；翻译默认也传当前目标图，并只允许文字区域变化。
- 每个 attempt 只调用一次图片工具，至多产生当前任务的一个候选；基础设施失败可以没有候选。质量或基础设施失败可按重试策略新建 attempt；不同任务不得共享会话图片、prompt、输出路径或参考图上下文。

## 原图、输出与比例保护

- 原图只读，禁止覆盖。成品使用 manifest 预分配的新路径。
- 用户未指定输出格式时，输入型任务默认 PNG 并保持源图是否含透明像素；用户明确要求 JPG/WebP/BMP/TIFF、保留源格式或透明/不透明时，写入 manifest 的 `expected_format` 与 `expected_alpha` 并硬验收，不得只改扩展名。
- 用户指定输出目录时严格遵守；否则在输入旁创建 `xobi-img-output/<任务名>-<时间>/`。纯文字生图在当前工作目录使用同结构。
- 任务根目录只放最终成品；`.xobi/source/`、`.xobi/work/`、`.xobi/manifest.json`、`.xobi/report.md` 保存源路径、中间产物和记录。ZIP 只包含任务根目录最终成品。
- 比例转换锁定商品自然纵横比、轮廓、厚薄、部件比例、材质和透视；只能等比缩放、自然扩展背景或重新排布，禁止拉伸、压扁或局部变形。
- 长文字先确定忠实且不增删事实的目标文案，再锁定逐字内容并采用自然换行、调整文字框、适度缩小字号；用户给出的精确目标文案禁止精简或改写。禁止压缩字宽、拉长字高、粘连、重叠、裁切、溢出或扭曲。
- 未授权修改的品牌、Logo、型号、数量、尺寸、单位和原有信息必须保持。

## Logo 硬规则

- 添加 Logo 的开工必填项包括本次 Logo 资产及其 `logo` 角色。只有用户明确说“使用默认 Logo”时才能启用技能内置模板；不得因用户没给 Logo 就静默套用旧 Logo。
- Logo 必须使用本次真实素材做最后一步确定性叠加，禁止 AI 重绘、仿制、改字、改色或拉伸。
- 批量前看完全部目标图。只有 Logo 的 `visible_bbox` 真正碰到文字、小图、角标、徽章、图标、脸部等信息模块时才重生底图；只进入 `safe_zone` 缓冲圈不算遮挡，普通背景或无信息商品边缘也不要求重生。
- 需要重排时只移动完整冲突信息模块，并用 `safe_zone` 控制刚好舒适的间距；除原/目标模块 bbox 的固定 2px 羽化边界外，移动前后其他 RGBA 像素必须逐像素相同，禁止顺手删除或改动远处商品、背景和其他模块，也禁止整条空白带、顶栏、底板或过度留白。
- 只有同系列中存在 `regenerate_for_conflict` 时才先做一张 family pilot，验收并冻结重排布局后其他成员并行；全为 `direct_overlay` 的系列完成逐图 dry-run/plan 后可直接并行确定性叠加。最终做分阶段三联检查。
- 同一批必须使用同一 Logo；不同 Logo 拆成独立批次。所有画幅按短边 4000 基准与 `1036×309` 参考框保持统一相对视觉大小和左上角位置。

执行 Logo 任务前必须读取 [references/logo.md](references/logo.md)，并以该文件作为唯一详细规则源。

## 批量、四路与降级

- 批量先运行 `scripts/preflight_images.py`，生成 manifest v3。纯文字 generate 使用 `--mode generate --variants <数量>` 且不传 `--input`；输入型任务用 `--logo`、重复 `--exclude` 或 `--roles-file` 排除 Logo/参考素材，PSD/PSB 明确记录为不支持并跳过，不增加强制依赖。
- 默认 `MAX_WORKERS = min(4, 宿主可用槽位, 待处理数, 宿主并发上限)`。宿主明确不支持并行时直接使用 1；未知时默认最多 4。
- 主协调者兼任 worker-1，最多另启三个 worker。一个 task 只归属一个 worker、只对应一个输出；每个 worker 内逐张串行。
- worker 写独立 `.xobi/work/task-state/<task_id>.json`；共享 manifest 只通过 `scripts/update_manifest.py` 的锁定合并更新。
- 每次实际图片调用都连续登记唯一 attempt，返回可验收候选即占用当前阶段一次质量 attempt；只有未通过验收才触发重试，每个执行阶段初次结果后最多 2 次针对性重试，共 3 个，只重试当前失败图。禁止 0 次成功、重复、跳号、漏记或三次失败后的第 4 次成功。没有可用候选的限流、连接、附件或落盘错误计基础设施 attempt；初次调用后最多重试 3 次，依次按 2/5/10 秒退避，共 4 个。混合结果分别计数，每次图片调用只归入一个结果类别。翻译候选后还需 AI Logo 冲突重排时，先登记 pending 的已接受翻译候选，再为 `logo_conflict` 使用下一 attempt；确定性后处理不增加图片 attempt。
- 两个 worker 出现同类基础设施错误时取消尚未执行的并行退避重试、暂停新任务，并按 [references/quality.md](references/quality.md) 选一个原始 pending task 做单路探针。探针必须沿用该 task 的参考图、prompt 和输出隔离，且按实际结果计入该 task；不得创建无归属调用。探针候选验收成功后降为 1 路补 pending。
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
- `scripts/preflight_images.py`：无输入 generate 变体预分配、输入角色过滤、唯一命名、worker 分配和 manifest v3。
- `scripts/update_manifest.py`：独立 task state、锁定合并和成功校验。
- `scripts/verify_manifest.py`：最终完整性、重复和哈希复核。
- `scripts/apply_logo.py`：Logo 几何 dry-run 与最终确定性叠加。
- `scripts/logo_relocation.py`：重排 Logo 冲突模块时，复算移动前后 ROI 指纹、原位清除、多模块一一对应，以及原/目标 ROI 外逐像素不变证据。
- `scripts/normalize_logo.py`：保守清理透明边或经视觉确认的纯色外围边，并把原始/规范化 Logo 的路径、哈希与裁切谱系原子登记到 manifest。
- `scripts/create_contact_sheet.py`：普通总览或按 family 的 source/base/final 三联图。
- `scripts/compose_localization.py`：按调用前已冻结的逐图文字 bbox，把 raw edit candidate 确定性合回 source，输出框外逐像素不变的无损 localized_base 与 provenance；reference-edit success 必须登记 provenance，update/verify 会从 raw candidate 重算合成结果。
- `scripts/resample_image.py`：只在宽高比完全相同时，把整张已验收图片用 LANCZOS 原子重采样到精确尺寸，并按 manifest 期望格式编码。
- `scripts/optimize_images.py`：仅按用户明确规格做离线尺寸、格式和体积优化。
- `scripts/install_skill.py`：安装到 Codex/OpenClaw，支持跨电脑路径。

## 执行顺序

1. 只读查看输入并通过开工门禁。
2. 读取当前模式所需 reference；批量创建 manifest 并锁定输入角色。
3. 建立逐图内容锁；翻译在 attempts=0 时用独立 pending 更新登记并冻结 localization plan，Logo 写 logo plan/family pilot。
4. 按 task 分工执行，每个 attempt 只产一个候选并立即记录。
5. 逐图查看 source/base/final，失败项按精确原因重试。
6. 生成联系表并运行 manifest 验证；核对成功、跳过、失败、遗漏和重复。
7. 只交付任务根目录最终成品或不含 `.xobi/` 的 ZIP，并报告绝对路径。
