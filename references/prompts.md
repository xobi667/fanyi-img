# fanyi Prompt Rules

## 默认直接纯生图

默认先查看当前源图，再调用 Codex 直接纯生图，不传 `referenced_image_paths`。只有用户明确要求编辑原图时才使用参考图编辑。

组装 prompt 前必须已经确认输出比例。比例不明确时停止，不得生成；不要在 prompt 中自行选择方图、竖图或原比例。

单次 prompt 保持简短，通常包含：

```text
{比例和目标语言} e-commerce ad showing {当前图的商品与主要构图}.
Text blocks exactly: “{目标语言文字1}”, “{目标语言文字2}”...
Exactly these text blocks, no other text, no watermark.
```

- 必须先从源图建立文字锁，再写目标语言文字；不能根据文件名补文案。
- 复杂多步骤图可以逐项列出步骤，但不要粘贴整份长规则。
- 一个请求只生成一个 task_id；不同图片不得共用 prompt 或会话图片。
- 生成成功后立即把 Codex 返回文件复制到该任务的原始生图输出路径，并保留生成器原文件。

## 用户要求统一时：批量视觉系统锁

只有用户语义明确要求整批视觉、排版或系列风格统一时启用。单独出现“保持一致”不能自动触发；语义不清时先询问。开始第一张生图前，从整批源图建立规范并保存到 `RAW_OUTPUT_DIR/batch_style_lock.json`：

```json
{
  "version": 1,
  "canvas": {"ratio": "用户确认值", "width": 0, "height": 0},
  "typography": {
    "family_style": "共同字体视觉风格",
    "title_weight": "共同标题字重",
    "title_size_range": "共同标题字号范围",
    "body_size_range": "共同正文字号范围",
    "alignment": "共同对齐方式",
    "line_spacing": "共同行距范围"
  },
  "layout": {
    "title_zone": "共同标题区域",
    "body_zone": "共同正文区域",
    "outer_margin": "共同外边距范围",
    "module_spacing": "共同模块间距范围"
  },
  "palette": ["共同主色", "共同辅助色", "共同背景色"],
  "label_style": "共同图标、标签和徽章风格",
  "product_scale_range": "商品占画布的自然视觉尺度范围",
  "reference_source": "用于提炼抽象风格的代表图相对路径或 null",
  "content_isolation": true
}
```

把 `width`、`height` 和所有描述性占位值替换为本批确认后的真实值，使用 UTF-8 写入并原子替换目标文件。`content_isolation` 必须保持为 `true`。不得在 JSON 中保存任何单图译文、参数、商品内容或会话图片。

每张图的简短 prompt 还必须加入：

```text
Use the frozen BATCH_STYLE_LOCK for this batch. Keep typography, hierarchy,
alignment, margins, spacing, color palette, label style, and natural product
display scale consistent with the other images. Preserve this source image's
own information structure. Do not create a new layout style for this image.
```

强制边界：

- “统一”是统一视觉语言，不是让所有图片拥有相同文案、相同商品或完全相同构图。
- 每张图仍使用独立文字锁、商品几何锁和 task_id。
- 不得把上一张图的文字、参数、商品、图标或卖点带入下一张。
- 不得为了统一而新增/删除文字块、改变商品结构或强行套模板。
- 多 worker 时由主协调者生成一次 `BATCH_STYLE_LOCK` 并原样下发；worker 不得自行改写规范。
- prompt 中只摘录当前任务需要的锁定字段；风格锁原件以 `batch_style_lock.json` 为准。
- 可用一张代表图建立抽象视觉基准，但不得把代表图的商品、文字、参数或会话图片共享给其他任务。纯提示词只能追求稳定的视觉近似，不能宣称字体像素级完全一致。
- 用户没有明确要求统一时，不强制套批量版式，优先忠实保留各源图版式。

## 强制总模板

以下是约束全集，用于组装当前图片的简短 prompt，不要整段原样提交。每张图生成时必须体现目标语言、构图、可见文字锁和“严禁新增文字”。

```text
Translate this product advertisement image into {TARGET_LANGUAGE}.

STRICT NO-ADDITION RULE: Translate only the visible text that already exists in the source image. Do not add, invent, infer, complete, or hallucinate any new text, slogans, selling points, labels, badges, icons, parameters, footer text, or decorative wording. Do not use the filename, SKU name, folder name, product category, or visual appearance to create text. If an area has no text in the original image, keep it completely text-free. Keep the number of text blocks and their positions the same as the original. Only replace existing text with its translation.

TEXT SCOPE LOCK: Before editing, identify the exact visible source text blocks in the image. Replace each existing source text block with one matching translated text block. Do not create additional text blocks. Do not add text to blank background, product areas, empty banners, empty icon areas, or corners. If the source image has only 3 text blocks, the output must have only those 3 translated text blocks.

Keep the same product, main composition, layout structure, colors, icons, label positions, selling-point areas, and e-commerce design style as close as possible to the original. Do not add extra products or change the product.

PRODUCT GEOMETRY LOCK: Preserve the product's natural aspect ratio, silhouette, thickness, width-to-height relationship, component proportions, material texture, and perspective. Never stretch, squash, widen, narrow, flatten, elongate, or locally enlarge the product to fill the requested canvas. Adapt to a new ratio only by extending text-free background, adding balanced breathing room, or recomposing the unchanged product at a natural scale.

Improve only non-text visual quality: make the image clean, polished, premium, sharp, and ready for online marketplace use. Clean edges and corners. Remove unwanted white borders, black marks, dirty margins, crop leftovers, compression artifacts, and awkward blank areas through image generation. Keep the product and layout unchanged. These visual improvements must not introduce any new text.

Keep translations concise and readable. If target-language text is longer than the source area, use this order: shorten only within the same meaning, wrap naturally, adjust the text box, then reduce font size slightly while keeping it readable. Never compress glyph width, stretch glyph height, tighten spacing until letters touch, overlap text, clip text, or distort typography. Do not add explanations or extra benefits.

Absolutely avoid tiny unreadable text. Do not generate micro footer text, dense paragraphs, disclaimers, or decorative wording unless such text visibly exists in the source image.

For icon selling-point areas, translate only the icon text that visibly exists in the source image. If an icon area has no text, keep it text-free. Do not invent icon labels.

Keep fonts natural, stylish, and readable. Do not stretch, squeeze, warp, or distort text. Match the original commercial typography hierarchy: bold titles remain bold, labels stay inside labels, badges stay inside badges, and body text remains clean and readable.

For Thai text, use a modern Thai e-commerce typography style, not a plain default system font. Thai titles should look designed, clean, rounded, and bold when appropriate. Body text should be clear and well-spaced.

Preserve all visible quantities, units, dimensions, model numbers, thickness, width, length, colors, and package counts that are actually written in the source image. Do not add any parameter that is not visibly written.

{RATIO_REQUIREMENT_IF_ANY}

Use these translation pairs/glossary only when the corresponding source text visibly appears in the image:
{TRANSLATION_PAIRS}
```

## 1:1 要求

如果用户要求 1:1，加入：

```text
Output must be a square 1:1 e-commerce image. Fill the canvas cleanly without unwanted white borders or empty margins. Do not crop, stretch, or distort the product.
```

更完整版本：

```text
Output must be a square 1:1 e-commerce image. You may extend the non-text visual background/product scene to fill the square canvas cleanly. Do not crop, stretch, or distort the product. Do not add any new text, SVG text, icon labels, slogans, parameters, or selling points while expanding. Blank/no-text areas in the source must remain text-free.
```

## 任意比例转换要求

只要目标比例与源图不同（包括 16:9、9:16、4:5、3:4 等），prompt 必须加入：

```text
Output must use the requested {TARGET_RATIO} canvas. Preserve the product's original natural proportions and geometry exactly: no stretching, squashing, widening, narrowing, flattening, elongating, or local deformation. Fit the new canvas by extending only text-free background, adding balanced negative space, or naturally recomposing the unchanged product. Preserve natural text glyph proportions. If translated text is long, shorten within the same meaning, wrap naturally, adjust the text box, or reduce font size slightly; never squeeze, stretch, overlap, clip, crowd, or warp the text.
```

“锁定产品尺寸”表示锁定商品相对比例和自然视觉尺度，不要求跨画布保留相同像素宽高。允许为构图整体等比缩放商品，但禁止非等比缩放和局部变形。

## 无文字商品图 / 空白图 prompt

如果当前图片是无文字商品图、纯产品图、素材图、空白图、纯色图、透明图或无文案占位图，且需要按比例或修复瑕疵，prompt 必须改为类似：

```text
This source image has no visible text. Keep it completely text-free. Do not add any words, labels, slogans, icons, badges, parameters, or decorative text. Optimize only the non-text visual canvas: clean black marks, dirty edges, crop leftovers, uneven borders, transparent edges, compression artifacts, and awkward margins. Output must follow the requested ratio exactly, such as square 1:1 when required. Preserve the blank/no-text placeholder meaning.
```

如果当前图片是无文字商品图/纯产品图，还必须追加：

```text
Preserve the actual product, material texture, shape, and commercial photo style. If the source is a tall product photo, collage, or close-up detail, compose it into the requested ratio cleanly by extending only non-text background or product-safe visual area. Do not invent packaging, labels, icons, feature text, specifications, or promotional elements.
```

## 禁止诱导新增文案

禁止使用任何会诱导新增文案的 prompt 句子，例如：

```text
Prefer readable phrases such as ...
Use short punchy phrases...
Add marketplace-style labels...
Complete missing selling points...
Make icon labels clearer...
```

除非原图有对应文字，否则一律不能生成。

## 文字排版与可读性

核心原则：

```text
只翻译原图已有文字；少一点字可以，但绝对不能多出原图没有的意思。
```

- 不能为了完整逐字翻译，把字做得极小、很挤、重叠或超出区域。
- 如果目标语言比中文更长，允许换行、分段、调整行距、适当缩小字号。
- 处理长译文时按固定优先级执行：同义精简 -> 自然换行 -> 调整文字框 -> 适度缩小字号；任一步都不得损失核心含义或可读性。
- 字号不能小到难以阅读。
- 不能横向压缩/拉伸字形、纵向压扁/拉长字形、把字距挤到粘连、倾斜变形、重叠或裁切来硬塞文字。
- 空间不足时，只能在同一原文含义内压缩表达，不能补充新卖点、新解释、新参数。
- 必须保留原图已经写出的核心卖点、关键信息、卖点数量、重要参数、规格、数量、尺寸。
- 不允许把关键卖点直接删没。
- 不允许把原图没有写出的卖点补上去。
- 不允许把短中文扩写成带解释的长目标语言文案。

允许和禁止示例：

```text
允许：遮光好 -> Excellent Light Blocking / Good Light Blocking
允许：卷收设计 -> Roll-Up Design
允许：黑色包边 -> Black Edge Trim
禁止：把“遮光好 / 卷收设计 / 黑色包边”扩展成 UV Protection、Heat Insulation、Privacy Protection、Static Cling、Cut to Size 等原图没有的卖点。
```

图标卖点区：

- 只有原图图标旁边/下面本来有文字，才翻译这些文字。
- 原图图标没有文字，就不能加图标文字。
- 每个卖点建议 1-2 行，尽量短词组。
- 不要用很长句，不要加解释。

主标题：

- 只翻译原主标题已有内容。
- 不准新增副标题；只有原图本来有副标题时才翻译副标题。

## 画面质感与边角优化

生图 prompt 必须要求：

- 清理不必要白边。
- 清理黑点、小黑块、脏边。
- 清理裁切残留和边角瑕疵。
- 减少压缩噪点、模糊感。
- 让边角完整干净。
- 画面更清晰、更高级、更适合主图/详情页。

禁止：

- 改变商品本体。
- 改变商品纵横比、轮廓、厚薄、长宽关系、部件比例或透视；禁止通过非等比拉伸商品填满目标比例。
- 改变主要构图。
- 改变图标含义。
- 改变卖点区域。
- 用本地裁剪/拉伸去白边。
