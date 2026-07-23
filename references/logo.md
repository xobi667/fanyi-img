# Logo 添加唯一规则

本文件是 Logo 添加、冲突重排、尺寸计算和验收的唯一真源；若其他文件的职责化摘要、prompt 模板或验收清单与这里冲突，以本文件为准，不得另定相反规则。只有用户明确要求添加 Logo 并确认本次 `logo` 资产时才启用本文件；源图已有 Logo、盘点清单含 Logo 或只要求翻译/普通编辑都不构成 Logo 例外。Logo 添加是整个 skill 唯一允许本地确定性视觉叠加的功能；`logo_conflict` 是 Logo 流程中唯一允许原生图片模型接收参考图的阶段。Fanyi localization 使用源图参考属于独立翻译路线，不启用 Logo 流程。

## Logo 资产与标准尺寸

- 必须先确认用户本次指定的真实 Logo 资产和 `logo` 角色；只有用户明确要求“使用默认 Logo”时才可传 `--use-default-logo` 启用技能模板。缺少本次 Logo 时先询问，禁止静默沿用旧 Logo 或默认模板。记录原始路径和 SHA-256，禁止 AI 重绘、仿制、改字、改色、裁掉图形内容或拉伸；只允许按下一条清理外围冗余画布。
- Logo 有巨大外围白边、透明边或主体偏移时，先运行 `normalize_logo.py --dry-run`，只裁外围冗余画布；保留内部白底、黑底、翻页、阴影、文字和完整边缘。正式清理时同时传 `--manifest <.xobi/manifest.json>`，原子登记原始/规范化路径、两份 SHA-256、裁切框和设置；此后 plan、几何与叠加只能使用登记后的规范化 Logo。
- 全透明 Logo 必须拒绝。只要达到阈值的可见 alpha 覆盖完整画布（包括 254–255 近不透明），就无法自动区分“设计底板”和“外围底色”：`auto` 只做 dry-run 检查，确认外围为白色/纯色后显式选择 `--background white|solid`；若完整底板确属设计内容，叠加时才显式传 `--opaque-approved`。不得跳过这一步直接按整张方形画布缩放。
- 对每张最终画布使用 `scale = min(width, height) / 4000`。把清洗后的 Logo 等比 contain 进 `1036 x 309` 的 4000 短边参考框，再按同一 scale 缩放并锚定左上角 `(0,0)`。
- 同批统一使用相同 Logo、参考框、透明度阈值、安全边距和缩放公式；每张图片按自己的最终尺寸重新 dry-run，禁止复用其他分辨率的像素坐标。

## 两个区域与冲突判定

- `visible_bbox` 是 Logo 非透明像素真实覆盖范围，只用它判断原图是否会被实际遮挡。
- `safe_zone` 是 `visible_bbox` 外加舒适间距，只用于冲突重排后的布局和锚点；不得用它扩大冲突范围，也不得形成整条空白带。
- 只有信息承载模块与 `visible_bbox` 相交时才使用 `regenerate_for_conflict`。仅普通背景、无信息商品边缘或只进入 safe-zone 缓冲环时使用 `direct_overlay`，禁止多余重生。

信息承载模块包括文字块、角标、价格、参数、赠品小图、促销圆图、徽章、图标、人物脸部、二维码、条码以及其他影响理解或购买决策的视觉元素。有关联的小图、图标、底板和文字视为一个不可拆分模块；避让时必须整体移动。

## logo_plan.json

批量添加前必须看完全部原图，并在 `.xobi/work/logo_plan.json` 写入可追溯计划。所有 bbox 均为最终画布像素坐标 `[left, top, right, bottom]`：

```json
{
  "schema_version": 1,
  "logo": {"source": "...", "sha256": "...", "normalized": "...", "reference_box": [1036, 309], "reference_short_side": 4000},
  "items": [{
    "task_id": "task-000001", "source": "...", "final_size": [1254, 1254], "family_id": "family-01",
    "visible_bbox": [0, 0, 325, 96], "safe_zone": [0, 0, 350, 121],
    "modules": [{"id": "module-01", "type": "gift", "bbox": [0, 20, 280, 180], "members": ["thumbnail", "caption"]}],
    "conflicts": ["module-01"], "decision": "regenerate_for_conflict",
    "module_anchors": [{"module_id": "module-01", "placement": "below", "prepared_bbox": [0, 121, 280, 281]}],
    "family_reference": "task-000001", "base_approved": false, "final_approved": false
  }]
}
```

`decision` 只能是 `direct_overlay` 或 `regenerate_for_conflict`。`direct_overlay` 的 `module_anchors` 必须是空数组；`regenerate_for_conflict` 必须为每个 `conflicts` 模块写且只写一个锚点。锚点的 `module_id` 必须对应冲突模块，`placement` 只能是 `right` 或 `below`，`prepared_bbox` 必须记录该完整模块在 `prepared_base` 上的实际位置；它不得与 `safe_zone` 相交，左边或上边必须落在本图 dry-run 给出的 `right_module_start_range` 或 `below_module_start_range`。不得在完成逐图模块盘点、冲突判断和 family 分类前开始生成或叠加。

`regenerate_for_conflict` 还必须在 manifest item 登记 `conflict_reference_base`：它是最终画布尺寸、尚未叠加本次 active Logo、移动冲突模块之前已经批准的只读基底；源图原有 Logo 必须仍在其中。组合翻译任务使用已通过 fanyi 参考图翻译验收的完整候选；组合普通 edit 使用已通过 `pure_generation_edit` 验收的完整候选；组合纯文字 generate 使用已验收的生成结果。若用户只要求添加 Logo，不先执行纯生图 edit，源图或用户明确要求的确定性尺寸/格式转换结果就是基底。`prepared_base` 是移动后的基底，二者职责不同，禁止用同一张移动后图片伪装 reference，也不得跳过组合 edit/localization 的各自验收直接拿 source 代替其结果。

## Family pilot 与最多四路

- 将商品结构、文字层级、背景和促销模块基本一致的图片归入同一 `layout_family`；不同系列不得共享具体构图。
- 只有包含 `regenerate_for_conflict` 的 family 才先完成并验收一张 pilot，冻结标题方向、模块锚点、文字层级、商品尺度区间和间距节奏；同 family 的其他重排成员在冻结前不得启动。全为 `direct_overlay` 的 family 没有布局重构可冻结，完成逐图 dry-run、冲突判断和 plan 后可并行叠加。
- 冻结后，其余互不重复的任务最多四路并行，实际 worker 数服从宿主并发限制。每个任务只归属一个 worker，只产生一个最终输出。
- Family 锁定宏观布局关系，不强迫不同文字长度使用完全相同的像素坐标；必要时建立有记录的 variant，但不得随机换布局策略。

存在冲突重排的 family 必须在 pilot 成功时登记 `.xobi/work/layout_families.json`；登记后锁与文件 SHA-256 冻结，后续成员不得改写：

```json
{
  "schema_version": 1,
  "families": [{
    "family_id": "family-01",
    "members": ["task-000001", "task-000002"],
    "pilot_task_id": "task-000001",
    "requires_pilot": true,
    "pilot_approved": true,
    "pilot_output_sha256": "<pilot final SHA-256>",
    "lock": {
      "title_direction": "horizontal",
      "module_anchor": "below-safe-zone",
      "type_hierarchy": "title>benefit>detail",
      "product_scale_range": "0.72-0.78 canvas width",
      "module_spacing": "one safe-zone gap"
    },
    "variants": []
  }]
}
```

`members` 必须与 logo plan 中该 family 的任务精确一致；pilot 必须本身是 `regenerate_for_conflict`，所有重排成员的 `family_reference` 必须指向它。`pilot_output_sha256` 必须等于已验收 pilot 成品，五个 `lock` 字段不可为空；差异只能记录在 `variants`，不能偷偷换 pilot 或锁。

## 生成、叠加与验收

1. `direct_overlay` 不调用生图工具，直接在合格底图上确定性叠加真实 Logo。
2. `regenerate_for_conflict` 进入 `attempt_stage=logo_conflict`：这是任何 generate/edit/localization 组合的 Logo 阶段中唯一可把图片传给原生图片模型的参考编辑例外，只传当前 `conflict_reference_base`，不得传 source、pilot、其他任务图片或额外参考。宿主必须支持局部/蒙版式参考编辑并能把可变区域限制在原/目标模块 ROI 加固定 2px 羽化内；不支持时直接报告该冲突任务不可执行，不白耗三次全图生成。它只重构发生冲突的信息模块，完整保留底图所有文字、商品、原有 Logo、图标、数量和促销信息。验证器必须逐模块复算：原 bbox 已实质清除、`prepared_bbox` 存在对应模块、多模块一一匹配且没有交换或复制未移动；同时只允许每个原 bbox、目标 bbox 及固定 2px 羽化边界内变化，其他解码 RGBA 像素逐像素相同。移动前后画布尺寸不同而又没有可重算全画布映射时直接 fail closed。仅改无关像素、手写合法 anchor 或伪造 `passed` 一律不能成功。验证结果保存为带 reference/prepared SHA-256 的 `logo_relocation_validation`，最终 verify 必须重新计算并完全一致。禁止用本地缩小整图、整体平移、补边、模糊背景、顶栏或底板腾位置。
3. 每次 `logo_conflict` 调用只要返回可读取候选，就用当前全局 attempt 加 1 登记一次质量候选，并显式传入该次唯一的 `--prepared-base`。候选必须保存在 `.xobi/work`，各 attempt 不得复用路径或 SHA-256；manifest 为每次候选冻结 `candidate_path`、`candidate_sha256`、`candidate_width`、`candidate_height` 并在后续 verify 重新读取，历史失败候选不得删除或覆盖。最多 3 个质量候选；没有候选的基础设施 attempt 最多 4 个，且禁止携带 `prepared_base`、output、layout family、style lock 或任何候选产物。任何 `failure_type=None` 的 accepted 候选必须在同一次 update 复算通过完整 relocation/pixel-lock，并把完全相同的 `logo_relocation_validation` 同时绑定到 item 和 attempt；pending 也不例外，不能拖到最终 success 才验。候选一旦验收通过，`logo_conflict` 阶段立即封口，最终确定性叠加不再增加图片 attempt。
4. 把冲突模块自然放在 Logo 右侧或下方。最近信息模块的可见边缘紧邻 safe-zone 锚点，不增加第二段空白；safe zone 只能呈现自然背景，不能画出边框、白框、色块或占位符。
5. 查看尚未叠加本次 active Logo 的底图：`direct_overlay` 只需确认任何信息模块都不与 `visible_bbox` 相交，进入 safe-zone 缓冲环本身不算冲突；`regenerate_for_conflict` 只要求被移动的冲突模块落到 `safe_zone` 外并保持舒适锚点。普通背景或无信息商品边缘可以保留在其中，再执行 `apply_logo.py --safe-zone-approved`。
6. Logo 叠加是最后一次视觉修改；之后禁止再次交给 AI。需要调整尺寸、格式或有损体积时先处理 `prepared_base`，再做最后叠加；叠加后只允许保持解码像素完全相同的无损容器/元数据处理，JPEG/WebP 不得再次有损重编码。

必须完成 source/conflict_reference_base/prepared_base/final 分阶段验收；其中 Logo 的移动前基底记录为 `conflict_reference_base`，移动后基底记录为 `prepared_base`，组合翻译任务另保留 `localized_base`：

- `source -> localized_base/base_output -> conflict_reference_base`：组合任务中的 `localized_base` 或 `base_output` 是已通过严格内容锁验收的完整纯生图候选，不要求也不得伪称由 source 确定性像素合成；纯 Logo-only 任务可直接从 source/显式转换结果进入。进入 Logo 阶段前不得提前移动冲突模块或加入本次 active Logo。
- `conflict_reference_base -> prepared_base`：只移动 plan 中真实冲突模块，原位清除、目标位对应；除原/目标模块 bbox 的固定 2px 羽化边界外，其他 RGBA 像素必须逐像素相同，商品、背景、其他文字、图标、赠品、徽章和标签不得新增、遗漏、重复或漂移。
- `prepared_base -> final`：除真实 Logo 像素叠加外没有其他视觉变化。
- `final`：Logo 来源、尺寸、纵横比、位置、颜色和透明度正确，不遮挡任何信息模块；同 family 横向比较保持同一布局逻辑。

把 source/conflict_reference_base/prepared_base/final 对照图、组合任务的 localized_base、family 联系表、逐模块 relocation validation、失败项和最终批准状态保存在 `.xobi/work/`，任务根目录只放最终成品。
