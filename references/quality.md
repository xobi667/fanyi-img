# xobi-img 质量验收

## 单图通用

- 用户点名的变化已完成；未点名区域保持。
- 主体完整，无多余部件、畸变、错误透视、异常阴影或反射。
- 比例、精确尺寸、格式和透明通道符合本次要求。
- 没有未要求的文字、Logo、水印、角标、对象或伪字。
- 原图未覆盖，成品路径与 manifest 预分配路径一致。
- 必须视觉查看本地成品；脚本返回 0、文件存在或分辨率正确都不能代替视觉验收。

## localization

逐图 source/raw_edit_candidate/localized_base/final 并排检查；`raw_edit_candidate` 是不可交付的图片工具候选，reference-edit 的 `localized_base` 是通过 `compose_localization.py` 只合入计划文字框的无损 PNG，`final` 是验收后的交付文件。只有同尺寸 PNG 且没有 Logo 时，localized_base 与 final 才可以是同一路径：

- `source -> localized_base` 只有 plan 中 `(source_bbox ∪ target_bbox) - protected_non_text_regions` 的像素允许变化；保护区及框外解码 RGBA 像素必须逐像素相同。商品、全部照片、人物、图标、Logo、徽章、边框、色块、背景、阴影、纹理、数量、顺序和相互关系均未漂移。`non_text_inventory` 必须使用结构化 element bbox；所有与当前文字框相交的 element 都必须由同 ID 保护区完整覆盖交集，只有 `background_surface/canvas/null` 可免保护。字符串旧格式、漏报、缩小保护区、目标框侵入或候选返回后补报均失败。单框超过画布 20%、全部文字框 union 超过 60%、任何保护区/框外变化或缺少可重算映射的 ratio adaptation 都必须 fail closed。整图 `size_resample` 只允许发生在 localized_base 验收之后。
- 文字框必须贴合真实字形及必要的原背景；每块按 `(source_bbox ∪ target_bbox) - protected_non_text_regions` 的真实可编辑掩膜统计，两个框之间的空白和保护区不计入分母。目标文案不同却没有显著像素变化，或可编辑区内色差达到 8 的有意义变化/色差达到 20 的显著变化超过 85%，均拒绝，防止空跑、整块轻微调色或塞图，同时容忍跨分辨率 LANCZOS 对齐产生的极小插值误差。该像素启发式不等于 OCR，逐字语言、漏译、重复和额外文字仍由视觉验收负责。
- 硬像素锁和全局构图门禁都必须通过。reference-edit success 还必须登记 composition provenance；update/verify 从锁定的 raw candidate 和 frozen plan 重算 localized_base。删掉一个小图标、轻微全局调色、伪报整画布为文字框、缺少 composition、结果与重算不等价、篡改 provenance、把 raw candidate 直接交付或让 `localized_base -> final/prepared_base` 出现未经登记的变化，均判失败。provenance 证明结果一致性，不把可手写的 producer 字段当作进程身份认证。
- 当前 task 的 localization plan 必须在 attempts=0 时以独立 pending 更新登记；artifact 路径、SHA-256、manifest/task/source 绑定和 JSON 内容在 update/verify 中一致。success 首次传 plan、候选出来后扩大 bbox、attempt 后修改文件或复制另一 task 的计划登记，均判失败。
- 保持原比例且未指定不同精确尺寸时，全部非文字位置、间距、构图、裁切、尺度和画布结构完全未变；目标文字只允许 frozen plan 中逐块登记的排版适配。最终格式不是 PNG 时，只允许通过 `scripts/resample_image.py` 以 localized_base 原尺寸做一次确定性编码；相同宽高比的新精确尺寸只允许该脚本完成 `localized_base -> final`，或组合 Logo 任务中的 `localized_base -> prepared_base`，一次整图 LANCZOS 等比确定性重采样，并复核真实编码、透明像素和 ICC。二次 JPEG/WebP 编码或任意另存会因无法重现固定编码而失败。新比例缺少结构化、可重算坐标映射时直接 fail closed，不能用自由文本 allowed_changes 放行。
- 原文字块与译文一一对应，块数、语义角色、块阅读顺序、层级和颜色保持；`target_bbox` 默认等于 `source_bbox`，只有 plan 记录的长文案扩框或目标语言书写方向/对齐调整存在 `text_layout_adaptation`，且不得碰非文字模块。
- 逐字检查语言、拼写、标点、数字、币种、型号、数量、尺寸和单位；没有漏译、重复、新增、乱码或伪字。
- 长译文在 plan 前确定忠实文案，锁定后只做已记录的换行/文字框/字号适配；`user_exact` 与 `requested_target_text` 逐字相同，没有精简、改写、压缩、拉伸、粘连、重叠、裁切或溢出。
- 结果不得出现重设计、换商品图、删图标、改颜色、改边框、换背景或新增卖点。

任何非文字锁、构图门禁、确定性合成或 stage derivation 失败都判失败。初次结果后最多 2 次针对性参考编辑重试；仍失败必须先询问用户，不能静默纯重建。

## Logo

按 [logo.md](logo.md) 逐项验收。最低要求：

- Logo 来自 manifest 锁定的真实资产，哈希、颜色、纵横比和透明度正确；不是 AI 近似图。
- 可见 alpha 覆盖完整画布的 Logo（包括近不透明）已有明确的外围底色检查记录；`--opaque-approved` 只用于确认完整底板属于设计本身，不能拿来跳过白边/大画布清理。
- `visible_bbox` 内没有文字、小图、角标、徽章、图标、脸部或其他信息模块。只进入 safe padding、不碰可见 Logo 的内容不算冲突。
- 无冲突图没有被多余重生；普通背景或无信息商品边缘不要求清空。
- 重排图只移动完整冲突模块；最近模块外边缘落在 dry-run 给出的 module start range，没有第二段额外空白、整条顶部空带、顶栏、底板或占位框。
- 每张重排图都有同尺寸的移动前 `conflict_reference_base` 和移动后 `prepared_base`；没有可重算全画布映射时不同尺寸直接失败。验证器逐模块确认原 bbox 实质清除、目标 bbox 与原模块视觉指纹对应、多冲突一一匹配，并以原/目标 bbox 加固定 2px 羽化为唯一可变区，其他 RGBA 像素逐像素相同。改一个无关像素、删除远处商品、复制到新位置但不清原位、只清原位不放目标、错模块或交换 anchor 都失败。登记的 `logo_relocation_validation` 与 update/verify 按当前两份文件重算结果完全一致。
- source/conflict_reference_base/prepared_base/final 分阶段比较没有新增、遗漏、重复或改动商品、照片、原文、图标、赠品、标签和徽章。组合翻译任务另保留 `localized_base`，不得被 conflict_reference_base 或 prepared_base 覆盖。
- 存在冲突重排的 family 先有合格 pilot，成员沿用方向、层级、module anchor 和间距；全 direct_overlay family 不强制 pilot；不同 family 不强行同版。
- 所有比例使用同一短边公式；同批 Logo 可见视觉大小和左上角位置一致，未拉伸、裁切、模糊、重影或带意外白边。
- 最终图与 `prepared_base + active Logo` 的锁定公式逐像素一致；PNG/BMP/TIFF 直接比较，JPEG/WebP 按固定编码参数重现后比较。叠加后没有再次交给 AI 或再次有损编码。

## manifest 与批量完整性

最终必须满足：

```text
success + skipped + failed == target 总数
每个 task_id、source、output_key 唯一
一个 task 只归属一个 worker、只对应一个预分配输出
success 文件存在、可解码、扩展名/真实编码/透明契约一致、比例/尺寸正确且 SHA-256 未变化
不同 task 的最终输出路径与内容哈希不重复
.xobi/manifest.json、report.md、task-state 与磁盘一致
任务根目录没有遗漏成品或未登记的多余图片
```

运行：

```text
python scripts/verify_manifest.py --manifest <任务目录/.xobi/manifest.json>
```

验证未通过时不得声称“全部完成、无遗漏、无重复”。ZIP 输入还要核对安全解压目标数、终态数和 ZIP 成品数；ZIP 排除 `.xobi/`。

## 四路、重试与降级

- worker 数是 `min(4, 可用槽位, task 数, 宿主并发上限)`；宿主明确不支持并行时直接为 1。
- 返回可验收候选即计质量 attempt；只有未通过验收才触发重试，每图每个执行阶段初次结果 + 2 次针对性重试，共最多 3 个，不触发全局降级。基础设施失败没有可用候选，不占质量预算。
- 每次图片调用必须连续登记唯一 attempt；验收通过的候选也占用当前阶段质量预算，禁止 0 次成功、重复、跳号、漏记或在三次失败后登记第 4 次成功。翻译候选后还需 AI Logo 冲突重排时，先登记 pending 的已接受翻译候选，再用下一 attempt 登记 `logo_conflict`；确定性后处理不增加图片 attempt。
- Localization 只有当前 task 已记录 3 次 `attempt_stage=reference_edit` 质量失败，且 item 授权绑定本次 `manifest_id + task_id + source_sha256 + 第三次失败记录` 后，才可切到纯重建；一张图的授权不得扩散到同批其他图或新任务。纯重建 success 必须显式记录 `localization_execution_stage=pure_rebuild`，不得声称走过 bbox composition；只写 approval 不会放松 reference-edit 像素锁。切换后另开独立的初次 + 2 次 `attempt_stage=pure_rebuild` 质量预算，两阶段都不得无限重试。
- 基础设施问题：没有可用候选的限流、连接、附件或落盘错误，初次调用后最多重试 3 次，依次等待 2/5/10 秒，每个执行阶段总计最多 4 个 infrastructure attempts。混合质量/基础设施失败分别计数；每次图片调用只归入一个结果类别，并保留不归零的总调用序号。
- 两个 worker 出现同类基础设施错误：取消尚未执行的并行退避重试，停止派发新 task，选择最早的受影响 pending task，以该 task 的原始参考图、prompt 和隔离输出做一次单路探针。探针失败计入该 task 的 infrastructure budget；探针成功产生的候选计入该阶段 quality budget并正常验收，然后降为 1 路补 pending。不得创建无 task 归属的探针。
- success 默认不重跑；只有用户明确要求，或共同 family/style lock 被证明错误时，才重做明确受影响范围。
- 不切换未授权图片服务，不索取临时 API key。

## 联系表

普通批次查看最终图总览；翻译和 Logo 生成按 family 分组的 source/base/final 三联表：

```text
python scripts/create_contact_sheet.py --manifest <manifest> --output <.xobi/work/source-base-final.jpg>
```

横向检查内容锁、字体、层级、对齐、边距、间距、商品尺度和 family lock。只重试异常项；共享基准错误时只重做受影响 family，除非用户明确要求全部重做。

## 报告与交付

`.xobi/report.md` 至少列出模式、修改目标、比例、语言、worker、target/success/skipped/failed/pending、PSD/PSB 跳过、失败原因和任务路径。任务根目录只放最终成品；交付路径必须是绝对路径。只有用户明确要求尺寸、格式或体积时才做确定性后处理，不能借后处理改变视觉布局。
