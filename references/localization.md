# localization 唯一规则

本文件是跨语言图片翻译的唯一真源。默认模式必须是 `text_only_reference_edit`，不得默认纯生图重建；用户已给同语言精确替换文案时改走 `edit/text_replacement`。

## 开工条件

- 确认目标图、目标语言、输出比例或“保持原比例”。
- 多图时先确认每张图片的角色；每个成品只使用当前任务对应的源图，禁止跨任务串图。
- 只允许在开工门禁前进行只读查看、尺寸检查和文字盘点；信息不全时先询问，不创建正式任务或调用图片编辑。

## 默认执行：text_only_reference_edit

1. 查看当前源图，逐块记录原文、准确译文、目标文案来源、位置、层级、颜色、字体视觉、图标关联和换行关系；同时把纯背景承载面和所有有界非文字 element 分别写入结构化 `non_text_inventory`。脚本根据 inventory 与每块 `source_bbox ∪ target_bbox` 重算 `protected_non_text_regions` 是否完整；确实没有相交 element 时也必须写空列表。用户给出精确目标文案时标记 `target_text_source=user_exact`，逐 Unicode 字符锁定，禁止精简、润色或改写。
2. 把当前源图作为本次图片编辑的唯一目标参考，按宿主实际工具 schema 传入；不得省略参考图，也不得引用其他任务图片。图片工具返回的是 `raw_edit_candidate`，只能保存在 `.xobi/work/`，不能直接当作 `localized_base` 或成品。
3. 只替换源图中已经存在且盘点为 `translatable_text` 的可见覆盖文案。文字块数量和信息范围保持一一对应；无文字区域继续无字。
4. 除文字内容及适应译文所必需的自然换行、文字框和字号微调外，保持商品、人物、数量、轮廓、材质、颜色、视角、背景、图标、徽章、Logo、促销结构和未指定区域不变。
5. 禁止新增、推断、补全或删除卖点、参数、型号、单位、角标、品牌、装饰文字和伪字；不得根据文件名、目录名或商品外观创造文案。
6. `translated` 长译文只在写 plan 前选择忠实且不增删事实的自然表达；`user_exact` 不改任何字符。写入 plan 后只依次使用合理换行、记录后调整 `target_bbox`、适度缩小字号；禁止压缩或拉伸字形、重叠、裁切、溢出和不可读缩小。未记录 `text_layout_adaptation` 时文字框不得扩大。

默认范围优先级：普通标题、说明、促销和参数覆盖文案属于 `translatable_text`；品牌字标、Logo、型号和产品专名默认锁定；包装或商品照片内部的印刷文字属于产品像素，默认保持，只有用户明确点名后才作为独立文字块处理。不得因“翻译全部”擅自改画在商品本体上的品牌与包装。

文字模糊、遮挡或分辨率不足以可靠识别时不得猜测。若它影响“全部翻译”的完整性，开工前只追问该块；用户无法确认时把它记录为 `unresolved_text`、保持原样并在报告中列明，不得伪造译文。

## 确定性文字框合成与硬像素锁

`text_only_reference_edit` 不信任图片模型对整张候选的保持能力。每次 `reference_edit` 返回 `raw_edit_candidate` 后，必须运行：

```text
python scripts/compose_localization.py --source <原图> --candidate <.xobi/work/raw-edit-candidate> --output <.xobi/work/localized-base.png> --plan <localization-plan.json> --provenance-json <.xobi/work/localization-composition.json>
```

- 脚本先从结构化 inventory 重算所有相交 element 是否已由同 ID 保护区完整覆盖，再只从候选复制每个文字块的 `(source_bbox ∪ target_bbox) - protected_non_text_regions`；受保护的小图、Logo、徽章、边框和商品像素即使位于文字框内也直接取自原图，其余框外像素同样取自原图。候选与原图分辨率不同但宽高比相同时，先只为取框内容对齐到源尺寸。输出固定为无损 PNG，并记录 source、raw candidate、plan、output 的路径与 SHA-256、尺寸、可编辑掩膜、保护区和是否对齐缩放。
- `localized_base` 在所有计划文字框以外的解码 RGBA 像素必须与 source 逐像素一致；删除图标、全局调色、换商品、改背景、改边框或任何一个框外像素都会被 `update_manifest.py` 和 `verify_manifest.py` 重新计算后拒绝，不能靠 provenance 自报通过。
- `reference_edit` success 必须通过 `--localization-composition-json` 登记上述 provenance。update/verify 会锁定 provenance 与 raw candidate 的路径和哈希，并重新执行 `source + raw candidate + frozen bbox mask` 合成比较；缺少记录、结果不等价、篡改候选或篡改 provenance 都不能登记成功。文件本身不能证明具体由哪个进程写出，因此这里保证的是结果与官方合成算法逐像素等价，不宣称 producer 字段能认证脚本身份。
- 单个 `source_bbox` 或 `target_bbox` 不得超过画布 20%，全部文字框 union 不得超过画布 60%。超限即 fail closed，禁止把整画布、整个商品模块或巨大自由区域伪报成文字框。确属文字密集海报时先收紧到真实字形及必要背景范围；仍超限则停止并报告需要独立审核，不能默认放宽。
- 每个文字块按扣除 `protected_non_text_regions` 后的真实可编辑掩膜检查，不得用两个框之间的空白包络稀释统计。保护区像素必须与 source 逐像素相同。目标文案与原文不同时不能完全没有显著变化；色差达到 8 的有意义变化或色差达到 20 的显著变化超过真实可编辑区的 85%，都视为整块调色、换图或把文字框当自由绘图区并拒绝。色差 8 的门槛允许跨分辨率候选经 LANCZOS 对齐时产生极小插值误差，但不能放行整块轻微调色。这个门禁不能识别具体语言，因此仍必须逐字视觉核对译文和额外文字。
- 硬像素锁之后仍保留全局构图门禁；文字框内的大面积重画、模块换序或明显版式漂移仍可被拒绝。文字内容、OCR、语言、拼写和排版继续按人工视觉验收，不因像素锁通过而自动算成功。
- `ratio_adaptation.required=true` 当前没有可重算的结构化坐标映射，默认 fail closed。自由文本 `allowed_changes` 不能充当像素映射或绕过硬锁；在实现并登记可重算映射前，必须报告限制或另行确认处理方案。

禁止恢复旧版流程：翻译任务不得默认纯生图，也不得省略当前源图参考。视觉门禁或像素锁失败只能记录当前图的 `reference_edit` 质量失败并从原始 source 重试，不能把失败候选交付，也不能静默切换纯重建。

## 输出比例与唯一布局例外

- 输出为“保持原比例”，或目标尺寸与源图完全相同：先在源尺寸生成无损 PNG `localized_base`，画布结构、裁切、全部非文字位置、尺度、间距和构图绝对锁定。最终格式也是 PNG 时它可直接作为 final；用户要求 JPG/WebP/BMP/TIFF 或保留其他源格式时，必须再用 `resample_image.py` 以相同尺寸做一次确定性编码，不能直接另存或二次有损编码。
- 用户明确指定相同宽高比但不同的精确像素尺寸时，原生参考编辑候选 `localized_base` 仍保持源图尺寸和全部布局；验收通过后才运行 `scripts/resample_image.py`，把整张画布等比确定性重采样（Pillow LANCZOS）为 `target_size`，得到 `final`。脚本必须使用 manifest 的 `expected_format` 对应 `--output-format`，并保留支持格式中的透明像素与 ICC；不得分别缩放元素、补边、裁切或让图片模型借尺寸要求重画内容。
- 用户明确指定的新比例与源图不同时，当前 localization 在结构化、可重算坐标映射尚未实现前必须 fail closed：不得创建可执行 plan、不得调用图片工具，也不得用自由文本 `allowed_changes` 放行。先说明硬像素锁无法同时证明新画幅与非文字零改动，让用户选择保持原比例；若用户确实要改比例，把它拆成另一个明确授权、独立验收的比例适配任务，不能算作“翻译自动附带的修改”。
- 调用图片工具前分别记录 `target_size`、`size_resample` 和 `ratio_adaptation`。相同宽高比的精确尺寸转换必须写 `size_resample={"required":true,"method":"whole_canvas_lanczos"}` 且 `ratio_adaptation={"required":false,"allowed_changes":[]}`。当前任何 `ratio_adaptation.required=true` 的 localization plan 都必须在登记时拒绝；不得把漂移解释为工具限制。

## localization_plan

单图和批量任务都必须在调用图片工具前，为每张图单独写入并冻结 item 的 `localization_plan` artifact，不得只做口头盘点，不得把多张图汇总进一个可变文件，也不得跨图复用译文或参考图：

```json
{
  "task_id": "task-000001",
  "mode": "text_only_reference_edit",
  "source": "...",
  "source_sha256": "...",
  "source_size": [1200, 1200],
  "target_language": "Indonesian",
  "output_ratio": "original",
  "target_size": null,
  "size_resample": {"required": false, "method": null},
  "ratio_adaptation": {"required": false, "allowed_changes": []},
  "text_blocks": [
    {
      "id": "text-01",
      "source_bbox": [40, 30, 420, 120],
      "target_bbox": [40, 30, 420, 120],
      "source": "...",
      "translation": "...",
      "target_text_source": "translated",
      "requested_target_text": null,
      "role": "heading",
      "text_layout_adaptation": {"required": false, "reason": null, "target_alignment": null, "writing_direction": null},
      "protected_non_text_regions": [
        {"id": "icon-01", "bbox": [52, 42, 88, 78]}
      ]
    }
  ],
  "unresolved_text": [],
  "non_text_inventory": [
    {"id": "product-photo-01", "kind": "element", "scope": "region", "bbox": [120, 260, 1080, 1160]},
    {"id": "icon-01", "kind": "element", "scope": "region", "bbox": [52, 42, 88, 78]},
    {"id": "border-grid", "kind": "element", "scope": "region", "bbox": [20, 180, 1180, 1190]},
    {"id": "background", "kind": "background_surface", "scope": "canvas", "bbox": null}
  ],
  "pure_rebuild_allowed": false
}
```

每个 task 必须使用自己独立的 `.xobi/work/<task_id>-localization-plan.json`。写完后、第一次图片调用前，先用单独的 pending 更新登记并冻结；登记命令不得同时记录 attempt、候选或 success：

```text
python scripts/update_manifest.py --manifest <manifest> --task-id <task> --worker-id <worker> --status pending --localization-plan-json <.xobi/work/<task>-localization-plan.json>
```

登记前会完整校验必填字段、逐块文案、`user_exact`、bbox、版式声明、非文字清单、目标几何、确定性重采样方法和 fail-closed 比例规则；无效计划直接拒绝且不会冻结，修正后仍可正常登记。登记成功会保存 artifact 路径、SHA-256、manifest/task/source 绑定和登记时的零 attempt 状态。计划文件不能是 symlink/junction，不能与另一 task 共用。success 命令第一次传计划、任何 attempt 后新增计划、修改 bbox/译文/模式、替换文件或让 manifest/task-state 中的 plan 与冻结 artifact 不同，都会被 update/verify 拒绝。图片候选出来后再反推扩大 bbox 属于无效计划。

`source_bbox` 使用源图像素坐标；`target_bbox` 使用 `localized_base` 画布坐标并默认与之相同。只有长译文或目标语言书写方向确需调整且 plan 已记录 `text_layout_adaptation.required=true`、原因、目标边界和对齐/书写方向时，才允许改到 `target_bbox`，并且不得侵入任何非文字模块。

`non_text_inventory` 只接受结构化对象，字符串旧格式直接 fail closed。允许免保护的唯一类型是 `{"kind":"background_surface","scope":"canvas","bbox":null}`，表示为擦除旧字所需的连续纯背景承载面；不得把图标、商品、照片、边框、纹理或色块边界伪装成背景。其他条目必须使用 `kind=element`、`scope=region` 和画布内准确 bbox。每块都必须有 `protected_non_text_regions` 列表；凡 element bbox 与该块 `source_bbox ∪ target_bbox` 相交，都必须以同 ID 保护，并完整覆盖脚本计算出的交集。空列表只在确实没有相交 element 时合法；漏报、缩小保护范围、自由改 bbox、事后补报或让扩展后的 `target_bbox` 侵入 element 都直接拒绝。`target_text_source` 只能是 `translated` 或 `user_exact`；后者必须同时记录 `requested_target_text`，且 `translation` 必须与其逐字相同。文字块数量、唯一 ID、顺序、结构化非文字清单、保护区和最终译文在图片调用前锁定；源 SHA-256 或尺寸变化时计划失效，必须重新查看源图。

提示词必须明确包含：

```text
TEXT-ONLY REFERENCE EDIT: Use the current source image as the sole target reference. Replace only the listed existing text blocks with the exact translations. Preserve every non-text pixel region and all unlisted content exactly; inability to preserve them is a failed result. Do not add, remove, infer, redesign, or rebuild any other content.
STRICT NON-TEXT LOCK: Every non-text element is immutable. Preserve the exact product and all photos, people, icons, logos, badges, borders, color blocks, background, shadows, textures, quantities, order, and relationships. Preserve exact non-text positions, spacing, composition, crop, scale, and canvas structure. Only each plan-approved text block may use its recorded wrapping, font-size, alignment, writing-direction, or TARGET_BBOX adjustment. Reconstruct only the minimal pixels directly behind the replaced glyphs. A different output aspect ratio is not authorized in this localization attempt.
STRICT NO-ADDITION RULE: Keep the number and scope of visible text blocks one-for-one. Blank or no-text areas must remain text-free.
TEXT_LAYOUT_ADAPTATION: Use only the per-block TARGET_BBOX, alignment, and writing direction explicitly recorded in the plan. Any unlisted text-region expansion or movement is forbidden.
RATIO_ADAPTATION: NONE. `ratio_adaptation.required` must be false; otherwise stop before calling the image tool.
```

## 验收与重试

- 每次尝试都从原始源图开始，不把失败输出作为下一次参考，避免累计漂移。
- 每次实际图片调用都把全局 `attempts` 严格加一，并写入唯一、连续的 attempt history；禁止 0 次成功、重复编号、跳号或漏记。返回候选即占用当前阶段一次 quality attempt，无论候选最终失败还是被接受。
- 每次参考编辑先保存隔离的 `raw_edit_candidate`，再确定性合成为 lossless PNG `localized_base`；success 同时登记不可变 composition provenance。未经合成、provenance 重算和硬像素锁的 raw candidate 一律不得登记 success。
- 逐字核对语言、拼写、标点、数字、币种、型号、单位和换行；核对无漏译、重复、新增、乱码或伪字。
- 对照源图检查所有非文字内容、商品几何、布局、颜色和信息模块没有漂移。
- 这里只讨论质量失败：初次结果质量验收失败后最多进行两次针对性重试，总计最多三个质量 attempt。每次只针对已确认的失败点收窄提示词，并以 `failure_type=quality`、`attempt_stage=reference_edit` 记录失败原因与尝试次数。
- 每阶段最多 3 个 quality attempts，成功候选也计入预算。三个都不合格时立即停止，保留失败记录并询问用户下一步；不得用“第 4 次成功”绕过预算，也不得自动切换纯生图。限流、连接、附件和宿主调用等基础设施失败按阶段执行初次调用加最多 3 次重试、2/5/10 秒退避，共最多 4 个 infrastructure attempts。
- 翻译候选验收后还要做 AI Logo 冲突重排时，先以 `status=pending`、`attempt_stage=reference_edit`（或已授权的 `pure_rebuild`）登记已接受候选，再把下一次图片调用登记为 `attempt_stage=logo_conflict`；最终 `localization_execution_stage` 仍取自前一个已接受的翻译候选。确定性编码、重采样和 Logo 叠加不增加图片 attempt。进入 `pure_rebuild` 后禁止退回 `reference_edit`。
- 阶段记录中，`source` 是原图，`raw_edit_candidate` 是只留在 `.xobi/work/` 的图片工具返回，`localized_base` 是只合入计划文字框的无损 PNG，`prepared_base` 是需要继续添加 Logo 时的最终尺寸无 Logo 底图，`final` 是交付文件。只有最终要求同尺寸 PNG 且没有 Logo 时，`localized_base` 才可与 final 指向同一路径；其他格式必须由 `resample_image.py` 做同尺寸确定性编码，相同宽高比的新尺寸则做整图确定性重采样。组合任务不得用一个含糊的 `base_output` 覆盖这些阶段。

## pure rebuild 例外

只有用户在获知当前图片 3 次参考编辑质量失败及其风险后，明确许可“纯生图重建”时才能使用。许可记录必须绑定本次不可复用的 `manifest_id`、当前 `task_id`、当前 `source_sha256` 和第三次失败记录；许可不得从“再试一次”“想办法”、一般性的继续指令、旧对话、旧 manifest 或同批另一图片中推断。许可一张不许可整批。

获准后冻结的 `localization_plan` 仍保持 `mode=text_only_reference_edit` 与原有文字块、bbox、译文和内容清单，禁止为了纯重建改写或换掉计划；实际执行模式由当前 task 的有效 `pure_rebuild_approval`、`attempt_stage=pure_rebuild` 和成功项的 `localization_execution_stage=pure_rebuild` 单独表示。报告可把这次执行显示为逻辑标签 `pure_rebuild_user_authorized`，但该标签不得写回或替换冻结计划。

纯重建是 bbox composition 的唯一明确例外：不得伪造 `compose_localization.py` provenance。把本阶段候选按 source 尺寸保存为无损 PNG `localized_base`，success 必须显式传 `--attempt-stage pure_rebuild`；只存在 approval 而没有该成功阶段标记时仍按严格 reference-edit 像素锁处理并拒绝整图变化。纯重建仍沿用逐图文字锁、商品几何锁、信息范围锁和严格无新增规则，并逐图视觉检查全部原内容、译文和排版；它不具备“未改像素”的保证，因此只有用户明知风险的当前任务许可才能启用。

切换后建立独立的纯重建质量预算：初次纯重建加最多 2 次针对性重试，共 3 个 `attempt_stage=pure_rebuild` quality attempts；不得把此前 3 次参考编辑混算后形成死路，也不得在新的 3 次耗尽后无限继续。基础设施失败仍独立遵守最多 4 次规则。
