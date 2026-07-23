# xobi-img 质量验收

## 单图通用

- generate、普通 edit 和 commerce_main_image 使用无参考纯生图；localization 使用当前原始源图作为唯一参考；Logo 按专属规则处理。通过验收的完整候选直接成为成品视觉内容，不经过本地蒙版、裁贴或局部合成。
- 用户点名的变化已完成；未点名区域保持。
- 主体完整，无多余部件、畸变、错误透视、异常阴影或反射。
- 比例、精确尺寸、格式和透明通道符合本次要求。
- 没有未要求的文字、Logo、水印、角标、对象或伪字。
- 原图未覆盖，成品路径与 manifest 预分配路径一致。
- 必须视觉查看本地成品；脚本返回 0、文件存在或分辨率正确都不能代替视觉验收。

## commerce_main_image

只有已按 [main-image.md](main-image.md) 显式进入 `commerce_main_image` 并冻结四项门禁与艺术指导的 task 才执行本节。内容正确不是充分条件；审美不合格本身就是 quality failure。

- manifest 的 `workflow=commerce_main_image`、`main_image_policy`、商品内容锁和艺术指导均已在第一次图片调用前冻结；`main_image_policy` 中的平台或“通用电商”、视觉方向、比例、文字策略和精确文字与用户确认一致，候选返回后不得为了包容结果改 plan。
- 图片模型调用为 `REFERENCE INPUT: NONE`。source、target、asset、style/layout reference、pilot 图片和最近会话图片都未作为参考输入；Logo 例外仍只按 [logo.md](logo.md) 生效。
- 全尺寸候选中的单一焦点明确，商品是第一阅读层；商品完整、轮廓清楚、占比与安全边距落在 plan 的冻结范围，没有危险裁切、拉伸、压扁、局部放大或漂浮感。
- 信息层级使用实现目标所需的最少层数；没有拥挤拼贴或互相争抢注意力的道具、文字、角标和装饰。文字严格符合 `text_policy`；`no_text` 时没有新增画布/营销文案，但商品本体、包装、铭牌上的锁定印刷及源图原有 Logo 仍保留，除非用户另行明确授权删除。无编造卖点、参数、认证、折扣、评分、赠品、伪字或遗漏的必需精确文案。
- 商品尺度、透视、场景关系可信；材质纹理尺度、粗糙度、反射、透射或织物细节真实，没有塑料化、蜡感、过度磨皮、重复纹理、伪高光或虚假结构。
- 主光方向、曝光、高光、接触阴影、反射和轮廓分离一致；商品不漂浮，阴影不脏、不双重且不与光源矛盾。背景干净，主体分离充分，色彩和饱和度受控。
- 没有廉价黄黑促销条、随机红角标、粗描边、椭圆贴片、拥挤拼贴、廉价伪 3D 文字/按钮/漂浮图标、过饱和或其他冻结禁用样式。
- 必须按 [main-image.md](main-image.md) 的真实两步 CLI 先 `prepare`，把候选原始字节冻结为独立 full snapshot，实际视觉查看全尺寸/保持比例长边 `256px`/保持比例长边 `160px` 三档，再填写绑定模板并 `finalize`。每个有候选的 attempt 必须在同一次 manifest update 绑定自己的 review；失败 review 必须是 `passed=false`，通过 review 必须是 `passed=true`。七项分数全部 `>=4`、六项 required checks 全为 `true`、八项 hard rejects 全为 `false` 后才能接受候选。脚本生成审阅材料和证据，不替代人工视觉判断，也不得修改候选；手写 review、缺 assessment/evidence、复用旧 review 或候选被覆盖后丢失历史 full snapshot 都失败。
- 长边 256 和长边 160 两档中，商品、单一焦点和主轮廓可立即识别；保留文字时主文案仍可读，辅助信息不是理解画面的前提。缩放必须保持比例，不得裁切或拉伸；任一档失败即整张候选失败。

失败时登记精确原因：`weak_single_focus`、`hero_occupancy_or_margin_fail`、`unsafe_crop`、`cluttered_hierarchy`、`material_unrealistic`、`scale_or_perspective_fail`、`lighting_or_shadow_fail`、`background_separation_fail`、`thumbnail_256_fail`、`thumbnail_160_fail`、`cheap_cliche` 或 `invented_claim`，并把该候选自己的 `passed=false` finalized review 登记进同一个 attempt record。从同一无参考纯生图阶段只针对失败轴重试；初次结果后最多 2 次。三次仍失败就报告失败，把终局拒绝候选归档到 `.xobi/work/rejected/` 并移出交付根目录，但所有历史 review、assessment、evidence、full snapshot 和缩略图继续保留且最终 verify 会逐条复算；不得把“内容没错但很丑”的候选登记为 success。

主图批量按 family 先验收一张内部 pilot。pilot 的全尺寸/长边 256/长边 160 三档都通过后才并行该 family 其余成员；无需用户逐张确认。成员只沿用冻结的抽象艺术指导，不得引用 pilot 图片或串用其商品和文案。

## localization

逐图并排检查 `source/fanyi_raw_candidate/final`。图片模型返回完整参考编辑候选，通过全部验收后候选才可成为 final 的视觉内容：

- 当前 task 的原始源图是图片模型唯一参考。本地文件使用 `referenced_image_paths`，会话图片使用最小 `num_last_images_to_include`，两者不得共存；不得附带失败候选、另一 task、Logo、pilot 或其他图片。
- 当前 task 的 localization plan 必须在 attempts=0 时独立登记，绑定 manifest/task/source 路径、SHA-256 和尺寸。逐块原文、译文、角色、顺序、位置和完整 `content_lock` 在第一次图片调用前冻结；另一 task 的计划、译文或构图不得复用。
- 原文字块与译文一一对应，文字块数量、语义范围、阅读顺序、层级、颜色和模块位置保持。逐字检查语言、拼写、标点、数字、币种、型号、数量、尺寸和单位；没有漏译、重复、新增、乱码或伪字。
- `user_exact` 必须与 `requested_target_text` 逐 Unicode 字符相同；不得精简、改写、纠错或替换。长译文只能在原文字模块内自然换行、调整字距和适度缩小字号，不能移动或扩大模块、压缩或拉伸字形、粘连、重叠、裁切、溢出或小到不可读。
- source 与候选中的商品、照片、人物、Logo、图标、徽章、边框、色块、背景、阴影、纹理、装饰、数量、顺序、颜色、轮廓、材质、视角、裁切、尺度、位置、间距、构图、版式和相互关系必须保持；任何可见的重设计、换商品、删图标、全局调色、改边框、换背景、移动模块或新增卖点都失败。
- 无字区域继续无字；不得增加标语、参数、角标、底板、装饰、水印或伪字。包装/商品本体上的品牌和印刷文字只有用户明确点名才允许翻译。
- 参考图编辑仍需视觉核对全部非文字内容。可以用 OCR、感知差异、元素检测和联系表辅助发现漂移，但它们只能用于拒绝候选，不能用于本地修补候选。
- 保持原比例时，画布、裁切和布局必须保持。用户明确新比例时，`allowed_changes` 只能包含 `minimal_canvas_adaptation`、`proportional_subject_scaling` 和 `necessary_text_reflow`，仍不得改变商品形状、背景风格、信息数量和层级；两项要求无法同时满足时必须在开工前确认，不能让失败候选替用户决定。
- 通过验收的候选可复制、移动、重命名和登记哈希，但不得运行 `compose_localization.py`、本地文字蒙版、局部裁贴或像素回填。组合 Logo 任务之后才进入 [logo.md](logo.md) 的冲突处理和最终确定性 Logo 叠加。

初次结果后最多 2 次针对性质量重试。每次都重新引用原始源图，不得把失败候选串成下一次参考；三次仍失败就报告失败。1:1 且用户未覆盖旧版规格时，最终必须由 `final_optimize_images.py` 输出 JPEG、`800×800`、`900–1024KB`。

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
- `direct_overlay` 不调用图片模型；最终 success 更新不传 `--attempts` 或 `--attempt-stage`，不得新建 attempt record，前序 attempts 总数和历史保持不变。
- 只有真实信息冲突经冻结 plan/geometry/decision 证明，并实际调用图片模型重排时，才新增独立 `attempt_stage=logo_conflict` 图片 attempt；无冲突、`direct_overlay`、确定性叠加或单纯状态更新均不得增加 attempt。

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
- 图片工具只要返回任何可读取候选就计质量 attempt，不以“最终可验收”为计数前提；commerce 主图的每个此类 attempt 还必须同时绑定由该候选三档证据 finalized 的 review。`logo_conflict` 的每个此类 attempt 必须传入并永久保留独立 `.xobi/work` `prepared_base`，由 manifest 记录并复核候选路径、SHA-256 与尺寸，不得复用路径或哈希；accepted pending/success 还必须同次通过 relocation/pixel-lock，并在 item 与 attempt 保存完全相同的可复算证据。只有候选未通过验收才触发重试，每图每个执行阶段初次结果 + 2 次针对性重试，共最多 3 个，不触发全局降级。基础设施失败没有可读取候选，不占质量预算，也不得伪造 candidate、review、prepared_base、output 或其他候选产物。
- Localization 的图片调用必须连续登记；验收通过的候选也占用质量预算，禁止重复、跳号、漏记或三次失败后登记第 4 次成功。每次翻译调用的唯一参考都是当前 task 的原始源图。普通 generate/edit 仍锁定无参考纯生图；Logo 冲突阶段规则不变。
- 基础设施问题：没有可用候选的限流、连接、附件或落盘错误，初次调用后最多重试 3 次，依次等待 2/5/10 秒，每个执行阶段总计最多 4 个 infrastructure attempts。混合质量/基础设施失败分别计数；每次图片调用只归入一个结果类别，并保留不归零的总调用序号。
- 两个 worker 出现同类基础设施错误：取消尚未执行的并行退避重试，停止派发新 task，选择最早的受影响 pending task，以该 task 的冻结 prompt、原模式输入策略和隔离输出做一次单路探针。localization 探针仍只引用原始源图，`logo_conflict` 探针仍只引用冻结底图。探针失败计入该 task 的 infrastructure budget；探针成功产生的候选计入质量预算并正常验收，然后降为 1 路补 pending。不得创建无 task 归属的探针。
- success 默认不重跑；只有用户明确要求，或共同 family/style lock 被证明错误时，才重做明确受影响范围。
- 不切换未授权图片服务，不索取临时 API key。

## 联系表

普通批次查看最终图总览；主图另看全尺寸/长边 256/长边 160 review，翻译按 source/fanyi_raw_candidate/final，Logo 按 source/conflict_reference_base/prepared_base/final 分阶段查看：

```text
python scripts/create_contact_sheet.py --manifest <manifest> --output <.xobi/work/source-base-final.jpg>
```

横向检查内容锁、字体、层级、对齐、边距、间距、商品尺度和 family lock。只重试异常项；共享基准错误时只重做受影响 family，除非用户明确要求全部重做。

## 报告与交付

`.xobi/report.md` 至少列出模式、修改目标、比例、语言、worker、target/success/skipped/failed/pending、PSD/PSB 跳过、失败原因和任务路径。任务根目录只放最终成品；交付路径必须是绝对路径。只有用户明确要求尺寸、格式或体积时才做确定性后处理，不能借后处理改变视觉布局。
