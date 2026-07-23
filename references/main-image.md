# commerce_main_image 唯一规则

`commerce_main_image` 是电商主图的显式 creative workflow。只有用户明确要求“做主图”“重做主图”或“优化主图”等整张主图创作时启用；仅说“翻译主图”、修改主图某一处、换背景、改字或泛称“处理图片”都不启用。无输入时基础任务仍按 `generate` 预检，有输入时仍按 `edit` 预检，并在 manifest 写入 `workflow=commerce_main_image`，在当前 task 的操作摘要、主图计划和报告中记录同一 workflow。

## 开工门禁

除目标商品或输入素材外，第一次图片调用前必须确认并冻结以下四项；缺什么只问什么，允许在一个问题中合并全部缺项：

- `platform_profile`：具体平台，或用户明确选择“通用电商”；不得静默猜平台。
- `visual_direction`：例如克制高级、自然生活方式、清爽科技或用户给出的其他明确方向。
- `output_ratio`：比例或精确尺寸，沿用本 skill 的比例门禁。
- `text_policy`：`no_text`、`preserve_existing_exact` 或 `user_exact`。`no_text` 表示不添加标题、说明、角标等主图营销文案，商品/包装本体上已锁定的品牌与印刷文字仍保留；`preserve_existing_exact` 只用于有输入的 edit 基础任务并逐块登记非空 `existing_text_inventory`；`user_exact` 必须先取得最终准确 `exact_text`。除 `user_exact` 外 `exact_text` 必须为空，不得自行写卖点。

平台规则与文字策略、比例或用户精确内容冲突时，开工前只说明冲突并让用户选择。门禁前只读查看商品、源图、Logo、asset、style/layout reference 与元数据；不得创建正式任务或调用图片模型。平台、视觉方向、比例和文字策略已经明确时直接执行，不要求用户挑模板，也不逐张确认候选。

## 授权边界与执行方式

- 主图请求授权在冻结艺术指导内重做构图、背景、光线、阴影、色彩关系、信息层级和文字视觉；它不授权改变商品身份、数量、型号、颜色、部件、自然比例、真实结构或用户事实。
- 协调者先查看输入并写出完整商品/内容清单；图片模型调用固定为无参考纯生图，不传 target、source、asset、style/layout reference、最近会话图片或任何隐式图片上下文。
- 参考素材只供协调者观察并转写为当前 task 的文字约束；不得把另一 task 的商品、文案、构图或 prompt 串入本图。
- 用户只要求翻译或说“翻译主图”时执行恢复后的 fanyi 参考图翻译，唯一变化只有准确译文，禁止借主图规则美化。普通 edit 仍只改用户点名项。
- Logo 唯一例外保持不变：只有用户明确要求添加本次真实 Logo 时，才按 [logo.md](logo.md) 做确定性叠加；真实遮挡时才允许 Logo 专属局部参考重排。

## 冻结艺术指导

每个 task 在 attempts=0、第一次图片调用前冻结独立 `main_image_plan`，绑定 task、manifest operation、源路径/哈希（如有）和预分配 final 路径，并登记不可变的 `main_image_plan_registration` 路径与 SHA-256。manifest 与计划必须完整写明：

```text
manifest: workflow=commerce_main_image; main_image_policy {platform_profile, visual_direction, output_ratio, text_policy, exact_text}
main_image_plan: schema_version=1, contract=commerce-main-image-plan-v1, task_id, creative_route=commerce_main_image, platform_profile, visual_direction, output_ratio, text_policy
exact_text, existing_text_inventory, product_content_lock, single_focus, hero_occupancy, safe_margin
information_hierarchy, composition, camera_and_scale, lighting_and_shadow
material_response, background_and_color, thumbnail_requirements=[160, 256], forbidden_patterns
source, source_sha256, output, operation
```

写好 UTF-8 计划后，先做 attempts=0 的独立 pending 登记；这次更新不得同时登记 attempt、候选、review 或 success：

```text
python scripts/update_manifest.py --manifest <任务目录/.xobi/manifest.json> --task-id <task_id> --status pending --worker-id <worker_id> --main-image-plan-json <.xobi/work/task_id-main-image-plan.json>
```

艺术指导必须满足：

- 单一视觉焦点，商品始终是第一阅读层；道具、背景和文字不得与商品争抢注意力。
- 商品完整、轮廓清楚、自然比例正确。按平台、品类和视觉方向冻结适当占比与安全边距；通用电商默认商品视觉包围盒沿主轴约占画布 `65%–85%`，最近安全边距不少于画布短边 `5%`，有合理理由时在 plan 中写明覆盖值。
- 使用完成目标所需的最少信息层。默认至多一个主文案层和一个辅助信息组；平台禁止营销文案或 `text_policy=no_text` 时不添加任何标题、说明、角标或伪字，但仍保留内容锁要求的商品/包装本体品牌与印刷。任何新增主图文案都必须来自源图已确认事实或 `user_exact`。
- 商品尺度、透视和场景关系可信；不得为了填满画布拉伸、压扁、局部放大或伪造不可见结构。
- 材质必须有与品类一致的纹理尺度、粗糙度、反射、透射或织物细节；避免塑料化、蜡感、过度磨皮、重复纹理和无依据的高光。
- 主光方向、明暗层级、接触阴影、轮廓分离和反射一致；商品不得漂浮，阴影不得脏、双重或与光源矛盾。
- 背景干净，主体与背景有足够明度或色彩分离；保持受控饱和度与高光，不用过曝、脏灰或模板化渐变掩盖构图问题。
- 保持原比例缩小到最长边 `256px` 与 `160px` 时，仍能立即识别商品、单一焦点和主轮廓；保留文字时，主文案必须可读，辅助信息不得成为理解主图的前提。

无论视觉方向为何，都禁止廉价黄黑促销条、随机红角标、粗描边、椭圆贴片、拥挤拼贴、廉价伪 3D 文字/按钮/漂浮图标、过饱和，以及任何编造的卖点、参数、认证、折扣、评分、赠品或伪字。

## 生成、验收与重试

使用 [prompts.md](prompts.md) 的 `PURE GENERATION COMMERCE MAIN IMAGE` 模板，每个 attempt 只生成当前 task 的一张完整候选。候选写到 manifest 为该 task 预分配的 final 路径，并必须同时通过三档视觉验收：全尺寸、保持比例长边 `256px`、保持比例长边 `160px`；缩略图只用于检查，不得反向覆盖或替代原始成品。

先运行 `prepare`，从当前候选原始字节确定性生成全尺寸/长边 256/长边 160 三档证据、`evidence.json` 和已绑定的 `assessment-template.json`。全尺寸证据是独立目录内的 `full-original<原后缀>` 原始字节快照，不再指向会被下一次重试覆盖的 final 路径；两张缩略图只能从该快照确定性派生。再实际查看这三档，只填写模板中的逐档 `passed/notes`、评分、检查项、reviewer 和 notes；不得改 manifest/task/candidate/plan/各视图的路径、尺寸或 SHA-256。七个 `scores`（`visual_hierarchy`、`product_fidelity`、`material_realism`、`typography`、`spacing`、`commercial_polish`、`thumbnail_readability`）均为 1–5 整数且必须至少 4；`no_text` 时 `typography` 评价“无未授权营销字/伪字且锁定的本体印刷正确”，不能留空或写 N/A。六个 `required_checks`（`single_focal_point`、`product_priority`、`clear_hierarchy`、`safe_margins`、`realistic_scale_and_shadow`、`no_invented_claims`）必须全部为 `true`；八个 `hard_rejects`（`cheap_banner`、`random_badge`、`thick_outline`、`oval_sticker_collage`、`clutter`、`fake_3d`、`oversaturation`、`invented_claim`）必须全部为 `false`。模板中需要填写的评审字段如下，其余绑定字段原样保留：

```json
{
  "views": {"full": {"passed": true, "notes": ""}, "256": {"passed": true, "notes": ""}, "160": {"passed": true, "notes": ""}},
  "scores": {"visual_hierarchy": 4, "product_fidelity": 4, "material_realism": 4, "typography": 4, "spacing": 4, "commercial_polish": 4, "thumbnail_readability": 4},
  "required_checks": {"single_focal_point": true, "product_priority": true, "clear_hierarchy": true, "safe_margins": true, "realistic_scale_and_shadow": true, "no_invented_claims": true},
  "hard_rejects": {"cheap_banner": false, "random_badge": false, "thick_outline": false, "oval_sticker_collage": false, "clutter": false, "fake_3d": false, "oversaturation": false, "invented_claim": false},
  "reviewer": "visual-review",
  "notes": ""
}
```

两步命令固定如下；`prepare` 默认按 task 与 candidate 哈希创建新目录，`finalize` 会重新快照当前 candidate/plan，逐字节核对全尺寸快照并复算两张缩略图，拒绝旧评分、错图、动态图、多帧图、被改证据或覆盖已有 review：

```text
python scripts/create_main_image_review.py prepare --manifest <任务目录/.xobi/manifest.json> --task-id <task_id> --candidate <manifest预分配final路径> --plan-json <已冻结main_image_plan.json> [--output-dir <.xobi/work/独立证据目录>]
python scripts/create_main_image_review.py finalize --manifest <任务目录/.xobi/manifest.json> --task-id <task_id> --candidate <manifest预分配final路径> --plan-json <已冻结main_image_plan.json> --evidence-dir <prepare输出目录> --assessment-json <已填写的绑定模板.json> [--review-json <evidence-dir内review.json>]
```

`review.json` 必须绑定当前 candidate、冻结 plan、全尺寸快照、两张缩略图、`evidence.json` 和已填写 assessment 的路径与哈希。每个有候选的 attempt 都必须在同一次 update 中登记自己的 finalized review：审美失败绑定 `passed=false`，通过候选绑定 `passed=true`；不能只在最后成功时补一份 review，也不能手写分数 JSON 绕过 `prepare/finalize`。历史 attempt 的 review 和三档证据在候选被覆盖或终局归档后仍必须存在且可复算。

每个有候选的图片调用把 `--attempts` 在当前全局总值上严格加 1，并使用 `--attempt-stage commerce_main_image`。质量候选序号最多 3 个，但前面若发生基础设施 attempt，全局 attempt 数可以大于 3，不能把命令占位符误写成固定的 `1|2|3`。前两次质量失败保持 pending，第三次失败终结为 failed；普通主图通过时必须在同一个 success attempt 绑定通过的 review：

```text
python scripts/update_manifest.py --manifest <任务目录/.xobi/manifest.json> --task-id <task_id> --status <pending|failed> --worker-id <worker_id> --attempts <当前总attempt+1> --attempt-stage commerce_main_image --failure-type quality --error <精确审美失败原因> --main-image-quality-review-json <passed=false的review.json>
python scripts/update_manifest.py --manifest <任务目录/.xobi/manifest.json> --task-id <task_id> --status success --worker-id <worker_id> --output <manifest预分配final路径> --attempts <当前总attempt+1> --attempt-stage commerce_main_image --main-image-quality-review-json <通过的review.json>
```

后续还要添加 Logo 时，主图通过候选先以 pending attempt 同时绑定 `passed=true` review 与保存到 `.xobi/work` 的 `--base-output`；该 review 成为 accepted attempt 的冻结别名。该 accepted attempt 一经登记，`commerce_main_image` 图片阶段立即封口，后续只允许 Logo 流程，不能再追加任何主图质量或基础设施 attempt。Logo 的 direct overlay 或 conflict 阶段完成后沿用这份 review，不重新传主图 review，也不让最终 Logo 像素冒充被审过的无 Logo 主图：

```text
python scripts/update_manifest.py --manifest <任务目录/.xobi/manifest.json> --task-id <task_id> --status pending --worker-id <worker_id> --attempts <当前总attempt+1> --attempt-stage commerce_main_image --base-output <已验收无Logo主图base> --main-image-quality-review-json <passed=true的review.json>
```

内容正确但主图审美不合格仍属于 quality failure。单一焦点、商品优先、占比/安全边距、信息层级、材质、尺度、光影、背景分离、两档缩略图或禁用样式任一失败，都按精确失败原因从同一无参考纯生图阶段重试，不得放松商品与文字内容锁。沿用每图初次结果加最多 2 次针对性质量重试；第三次仍失败时任务终结为 failed，脚本把该拒绝候选连同原 SHA-256 归档到 `.xobi/work/rejected/` 并移出任务根目录，防止它被误交付；不得把“内容没错但很丑”的候选登记为 success。

## 批量 family pilot

批量按平台、比例、品类、视觉方向和文字策略划分 `main_image_family`。每个 family 先选择一张有代表性的内部 pilot，冻结共用艺术指导并完成全尺寸/长边 256/长边 160 三档验收；pilot 通过后，family 其余成员才可并行。pilot 是内部质量门禁，不要求用户逐张确认。成员只继承抽象艺术指导、商品占比区间、层级、光影和间距，不继承 pilot 图片、商品、精确文案或局部构图；pilot 三次仍不合格时停止该 family 并报告，其他已通过 family 可继续。
