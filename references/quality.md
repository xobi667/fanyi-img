# xobi-img 质量验收

## 单图通用

- 除 Logo 冲突底图专属流程外，原生图片调用是无参考图的纯生图；调用记录没有 target/reference/最近会话图片输入。通过验收的完整候选直接成为成品视觉内容，不经过本地蒙版、裁贴或局部合成。
- 用户点名的变化已完成；未点名区域保持。
- 主体完整，无多余部件、畸变、错误透视、异常阴影或反射。
- 比例、精确尺寸、格式和透明通道符合本次要求。
- 没有未要求的文字、Logo、水印、角标、对象或伪字。
- 原图未覆盖，成品路径与 manifest 预分配路径一致。
- 必须视觉查看本地成品；脚本返回 0、文件存在或分辨率正确都不能代替视觉验收。

## localization

逐图并排检查 `source/pure_generation_candidate/final`。默认翻译没有 reference-edit、文字框本地合成或像素回填阶段；图片模型返回的是完整纯生图候选，通过全部验收后该候选本身才可成为 final 的视觉内容：

- manifest 中的执行模式必须是 `pure_generation_localization`，`reference_policy=none`。调用记录不得包含源图、参考图、最近会话图片或其他隐式图片输入；候选出来后不得新增、删减或放宽冻结计划。
- 当前 task 的 localization plan 必须在 attempts=0 时独立登记，绑定 manifest/task/source 路径、SHA-256 和尺寸。逐块原文、译文、角色、顺序、位置和完整 `content_lock` 在第一次图片调用前冻结；另一 task 的计划、译文或构图不得复用。
- 原文字块与译文一一对应，文字块数量、语义范围、阅读顺序、层级、颜色和模块位置保持。逐字检查语言、拼写、标点、数字、币种、型号、数量、尺寸和单位；没有漏译、重复、新增、乱码或伪字。
- `user_exact` 必须与 `requested_target_text` 逐 Unicode 字符相同；不得精简、改写、纠错或替换。长译文只能在原文字模块内自然换行、调整字距和适度缩小字号，不能移动或扩大模块、压缩或拉伸字形、粘连、重叠、裁切、溢出或小到不可读。
- source 与候选中的商品、照片、人物、Logo、图标、徽章、边框、色块、背景、阴影、纹理、装饰、数量、顺序、颜色、轮廓、材质、视角、裁切、尺度、位置、间距、构图、版式和相互关系必须保持；任何可见的重设计、换商品、删图标、全局调色、改边框、换背景、移动模块或新增卖点都失败。
- 无字区域继续无字；不得增加标语、参数、角标、底板、装饰、水印或伪字。包装/商品本体上的品牌和印刷文字只有用户明确点名才允许翻译。
- 纯生图无法提供框外像素逐值相同的保证，因此不把像素相等冒充验收结论。可以用 OCR、感知差异、元素检测和接触表辅助发现漂移，但它们只能用于拒绝候选，不能用于本地修补候选。
- 保持原比例时，画布、裁切和布局必须保持。用户明确新比例时，`allowed_changes` 只能包含 `minimal_canvas_adaptation`、`proportional_subject_scaling` 和 `necessary_text_reflow`，仍不得改变商品形状、背景风格、信息数量和层级；两项要求无法同时满足时必须在开工前确认，不能让失败候选替用户决定。
- 通过验收的候选可复制、移动、重命名和登记哈希，但不得运行 `compose_localization.py`、本地文字蒙版、局部裁贴、像素回填或二次 AI 编辑。组合 Logo 任务只允许之后进入 [logo.md](logo.md) 的冲突处理和最终确定性 Logo 叠加。

初次纯生图结果后最多 2 次针对性质量重试，每图共 3 个 `attempt_stage=pure_generation` quality attempts；其冻结计划模式始终是 `pure_generation_localization`。三次仍失败就报告失败；不得登记第 4 次成功，不得切到参考编辑，也不存在“参考编辑失败三次后才授权纯生图”的流程。

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
- 图片工具只要返回任何可读取候选就计质量 attempt，不以“最终可验收”为计数前提；只有候选未通过验收才触发重试，每图每个执行阶段初次结果 + 2 次针对性重试，共最多 3 个，不触发全局降级。基础设施失败没有可读取候选，不占质量预算。
- Localization 的图片调用必须连续登记唯一 attempt；验收通过的候选也占用当前阶段质量预算，禁止 0 次成功、重复、跳号、漏记或在三次失败后登记第 4 次成功。翻译计划模式始终是 `pure_generation_localization`，普通翻译调用阶段是 `pure_generation`。普通 generate/edit 的第一阶段由 manifest `image_model_policy` 锁定无参考纯生图，不使用 localization stage；任何模式进入真实 Logo 冲突时，都必须已有 active Logo、冻结的冲突 plan/geometry/decision、已接受前序 base 与绑定的 `conflict_reference_base`，再用后续唯一 attempt 登记 `logo_conflict`。无 Logo、direct_overlay、无冲突或首次 attempt 拒绝。最终确定性 Logo 叠加不增加图片 attempt。
- 新 localization 不接受 `attempt_stage=reference_edit`、`pure_rebuild_approval` 或 `localization_execution_stage=pure_rebuild`。这些字段仅可出现在 v1-v3 旧 manifest 的只读兼容验证中；旧 manifest 不能新增 update/attempt，继续执行前必须迁移到当前纯生图策略。
- 基础设施问题：没有可用候选的限流、连接、附件或落盘错误，初次调用后最多重试 3 次，依次等待 2/5/10 秒，每个执行阶段总计最多 4 个 infrastructure attempts。混合质量/基础设施失败分别计数；每次图片调用只归入一个结果类别，并保留不归零的总调用序号。
- 两个 worker 出现同类基础设施错误：取消尚未执行的并行退避重试，停止派发新 task，选择最早的受影响 pending task，以该 task 的冻结 prompt、无参考输入策略和隔离输出做一次单路探针；只有 `logo_conflict` 探针沿用其唯一底图参考。探针失败计入该 task 的 infrastructure budget；探针成功产生的候选计入该阶段 quality budget并正常验收，然后降为 1 路补 pending。不得创建无 task 归属的探针。
- success 默认不重跑；只有用户明确要求，或共同 family/style lock 被证明错误时，才重做明确受影响范围。
- 不切换未授权图片服务，不索取临时 API key。

## 联系表

普通批次查看最终图总览；翻译按 source/pure_generation_candidate/final，Logo 按 source/conflict_reference_base/prepared_base/final 分阶段查看：

```text
python scripts/create_contact_sheet.py --manifest <manifest> --output <.xobi/work/source-base-final.jpg>
```

横向检查内容锁、字体、层级、对齐、边距、间距、商品尺度和 family lock。只重试异常项；共享基准错误时只重做受影响 family，除非用户明确要求全部重做。

## 报告与交付

`.xobi/report.md` 至少列出模式、修改目标、比例、语言、worker、target/success/skipped/failed/pending、PSD/PSB 跳过、失败原因和任务路径。任务根目录只放最终成品；交付路径必须是绝对路径。只有用户明确要求尺寸、格式或体积时才做确定性后处理，不能借后处理改变视觉布局。
