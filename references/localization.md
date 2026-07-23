# fanyi 翻译规则

本文件恢复 `531e28e` 版本的 fanyi 翻译流程。翻译是参考图编辑，不是脱离原图重新创作，也不使用后来的整张无参考重建或文字框本地拼贴。

## 开工条件

- 必须确认目标图片、目标语言和输出比例。
- 只缺哪一项就只询问哪一项；信息完整后直接执行。
- 多图时一张源图对应一个 task、一个 worker 和一个输出，参考图、prompt、原始候选及最终文件不得跨 task 共用。
- 原图只读，禁止覆盖。

## 固定执行方式

1. 先查看当前源图，记录全部可见文字、商品、人物、背景、Logo、图标、色块、边框、布局、数量、尺寸、颜色和必须保持不变的区域。
2. 本地文件调用内置图片能力时，把当前源图作为唯一 `referenced_image_paths`；只有图片仅存在于当前对话时，才使用能覆盖该源图的最小 `num_last_images_to_include`。两种引用方式不得同时使用。
3. 每次调用只处理当前一张图，禁止附带另一张源图、失败候选、Logo 素材、风格图或最近会话中的无关图片。
4. 图片模型只翻译并替换源图已有文字，这是唯一允许的内容变化。返回的完整图片候选就是 fanyi 原始成品候选，不再运行 `compose_localization.py`，也不把它局部拼回原图。
5. 每次重试都重新使用原始源图，不把失败候选作为下一次参考，避免累计漂移。

## 提示词硬规则

每张图的 prompt 必须包含：

```text
Use the current source image as the sole authoritative edit reference.
Translate every visible source-language text block into {TARGET_LANGUAGE}.
Replace only text that already exists in the source image.

STRICT NO-ADDITION RULE: Do not add, invent, infer, complete, or hallucinate any new title, slogan, selling point, label, badge, parameter, footer, watermark, decoration, icon, object, or pseudo-text. Areas without text in the source must remain text-free.

TEXT SCOPE LOCK: Keep the same number, semantic scope, approximate position, hierarchy, color relationship, alignment, and reading order of text blocks. Preserve every number, model, quantity, size, currency, and unit meaning.

CONTENT LOCK: Keep the exact product, people, photos, Logo, icons, borders, color blocks, background, shadows, textures, quantities, crop, scale, positions, spacing, composition, and layout. Do not redesign, beautify, restyle, recolor, move, add, remove, or replace any non-text content.

PRODUCT GEOMETRY LOCK: Preserve natural aspect ratio, silhouette, thickness, component proportions, material texture, and perspective. Never stretch, squash, widen, narrow, flatten, elongate, or locally enlarge the product.

Output ratio: {RATIO}. This is a translation edit, not a redesign.
```

- 译文必须准确自然，不改变事实、数字、型号、尺寸、数量和单位。
- 用户提供精确目标文案时逐字照写，不得精简、润色、纠错或换词。
- 长译文依次采用自然换行、调整文字框、适度缩小字号；禁止压缩字宽、拉长字高、重叠、裁切、溢出或扭曲。
- 品牌字标、Logo、型号和包装本体印刷文字默认保持；只有用户明确要求时才翻译。
- 看不清的文字不得猜测；影响完整翻译时先询问该文字。

## 比例

- 用户要求保持原比例时，保持原画幅、裁切和布局。
- 用户明确要求新比例时，只允许为新画布做必要的自然重排和等比缩放；商品不得拉伸或压扁。
- `1:1` fanyi 任务默认沿用旧版最终交付规格：先保存原始翻译候选，再运行最终优化为 `800×800 JPG`、`900–1024KB`。用户明确要求其他尺寸、格式、透明背景、保持原格式或不压缩时，以用户要求为准。
- 非 `1:1` 不套用旧版方图压缩预设，只按用户确认的比例与格式交付。

## 批量与四路

批量翻译先运行：

```text
python scripts/preflight_fanyi.py --input <输入文件或目录> --target-suffix <目标语言> --workers 4 [--require-square]
```

- 默认最多四路，每个 worker 只处理分配给自己的图片，内部逐张串行。
- 宿主不支持并行或两个 worker 出现同类基础设施错误时，停止新并行调用并降为单路继续原任务清单。
- 成功图片不重跑，只重试当前失败图。
- 每张最多 3 个有候选质量尝试；无候选基础设施错误最多 4 次，按 2/5/10 秒退避。

## 最终压缩

只有 `1:1` 且用户未覆盖旧版交付规格时，在全部原始翻译候选验收通过后运行：

```text
python scripts/final_optimize_images.py --input <原始翻译候选目录> --output <最终输出目录> --size 800x800 --min-kb 900 --max-kb 1024
```

已有不合格成品需要重做时加 `--overwrite`。压缩后必须复核：

- 所有最终图片都是可解码 JPEG；
- 尺寸为 `800×800`；
- 文件大小为 `900–1024KB`；
- 图片数量与原始翻译候选一一对应；
- 没有漏图、重复、覆盖或把报告文件当图片交付；
- 压缩没有改变文字内容、商品比例或画面结构。

透明图、非 `1:1`、用户指定其他格式或尺寸时禁止套用此预设。

## 验收

逐图对照 source、原始翻译候选和最终压缩图：

- 原文均已翻译，目标语言、拼写、标点、数字和换行正确；
- 没有漏译、重复、新增、伪字或无中生有的卖点；
- 商品、人物、Logo、图标、背景、数量、颜色、轮廓、材质、位置、裁切、构图和版式未被擅自修改；
- 用户指定比例、格式和压缩规格全部满足；
- 原图仍完好且没有被覆盖。

不合格候选不得交付。最终只交付压缩后的成品目录或只含成品的 ZIP；原始候选、预检报告和压缩报告保留为内部记录。
