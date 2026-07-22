# localization 唯一规则

本文件是跨语言图片翻译的唯一真源。默认执行模式固定为 `pure_generation_localization`：查看源图、冻结文字与内容清单，然后调用当前宿主的原生图片模型从零生成整张候选。模型调用不传源图、参考图、最近会话图片或任何参考图参数。默认纯生图不需要额外授权或许可。纯生图只规定执行方式，不扩大修改权限；唯一允许的内容变化仍是把原图已有文字替换为准确译文。

## 开工条件

- 确认目标图、目标语言、输出比例或“保持原比例”。
- 多图时先确认每张图片的角色；一个 task 只使用自己源图的盘点结果，禁止跨任务串图、串文案或串构图。
- 门禁前只读查看源图、尺寸、格式和全部可见文字；信息不全时先询问，不创建正式任务或调用图片模型。
- 目标比例与源图不同时，把它视为用户明确点名的额外画布适配；用户只说“翻译”而未明确新比例时不得自行改画幅。

## 默认执行：pure_generation_localization

1. 查看当前源图，逐块记录原文、准确译文、位置、层级、颜色、字体视觉、对齐、换行、数字、型号、单位及其关联图标；同时盘点商品、照片、人物、Logo、图标、徽章、边框、色块、背景、阴影、纹理、装饰、数量、顺序和相对关系。
2. 在第一次图片调用前冻结逐图 `localization_plan`。用户给出精确目标文案时标记 `target_text_source=user_exact`，逐 Unicode 字符锁定，禁止精简、润色、纠错或改写。
3. 根据该图的完整盘点组装独立纯生图 prompt。不得把源图作为 target、reference、attachment、最近会话图片或其他隐式参考输入；不得引用另一 task 的图片或 prompt。
4. 每次调用只生成一张完整候选并保存到当前 task 隔离的 `.xobi/work/`。候选通过验收后，其完整视觉内容直接成为 final；允许复制、移动、重命名和写 manifest，但禁止本地蒙版、裁贴、文字框合成、局部像素回填或二次 AI 编辑。
5. 任何未授权变化都判质量失败，并从同一个 `pure_generation_localization` 阶段重新生成；不得切到参考编辑，也不得交付“文字对了但商品、背景或版式变了”的结果。

## 翻译改动边界

用户说“翻译”时，以下规则同时生效：

- 只替换源图中已经存在且盘点为 `translatable_text` 的可见文案；文字块数量、语义范围、阅读顺序和信息层级一一对应，无字区域继续无字。
- 商品、照片、人物、数量、轮廓、材质、颜色、视角、背景、图标、徽章、Logo、品牌、边框、色块、阴影、纹理、促销结构、装饰和所有未点名内容必须保持。
- 非文字模块的位置、尺度、间距、裁切、构图、版式和相互关系必须保持。纯生图不是美化、改版或重新设计的授权。
- 禁止新增、推断、补全、删除或合并卖点、参数、型号、单位、角标、品牌、装饰文字、水印和伪字；不得根据文件名、目录名、商品外观或常识创造文案。
- 译文只可在原文字模块内做必要的自然换行、字距和字号适配；不得移动文字模块、扩大或新增底板、挤压字形、拉伸字形、移动非文字元素、遮挡图标、重叠、裁切、溢出或缩小到不可读。
- 普通标题、说明、促销和参数覆盖文案默认可翻译；品牌字标、Logo、型号、产品专名及商品或包装照片内部印刷文字默认锁定，只有用户明确点名后才翻译。
- 文字模糊、遮挡或分辨率不足以可靠识别时不得猜测。影响“全部翻译”时开工前只追问该块；无法确认时记录为 `unresolved_text`、保持原样并在报告列明。

`pure_generation_localization` 是整张纯生图，因此不能承诺模型输出与源图框外像素逐值相同；但任何可见、语义、数量、几何、颜色、位置、背景或版式变化仍属于不合格。验收的职责是拒绝并重试不合格候选，不是用本地编辑把它修成合格。

## 输出比例

- 保持原比例时，画布、裁切和布局必须保持；纯生图 prompt 必须锁定源图宽高比、构图、元素位置和尺度关系，不得借翻译改画布。
- 用户明确指定相同宽高比的新精确尺寸时，优先让原生图片模型直接生成该规格；宿主做不到时报告限制，不默认运行本地重采样。
- 用户明确指定新宽高比时，`ratio_adaptation.required=true`，`allowed_changes` 只能是 `minimal_canvas_adaptation`、`proportional_subject_scaling` 和 `necessary_text_reflow`。这项要求不授权改商品形状、背景风格、信息数量或视觉层级；保持商品自然比例和完整轮廓，以最小画布适配完成新画幅。如“翻译不改版式”和新比例无法同时满足，开工前说明冲突并让用户选择，不能静默决定。
- 禁止拉伸、压扁、局部放大商品或用整条空白带、顶栏、底板掩盖布局问题。

## localization_plan

每张图都必须在 attempts=0 时登记并冻结独立 `localization_plan`，文件使用 `.xobi/work/<task_id>-localization-plan.json`，最少包含：

```json
{
  "task_id": "task-000001",
  "mode": "pure_generation_localization",
  "reference_policy": "none",
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
      "source": "...",
      "translation": "...",
      "target_text_source": "translated",
      "requested_target_text": null,
      "role": "heading",
      "source_bbox": [40, 30, 420, 120],
      "target_bbox": [40, 30, 420, 120],
      "text_layout_adaptation": {"required": false, "reason": null, "target_alignment": null, "writing_direction": null},
      "protected_non_text_regions": []
    }
  ],
  "unresolved_text": [],
  "non_text_inventory": [
    {"id": "product-01", "kind": "element", "scope": "region", "bbox": [120, 260, 1080, 1160]},
    {"id": "background", "kind": "background_surface", "scope": "canvas", "bbox": null}
  ],
  "content_lock": {
    "products": [],
    "photos": [],
    "people": [],
    "logos": [],
    "icons_badges": [],
    "borders_color_blocks": [],
    "background": "...",
    "layout_relationships": []
  },
  "allowed_changes": ["replace_existing_text_only"]
}
```

计划文件必须绑定当前 manifest、task、source 路径、SHA-256 和尺寸，不能与另一 task 共用。`target_size`、`size_resample` 和 `ratio_adaptation` 记录已确认输出几何；保持原比例时 `ratio_adaptation={"required":false,"allowed_changes":[]}`，用户明确新比例时只能登记 `minimal_canvas_adaptation`、`proportional_subject_scaling`、`necessary_text_reflow` 三项。新任务默认让原生模型直接满足规格，不得把这些字段解释为默认本地修改许可。`source_bbox`、`target_bbox`、`protected_non_text_regions`、`non_text_inventory` 与 `content_lock` 只用于组装 prompt 和对照验收，不是蒙版、局部合成或参考编辑授权。文字块数量、译文、顺序、角色、位置及内容锁在第一次图片调用前冻结；候选返回后不得为了包容错误结果而删减清单、扩大授权或改写译文。

提示词必须使用 [prompts.md](prompts.md) 的 `PURE GENERATION LOCALIZATION` 模板，并明确写出 `REFERENCE INPUT: NONE`、逐块精确译文、完整内容锁和严格无新增规则。

## 验收与重试

- 每次尝试都使用冻结的源图盘点和计划重新纯生图，不把失败候选作为参考或下一次输入。
- 逐字核对语言、拼写、标点、数字、币种、型号、数量、尺寸、单位和换行；确认无漏译、重复、新增、乱码或伪字。
- 把 source/candidate/final 并排查看，逐项核对所有商品、照片、人物、图标、Logo、徽章、边框、色块、背景、阴影、纹理、数量、顺序、位置、尺度、构图和版式。任何未点名变化直接失败。
- 每次图片调用连续登记唯一 attempt。计划模式是 `pure_generation_localization`，每次调用登记 `attempt_stage=pure_generation`；返回候选即占用一次质量尝试。初次结果后最多 2 次针对性重试，每图总计最多 3 次。三次都不合格就停止并报告具体失败项，不得登记第 4 次成功。
- 没有候选的限流、连接、附件或落盘错误属于 infrastructure attempt；初次调用后最多重试 3 次，按 2/5/10 秒退避，共最多 4 次，不占质量预算。
- 不存在先做 3 次 reference edit 再申请纯生图许可的流程；纯生图就是默认阶段，也不得在失败后降级到本地文字框拼贴。

## Logo 组合任务

翻译候选通过上述验收后，把它登记为“尚未叠加本次 active Logo”的 `localized_base`；源图原有 Logo 必须仍在其中。若本次 active Logo 不遮挡信息模块，直接按 [logo.md](logo.md) 最后一步本地确定性叠加；若会遮挡，才允许进入 Logo 专属 `logo_conflict` 参考编辑/局部重排例外，先得到合格 `prepared_base`，再确定性叠加。Logo 叠加后禁止再次交给 AI。

## 旧版兼容

`text_only_reference_edit`、`pure_rebuild_approval`、`compose_localization.py`、文字框像素拼贴和 composition provenance 只允许离线读取、验证、诊断或导出旧 manifest，不是可继续执行的图片流程。旧 manifest 不得再发起新的 reference-edit/pure-rebuild 图片调用；需要继续时先迁移到当前 `pure_generation_localization`。新任务不得运行 `compose_localization.py` 修改候选，不得因为旧字段仍存在就恢复“参考编辑失败三次才允许纯生图”的逻辑。
