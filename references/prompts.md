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

## 强制总模板

以下是约束全集，用于组装当前图片的简短 prompt，不要整段原样提交。每张图生成时必须体现目标语言、构图、可见文字锁和“严禁新增文字”。

```text
Translate this product advertisement image into {TARGET_LANGUAGE}.

STRICT NO-ADDITION RULE: Translate only the visible text that already exists in the source image. Do not add, invent, infer, complete, or hallucinate any new text, slogans, selling points, labels, badges, icons, parameters, footer text, or decorative wording. Do not use the filename, SKU name, folder name, product category, or visual appearance to create text. If an area has no text in the original image, keep it completely text-free. Keep the number of text blocks and their positions the same as the original. Only replace existing text with its translation.

TEXT SCOPE LOCK: Before editing, identify the exact visible source text blocks in the image. Replace each existing source text block with one matching translated text block. Do not create additional text blocks. Do not add text to blank background, product areas, empty banners, empty icon areas, or corners. If the source image has only 3 text blocks, the output must have only those 3 translated text blocks.

Keep the same product, main composition, layout structure, colors, icons, label positions, selling-point areas, and e-commerce design style as close as possible to the original. Do not add extra products or change the product.

Improve only non-text visual quality: make the image clean, polished, premium, sharp, and ready for online marketplace use. Clean edges and corners. Remove unwanted white borders, black marks, dirty margins, crop leftovers, compression artifacts, and awkward blank areas through image generation. Keep the product and layout unchanged. These visual improvements must not introduce any new text.

Keep translations concise and readable. If target-language text is longer than the source area, shorten only within the same meaning, wrap naturally, or reduce font size slightly. Do not add explanations or extra benefits.

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
- 字号不能小到难以阅读。
- 不能横向拉伸、纵向压扁、倾斜变形来硬塞文字。
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
- 改变主要构图。
- 改变图标含义。
- 改变卖点区域。
- 用本地裁剪/拉伸去白边。
