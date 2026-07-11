---
name: fanyi
description: Codex 专属商品图翻译技能，必须使用 Codex 内置生图/图片编辑能力，把商品图片中的中文翻译成英语、泰语、印尼语等目标语言，并生成电商成品图；翻译全部完成后默认做最终图片优化，输出 800x800 JPG 且文件体积控制在 900KB-1024KB。适用于单张图片、整个文件夹、主图/详情/sku 批量翻译、空白图/无文字图按比例优化、1:1 方图、画质优化、边角清理、字体排版优化、最终压缩等场景。
---

# fanyi

## 核心目标

当用户要求翻译商品图、主图、详情图、SKU 图、整个文件夹，或明确说“使用生图翻译”时，使用本 skill。

本 skill 的目标不是只翻译文字，而是：

```text
把中文商品图通过 Codex 内置生图/图片编辑能力重生为目标语言电商成品图。
保持商品、构图、布局、颜色、图标含义和卖点区域基本不变，同时优化文字、字体、边角、画质、比例和整体电商质感。
```

除非用户明确说“只要文字翻译，不要生图”，否则必须调用 Codex 内置生图/图片编辑能力生成图片。

本 skill 是 Codex 专属流程。唯一允许的生图通道是 Codex 内置生图/图片编辑能力，不配置、不读取、不调用任何外部图片编辑服务。

翻译、生图、质检全部完成后，默认还必须执行最终图片优化：输出 800x800 JPG，文件体积控制在 900KB-1024KB。只有用户明确说“不压缩 / 不要最终优化 / 保留原尺寸原体积”时，才跳过这一步。

---

## 死命令：只翻译原图已有文字，严禁自由发挥

这是本 skill 的最高优先级规则，任何其它“优化、美化、电商质感、可读性、词库、SKU、文件名、产品理解”规则都不能覆盖它。

```text
图中有什么文字，就只翻译什么文字。
图中没有的文字，一律不准生成。
原图无文字区域，必须保持无字。
严禁创新，严禁补充，严禁联想，严禁根据产品外观/文件名/SKU/目录名生成新卖点。
```

强制禁止：

- 不允许新增任何原图不存在的标题、副标题、卖点、图标文字、参数、标签、角标、徽章、装饰文案、说明文字、页脚小字。
- 不允许把文件名、SKU 名、目录名、商品名、图片路径中的词加入画面。例如文件名里有“卷收-双面铝箔-黑色包边”，但原图只写“遮光好 / 卷收设计 / 黑色包边”，则只能翻译这三个原图文字，不准新增 `UV Protection`、`Heat Insulation`、`Privacy Protection`、`Static Cling`、`Cut to Size` 等原图没有的内容。
- 不允许根据产品看起来像什么来补卖点。看见隔热膜、门帘、铝箔、窗户、图标，也不能自动添加防晒、隔热、隐私、防虫、透气、环保等原图没有写出的词。
- 不允许因为“电商图更完整”而补齐图标区、补横幅、补参数表、补营销口号。
- 不允许把空白区域变成有字区域；原图空白就必须无字。
- 不允许把原图 3 个文字块扩展成 5 个、6 个或更多文字块。
- 不允许把一个短词翻译成长句。能短就短，必须控制在原文字块原本的信息量内。
- 不允许使用“优先可读性”作为借口删改或增加信息；只能在同一含义内缩短目标语言表达。
- 不允许使用词库主动创造文案；词库只允许在原图确实出现对应中文词时用于选词。

执行时必须在 prompt 最前面写入同等强度的英文禁令：

```text
STRICT NO-ADDITION RULE: Translate only the visible text that already exists in the source image. Do not add, invent, infer, complete, or hallucinate any new text, slogans, selling points, labels, badges, icons, parameters, footer text, or decorative wording. Do not use the filename, SKU name, folder name, product category, or visual appearance to create text. If an area has no text in the original image, keep it completely text-free. Keep the number of text blocks and their positions the same as the original. Only replace existing text with its translation.
```

---

## 死命令：必须一张一张顺序翻译，严禁串线混图

这是批量翻译的最高优先级执行规则，任何“加速、并发、批量效率、失败重试”都不能覆盖它。

```text
必须一张一张翻译。
翻译完当前这一张，确认保存完成，再翻译下一张。
每次生图请求只能处理一张源图。
当前 prompt 只能描述当前这一张图，不能包含其它图片的信息。
严禁多张并发，严禁多图同传，严禁把两张或十几张图片内容混合到一张结果图里。
```

强制要求：

- 默认 `MAX_WORKERS = 1`，不允许为了速度改成 2、5、10 或更高。
- 批量处理必须按文件列表顺序串行执行：第 1 张完成并保存后，才开始第 2 张。
- 每次 Codex 生图请求只能传入当前这一张源图。
- prompt 中不得出现其它文件名、其它图片路径、其它图片文字、上一张/下一张的内容汇总。
- 不允许把多张图片拼在一起、合成到一起、参考到一起、对照到一起生成。
- 不允许开启线程池、进程池、异步并发队列、批量同时发起生图请求。
- 如果 Codex 生图工具或模型出现把多张图内容串线/混合的迹象，必须停止并发思路，继续保持单张串行补跑。
- 失败重试也必须单张内部重试完成后再进入下一张，不能把失败图片和后续图片一起跑。

脚本实现必须使用类似逻辑：

```text
for image in images:
    process_one_image(image)
    save_result(image)
    then continue to next image
```

禁止使用类似逻辑：

```text
ThreadPoolExecutor
ProcessPoolExecutor
asyncio.gather
parallel map
批量把多张图同时发起生图请求
```

---

## 强制质检：先锁定原图文字清单，输出多字/串图直接判失败

为了防止模型自由发挥、把其它图片内容串进来，批量脚本必须尽量为每张图建立“当前图片文字锁”。

```text
当前图片文字锁 = 当前这一张源图中可见的文字块清单 + 文字块数量 + 大概位置。
输出结果只能包含这些文字块的目标语言翻译。
输出结果如果明显多出文字块、参数栏、卖点栏、图标标签、赠品、尺寸厚度等原图没有的内容，必须判定为失败，不能计入成功。
```

执行要求：

- 每处理一张图，日志里必须记录：当前源图路径、当前输出路径、当前 prompt 只针对这一张。
- 如果能做 OCR/视觉识别，先识别当前源图的可见文字块，写入 prompt 的 `VISIBLE SOURCE TEXT BLOCKS`；识别不准时也要遵守“只看当前图，不看其它图”。
- prompt 中可以明确写：`The visible source text blocks are approximately: ...`，让模型只翻译这个清单。
- 对特别简单的图，例如原图只有 `卷收悬停`，prompt 必须额外强调：`The source image has only one text block. The output must have only one translated text block.`
- 输出后必须做人工/脚本质检记录：如果结果出现原图没有的英文卖点、图标列表、底部参数表、赠品、尺寸、厚度等，加入失败清单并重生。
- 重生时 prompt 必须更严：`Previous attempt failed because it added extra text. Generate again with ONLY the existing source text translated.`

典型失败示例，必须判失败：

```text
原图只有：卷收悬停
输出却出现：Stay Cool, Heat Insulation, UV Protection, Privacy Protection, FREE GIFT, THICKNESS, SIZE, 3.5mm, 90×210cm
=> 失败，不能保存为成功图，必须重生。
```

---

## 唯一生图通道：Codex 内置生图

唯一通道必须是 Codex 内置图片编辑/生图能力。

强制要求：

- 有本地源图路径时，调用 Codex 图片编辑工具时每次只传当前这一张图，例如 `referenced_image_paths = [当前源图绝对路径]`。
- 每次 Codex 生图调用只处理一张源图；批量任务必须等待当前图片生成、保存或记录失败后，再进入下一张。
- 当前 prompt 只能包含当前图片的信息，不能包含其它图片路径、文件名、文字清单或汇总。
- 如果 Codex 生图工具返回本地生成文件路径，把该结果保存/移动/复制到目标输出路径。
- 如果 Codex 生图工具只返回会话图片而没有可保存的本地路径，必须在报告中记录该图片未能落盘，并说明原因；不要改用本地贴字、裁剪或重绘来伪造结果。

Codex 生图 prompt 仍然必须使用本 skill 的强制 prompt 模板，尤其是 `STRICT NO-ADDITION RULE` 和 `TEXT SCOPE LOCK`。

本地代码只能用于：

```text
扫描文件
创建目录
识别空白图/纯色图/透明图/无文案占位图，并按用户比例、瑕疵清理和最终优化规则处理
复制非图片文件
将当前源图传给 Codex 生图
保存 Codex 生图返回结果
统计数量
检查输出尺寸
生成报告和失败清单
翻译全部完成后，用本 skill 的最终优化脚本做 800x800 JPG 和 900KB-1024KB 体积优化
```

翻译/生图阶段不允许本地代码用于：

```text
覆盖文字
贴白块
本地重绘文字
本地裁剪成 1:1
本地拉伸/压缩/扩图
本地去白边
本地修边角
```

这些必须通过 Codex 生图 prompt 完成。

例外：最终图片优化阶段发生在所有翻译、生图、质检完成之后，只允许做确定性的尺寸、画布、JPEG 编码和体积控制，不允许改写文字、不允许贴字、不允许增加卖点、不允许改变商品含义。

---

## 目标语言识别与输出命名

根据用户表达确定目标语言。

常用映射：

```text
英语 / 英文 -> English -> 输出后缀：英语
泰语 / 泰文 -> Thai -> 输出后缀：泰语
印尼语 / 印尼文 / 印度尼西亚语 -> Indonesian -> 输出后缀：印尼语
越南语 / 越南文 -> Vietnamese -> 输出后缀：越南语
马来语 / 马来文 -> Malay -> 输出后缀：马来语
西班牙语 / 西语 -> Spanish -> 输出后缀：西班牙语
阿拉伯语 / 阿语 -> Arabic -> 输出后缀：阿拉伯语
```

强制规则：

- 输出文件夹后缀必须和用户要求一致。
- 用户说泰语，输出目录必须是 `项目名-泰语`，prompt 必须要求 Thai。
- 用户说印尼语，输出目录必须是 `项目名-印尼语`，prompt 必须要求 Indonesian。
- 不允许出现“文件夹是泰语，但 prompt 还是 English”的错误。

---

## 输入模式

### 1. 单张图片

如果用户给的是单张图片路径，仍然必须生图翻译。

输出规则：

- 默认最终输出到当前工作目录，文件名为：`原文件名-目标语言.jpg`。
- 原始生图结果可先保存为：`原文件名-目标语言-原始生图.png`，最终优化完成后以 `.jpg` 作为成品。
- 如果用户指定输出目录或“放到当前根目录”，按用户要求。
- 不要只给文字翻译，除非用户明确说“只要文字”。
- 单张图片同样遵守：目标语言、1:1、画质、边角、字体、单位、排版、不要本地手工贴字。

### 2. 整个文件夹

如果用户给的是文件夹，并说“翻译英语 / 翻译泰语 / 整个文件夹翻译 / 主图详情 sku 都翻译”等，必须批量处理。

输出目录：

```text
输入：E:\xxx\项目名称
输出：E:\xxx\项目名称-目标语言中文名
原始生图：E:\xxx\项目名称-目标语言中文名-原始生图
```

例如：

```text
铝箔隔热棉 -> 铝箔隔热棉-英语
门帘 -> 门帘-泰语
门帘 -> 门帘-印尼语
```

要求：

- 输出目录必须新建在输入目录同级。
- 默认最终输出目录保存优化后的 `.jpg` 成品；原始生图目录保存未压缩的中间结果。
- 不要把结果混到原文件夹。
- 不要覆盖原图。
- 如果输出目录已存在，默认补缺，不覆盖已有成功图片。
- 只有用户明确说“覆盖 / 重做 / 重新生成 / 全部重做”才覆盖。

---

## 目录结构与文件完整性

批量翻译时必须保持原结构。

例如：

```text
原项目/
  主图/1.jpg
  详情/a.png
  sku/SKU-01.jpg

原项目-英语/
  主图/1.jpg
  详情/a.jpg
  sku/SKU-01.jpg

原项目-英语-原始生图/
  主图/1.png
  详情/a.png
  sku/SKU-01.png
```

规则：

- 递归扫描整个输入文件夹。
- 支持图片扩展名：`.jpg .jpeg .png .webp .bmp .tif .tiff`。
- 每张图片都必须有对应最终 `.jpg` 输出；启用最终优化时，还应有对应原始生图中间文件。
- 非图片文件默认原样复制，以保持项目完整；如果用户说“只要图片”，才不复制非图片。
- 空白图、纯色图、透明图、无文案占位图只能跳过“文字翻译”，不能跳过“比例处理、瑕疵清理、最终优化”。这些图片也必须有对应原始中间文件和最终 `.jpg` 成品。
- 如果用户要求 `1:1`、方图、指定尺寸，空白/无文字图片也必须按该比例输出；不能因为没有文字就直接把原始比例复制成最终结果。
- 空白/无文字图片如果存在黑点、脏边、裁切残留、白边不整齐、透明边、压缩噪点、画布不完整等瑕疵，仍然必须调用 Codex 图片编辑/生图能力修复：保持无文字、保持空白/占位图含义，只清理瑕疵并按用户比例补齐画布。
- 只有当空白/无文字图片本身干净、无需视觉修复时，才可以把源图复制到原始生图目录作为中间文件；但最终优化启用时仍必须从原始生图目录生成符合要求的最终 JPG。
- 判断不确定时，宁可走生图翻译。
- 必须跳过已经生成的输出目录，避免重复翻译结果图。

跳过目录示例：

```text
*-英语
*-泰语
*-印尼语
*-越南语
*-马来语
*-西班牙语
*-阿拉伯语
*-原始生图
```

---

## 比例与尺寸

### 用户要求 1:1 时

如果用户说：

```text
比例 1:1
必须 1比1
方图
主图方图
```

必须：

- 在 prompt 中明确要求输出为 square 1:1 e-commerce image。
- prompt 写明输出必须是 square 1:1 e-commerce image。
- 可以通过 Codex 生图扩展/补全画面到 1:1，让背景、商品场景、边角更完整更好看。
- 扩图只能扩展无文字的视觉画面，不能扩展文字、不能扩展 SVG 图标文字、不能新增卖点、不能新增标签。
- 批量完成后检查输出宽高是否相等。
- 不是 1:1 的图片必须列入重生清单并重新调用 Codex 生图。
- 空白图、纯色图、透明图、无文案占位图也必须参与 1:1 检查；如果最终成品不是 1:1，不能记为成功。
- 不允许本地裁剪、拉伸、扩图、压缩来伪造成 1:1。

prompt 必须包含类似：

```text
Output must be a square 1:1 e-commerce image. You may extend the non-text visual background/product scene to fill the square canvas cleanly. Do not crop, stretch, or distort the product. Do not add any new text, SVG text, icon labels, slogans, parameters, or selling points while expanding. Blank/no-text areas in the source must remain text-free.
```

### 用户未指定比例时

- 默认使用 Codex 生图的稳定输出能力，并在 prompt 中要求保持原图电商详情图构图。
- 如果原图明显不是方图且用户没要求 1:1，可以保持原图电商详情图构图。
- 生图阶段不要本地二次改比例；最终优化阶段按本 skill 的 800x800 收尾规则处理。

---

## 最终尺寸与体积优化

默认必须在全部图片翻译、生图、质检完成之后，再统一执行最终优化。不要在每张图生图之后立刻压缩；必须等整批翻译完成，再进入这一收尾阶段。

默认目标：

```text
最终成品尺寸：800x800 px
最终成品格式：JPG
最终文件体积：900KB-1024KB（1MB 以下，900KB 以上）
```

强制规则：

- 只有用户明确说“不压缩 / 不要最终优化 / 保留原尺寸原体积”时，才跳过最终优化。
- 最终优化只改变画布尺寸、图片编码质量和文件体积，不允许改写文字、不允许贴字、不允许新增卖点、不允许改变商品和版式含义。
- 默认输出为精确 `800x800` 白底画布；保持原图比例等比缩放放入画布，不裁剪、不拉伸、不变形。
- 如果源图有透明背景，默认合成到白底。
- 空白图、纯色图、透明图、无文案占位图也必须进入最终优化；最终成品必须是精确 `800x800` JPG（或用户明确指定的最终尺寸），文件体积仍要控制在目标区间。
- 已存在的空白/无文字最终输出如果不是目标比例、目标尺寸、JPG 格式或体积区间，必须视为不合格并重新优化或重新生成，不能因为文件存在就跳过。
- 默认输出为 `.jpg`，保持相对目录结构和原文件名主干。
- 如果输出高于 1024KB，降低 JPEG 质量，寻找不超过上限的最高画质。
- 如果输出低于 900KB，允许像 `E:\图片压缩工具.html` 一样在 JPEG 文件尾部补无损填充字节，把文件体积补到 900KB 以上且不超过 1024KB；这不改变画面像素。
- 如果在 `800x800` 和最低 JPEG 质量下仍然超过 1024KB，记录为最终优化失败，不要继续缩小到非 800x800，除非用户明确允许。

默认目录：

```text
OUTPUT_DIR = 输入目录同级 / "项目名-目标语言"
RAW_OUTPUT_DIR = 输入目录同级 / "项目名-目标语言-原始生图"
```

执行方式：

- 翻译阶段把 Codex 生图生成的原始成品保存到 `RAW_OUTPUT_DIR`，保持原目录结构。
- 翻译全部完成并通过基础质检后，使用 `scripts/final_optimize_images.py` 从 `RAW_OUTPUT_DIR` 批量生成最终成品到 `OUTPUT_DIR`。
- 非图片文件复制到最终 `OUTPUT_DIR`。
- 如果最终 `.jpg` 已存在且大小大于 0，默认跳过；只有用户明确要求覆盖、重做、重新生成、全部重做时才覆盖。
- 最终优化完成后生成 `fanyi_optimize_report.txt`；如有失败，生成 `fanyi_optimize_failed.txt`。

推荐命令：

```text
python scripts/final_optimize_images.py --input RAW_OUTPUT_DIR --output OUTPUT_DIR --size 800x800 --min-kb 900 --max-kb 1024
```

用户指定其它尺寸或体积时，调整 `--size`、`--min-kb`、`--max-kb`，但仍然必须在翻译全部完成后执行。

---

## Prompt 总模板

每张图生成时，prompt 必须动态包含目标语言、比例、画质、字体、单位、可读性要求，并且必须把“只翻译原图已有文字、严禁新增文字”放在最前面。

强制模板：

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

禁止使用任何会诱导新增文案的 prompt 句子，例如：

```text
Prefer readable phrases such as ...
Use short punchy phrases...
Add marketplace-style labels...
Complete missing selling points...
Make icon labels clearer...
```

除非原图有对应文字，否则一律不能生成。

如果用户要求 1:1，加入：

```text
Output must be a square 1:1 e-commerce image. Fill the canvas cleanly without unwanted white borders or empty margins. Do not crop, stretch, or distort the product.
```

如果当前图片是空白图、纯色图、透明图或无文案占位图，且需要按比例或修复瑕疵，prompt 必须改为类似：

```text
This source image has no visible text. Keep it completely text-free. Do not add any words, labels, slogans, icons, badges, parameters, or decorative text. Optimize only the non-text visual canvas: clean black marks, dirty edges, crop leftovers, uneven borders, transparent edges, compression artifacts, and awkward margins. Output must follow the requested ratio exactly, such as square 1:1 when required. Preserve the blank/no-text placeholder meaning.
```

---

## 文字排版与可读性

核心原则：

```text
只翻译原图已有文字；少一点字可以，但绝对不能多出原图没有的意思。
```

规则：

- 不能为了完整逐字翻译，把字做得极小、很挤、重叠或超出区域。
- 如果目标语言比中文更长，允许换行、分段、调整行距、适当缩小字号。
- 字号不能小到难以阅读。
- 不能横向拉伸、纵向压扁、倾斜变形来硬塞文字。
- 空间不足时，只能在同一原文含义内压缩表达，不能补充新卖点、新解释、新参数。
- 必须保留原图已经写出的核心卖点、关键信息、卖点数量、重要参数、规格、数量、尺寸。
- 不允许把关键卖点直接删没。
- 不允许把原图没有写出的卖点补上去。
- 不允许把短中文扩写成带解释的长目标语言文案。

适合图片的改写方式只允许“同义压缩”，不允许“信息扩展”：

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

---

## 电商规格、数量、单位标准化

必须准确保留：

```text
数量
尺寸
单位
型号
厚度
宽度
长度
颜色
套装数量
赠品数量
适用尺寸
重要参数
```

常用翻译：

```text
1个 = 1 pc / 1 pcs
2个 = 2 pcs
一包 = 1 pack
一对 = 1 pair
一套 = 1 set
3件套 = 3-piece set / 3 pcs set
送1包图钉 = Includes 1 pack of pins
```

默认：

- 英文标准优先 `1 pc / 2 pcs`。
- 如果用户明确喜欢 `1pcs / 2pcs`，就统一用这个风格。
- 不允许把 `1包` 翻成 `1 pc`。
- 不允许漏掉数量。

尺寸格式要统一：

```text
3.5mm -> 3.5 mm 或 3.5mm，整套保持一致
4CM -> 4 cm 或 4CM，整套保持一致
90*210cm -> 90 × 210 cm
```

---

## 字体与语言风格

### 英语

- 商品图标题可用 Title Case 或全大写，但同一批要统一。
- 促销角标可用 `FREE GIFT`、`UPGRADED` 等醒目风格。
- 正文要自然，不要机器直译。

### 泰语

- 必须使用现代泰语电商广告字体风格。
- 不能像 Windows 默认字体直接打上去。
- 标题可加粗，字形圆润清晰，有层次。
- 正文字距适中，不能堆叠错乱。
- 保持和原图色块、标签、图标风格一致。

### 印尼语/越南语/马来语/西班牙语等

- 要使用对应语言自然电商表达。
- 不要逐字机械翻译。
- 字体风格要像设计稿，不像系统默认文本。

### 阿拉伯语

- 注意从右到左排版。
- 不要打乱字符连接。
- 标签和按钮内文字要保持可读。

---

## 电商画面质感与边角优化

默认要让图片看起来更像电商成品图。

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

---

## 主图 / 详情 / SKU 策略

如果目录包含这些结构，处理时要区分优先级。

### 主图

- 画面干净、醒目。
- 只翻译原图已有文案。
- 适合平台主图，但不能为了“主图效果”新增卖点或装饰文字。
- 原图没有小字，就不要生成小字。

### 详情

- 只翻译详情图原本存在的说明。
- 但必须分段、分层级、保证可读。
- 长段落可在同义范围内压缩成短句，但不能新增原文没有的功能、参数、承诺。

### SKU

- 规格、型号、尺寸、颜色、厚度、数量必须以原图可见文字为准。
- 文件名或 SKU 名只用于命名输出文件，不能进入图片画面。
- 不要为了美观删掉 SKU 关键信息。
- 不要把 SKU 文件名中的规格/颜色/属性补进图里，除非这些字在原图里可见。

---

## 常用品类词库

### 铝箔隔热棉

```text
买就送 = FREE GIFT
双面胶 = Double-sided Tape
升级款 = UPGRADED
双面铝箔（无背胶） = DOUBLE-SIDED ALUMINUM FOIL (NO ADHESIVE)
压花方格铝箔 = Embossed Grid Aluminum Foil
施工便捷 = Easy to Install
厚度：3.5mm = Thickness: 3.5mm
环保无甲醛 = Eco-Friendly, Formaldehyde-Free
终身质保 = Lifetime Warranty
不隔热不保温包退 = Full Refund If It Doesn't Insulate or Retain Heat
```

### 门帘 / 磁吸门帘

```text
门帘 = Door Curtain / Magnetic Screen Door
防蚊门帘 = Magnetic Anti-Mosquito Screen Door
磁吸门帘 = Magnetic Screen Door
魔术贴 = Hook-and-Loop Tape / Adhesive Hook-and-Loop Tape
加宽魔术贴 = Extra-Wide Hook-and-Loop Tape
顶部加宽 = Extra-Wide Top Strip / Widened Top Edge
两侧魔术贴 = Side Hook-and-Loop Tape
送图钉 = Includes Pins / Includes 1 Pack of Pins
静音设计 = Quiet Closure
内置磁铁 = Built-in Magnets
包边布 = Reinforced Edge Fabric
加密网纱 = Dense Mesh
防尘防虫 = Keeps Dust and Bugs Out
通风透气 = Breathable Mesh
适用木门/铁门/大理石门/不锈钢门/瓷砖门框 = Suitable for wooden, metal, marble, stainless steel, and tile door frames
```

---

## 批量并发、重试、补缺

并发：

```text
默认 max_workers = 1
强制串行：一张完成后再处理下一张
禁止并发：不允许 max_workers > 1
```

每张图片至少重试 3 次。重试必须发生在当前这一张内部：当前图片成功或最终失败记录后，才允许进入下一张。

如果 Codex 生图出现：

```text
RemoteDisconnected
连接断开
timeout
限流
```

不能提高并发，也不能批量并发补跑；只能保持 `MAX_WORKERS = 1`，对当前失败图片或失败清单逐张串行补跑。

批量结束后必须核对：

```text
成功生图数 + 空白图处理数 + 已存在跳过数 == 输入图片总数
```

如果用户要求 1:1，还必须核对每张输出图片宽高相等。

最终优化启用时，还必须核对：

```text
最终优化成功数 + 最终优化跳过数 == 输入图片总数
每张最终成品图片都是 800x800 JPG
每张最终成品图片体积都在 900KB-1024KB
```

---

## 报告与失败清单

批量完成后，在输出目录生成：

```text
fanyi_report.txt
```

内容至少包含：

```text
输入目录
输出目录
原始生图目录
目标语言
是否要求 1:1
是否启用最终优化
最终优化尺寸
最终优化体积区间
总图片数
成功生图数
空白图处理数
已存在跳过数
非图片复制数
失败数
最终优化成功数
最终优化跳过数
最终优化失败数
每张图片源路径和输出路径
```

如果失败数不为 0，生成：

```text
fanyi_failed.txt
```

失败清单至少包含：

```text
源图片路径
目标输出路径
失败原因
```

下次用户说继续、补跑、后台继续时，优先读取 `fanyi_failed.txt` 和缺失输出，继续补跑。

最终优化完成后，在最终输出目录生成：

```text
fanyi_optimize_report.txt
```

如果最终优化失败数不为 0，生成：

```text
fanyi_optimize_failed.txt
```

继续、补跑、后台继续时，还要优先读取 `fanyi_optimize_failed.txt` 和缺失的最终 `.jpg` 输出。

---

## 执行实现要求

批量执行可以写临时 Python 脚本，但必须遵守：

```text
INPUT_DIR = 用户给的文件夹
TARGET_LANG_SUFFIX = 目标语言中文名，例如 英语 / 泰语 / 印尼语
TARGET_LANGUAGE = prompt 里的目标语言，例如 English / Thai / Indonesian
OUTPUT_DIR = INPUT_DIR.parent / f"{INPUT_DIR.name}-{TARGET_LANG_SUFFIX}"
RAW_OUTPUT_DIR = INPUT_DIR.parent / f"{INPUT_DIR.name}-{TARGET_LANG_SUFFIX}-原始生图"
MAX_WORKERS = 1  # 死命令：必须一张一张顺序翻译，禁止并发，禁止串线混图
IMAGE_EXTS = .jpg .jpeg .png .webp .bmp .tif .tiff
用户要求 1:1 时：size = 1024x1024
唯一生图通道：Codex 内置图片编辑/生图能力
最终优化脚本：scripts/final_optimize_images.py
最终优化默认参数：--size 800x800 --min-kb 900 --max-kb 1024
```

执行逻辑：

1. 判断输入是单图还是文件夹。
2. 判断目标语言和输出后缀。
3. 判断是否要求 1:1、覆盖、只补缺、只图片。
4. 扫描文件，但跳过输出目录、原始生图目录和其他语言输出目录。
5. 创建原始生图目录和最终输出目录，并保持相对路径结构。
6. 非图片文件默认在最终优化阶段复制到最终输出目录。
7. 空白图、纯色图、透明图、无文案占位图只能跳过文字翻译，不能跳过比例处理、瑕疵清理和最终优化：有瑕疵或用户要求 1:1/指定比例时，调用 Codex 生图按比例清理；本身干净时可复制到 `RAW_OUTPUT_DIR` 作为中间文件，但仍必须进入最终优化。
8. 有文案/不确定图片默认走 Codex 生图，并把原始结果保存到 `RAW_OUTPUT_DIR`。
9. 调用 Codex 生图时，每次只把当前这一张源图作为 `referenced_image_paths` 传入。
10. 强制使用 `MAX_WORKERS = 1`，一张一张顺序处理，禁止并发。
11. 失败自动重试，但必须在当前图片内部完成重试；当前图片成功或记入失败后，才进入下一张。
12. 最终 `.jpg` 或原始生图输出存在且大小 > 0 时默认跳过，除非用户要求覆盖。
13. 用户要求 1:1 时检查所有图片输出尺寸，空白/无文字图片也必须检查；不合格则重生或重新最终优化。
14. 翻译全部完成后，生成基础报告和失败清单。
15. 除非用户明确禁止最终优化，否则运行 `scripts/final_optimize_images.py`，从 `RAW_OUTPUT_DIR` 生成最终 `OUTPUT_DIR`。
16. 检查最终优化报告，确认最终图片为 800x800 JPG，且体积在 900KB-1024KB。
17. 回复用户时汇报输入、输出、原始生图目录、数量、失败数、最终优化结果。

---

## 最终回复用户时

完成后简洁汇报：

```text
已完成/已后台开始：
输入目录：...
输出目录：...
原始生图目录：...
目标语言：...
图片总数：...
成功生图：...
空白图处理：...
已存在跳过：...
失败：...
最终优化：800x800 JPG，900KB-1024KB
优化成功：...
优化失败：...
报告：...
优化报告：...
```

如果失败：

- 说明失败数量。
- 列出失败文件。
- 告诉用户可以继续补跑。
