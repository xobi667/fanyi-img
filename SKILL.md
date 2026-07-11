---
name: fanyi
description: Codex 专属商品图翻译技能，必须使用 Codex 内置生图/图片编辑能力，把商品图片中的中文翻译成英语、泰语、印尼语等目标语言，并生成电商成品图；翻译全部完成后默认做最终图片优化，输出 800x800 JPG 且文件体积控制在 900KB-1024KB。适用于单张图片、整个文件夹、主图/详情/sku 批量翻译、无文字商品图/纯产品图按比例优化、空白图按比例优化、1:1 方图、画质优化、边角清理、字体排版优化、最终压缩等场景。
---

# fanyi

## 核心目标

把中文商品图通过 Codex 内置生图/图片编辑能力重生为目标语言电商成品图。保持商品、构图、布局、颜色、图标含义和卖点区域基本不变，同时优化文字、字体、边角、画质、比例和整体电商质感。

除非用户明确说“只要文字翻译，不要生图”，否则必须调用 Codex 内置生图/图片编辑能力生成图片。

本 skill 是 Codex 专属流程。唯一允许的生图通道是 Codex 内置生图/图片编辑能力，不配置、不读取、不调用任何外部图片编辑服务。

翻译、生图、质检全部完成后，默认必须执行最终图片优化：输出 800x800 JPG，文件体积控制在 900KB-1024KB。只有用户明确说“不压缩 / 不要最终优化 / 保留原尺寸原体积”时，才跳过。

## 必读引用

根据任务读取引用文件，不要把长规则留在脑内猜：

- 执行单图或批量任务前，读取 `references/workflow.md`。
- 组装任何 Codex 生图 prompt 前，读取 `references/prompts.md`。
- 做质检、重试、补跑、报告前，读取 `references/quality.md`。
- 翻译可见文字、处理目标语言、规格单位或品类词时，读取 `references/glossary.md`。

可用脚本：

- `scripts/preflight_fanyi.py`：批量预检输入图片、比例、已有输出、最终成品合规性，并输出处理清单。
- `scripts/final_optimize_images.py`：翻译全部完成后统一输出 800x800 JPG，并把体积控制在 900KB-1024KB。

## 死命令：只翻译原图已有文字

这是最高优先级规则，任何“优化、美化、电商质感、可读性、词库、SKU、文件名、产品理解”都不能覆盖它。

```text
图中有什么文字，就只翻译什么文字。
图中没有的文字，一律不准生成。
原图无文字区域，必须保持无字。
严禁创新，严禁补充，严禁联想，严禁根据产品外观/文件名/SKU/目录名生成新卖点。
```

强制禁止：

- 不允许新增任何原图不存在的标题、副标题、卖点、图标文字、参数、标签、角标、徽章、装饰文案、说明文字、页脚小字。
- 不允许把文件名、SKU 名、目录名、商品名、图片路径中的词加入画面。
- 不允许根据产品看起来像什么来补卖点。
- 不允许因为“电商图更完整”而补齐图标区、横幅、参数表或营销口号。
- 不允许把空白区域变成有字区域。
- 不允许把短词翻译成长句；只能在同一含义内缩短目标语言表达。
- 词库只允许在原图确实出现对应中文词时用于选词，不能主动创造文案。

Codex 生图 prompt 必须从同等强度的英文禁令开始：

```text
STRICT NO-ADDITION RULE: Translate only the visible text that already exists in the source image. Do not add, invent, infer, complete, or hallucinate any new text, slogans, selling points, labels, badges, icons, parameters, footer text, or decorative wording. Do not use the filename, SKU name, folder name, product category, or visual appearance to create text. If an area has no text in the original image, keep it completely text-free. Keep the number of text blocks and their positions the same as the original. Only replace existing text with its translation.
```

## 死命令：必须一张一张顺序处理

批量任务必须串行处理，一张完成并保存后再处理下一张。

- 默认 `MAX_WORKERS = 1`，不允许为了速度改成 2、5、10 或更高。
- 每次 Codex 生图请求只能传入当前这一张源图。
- 当前 prompt 只能描述当前这一张图，不能包含其它图片的信息。
- 禁止多张并发、禁止多图同传、禁止把多张图内容混到同一张结果图。
- 失败重试必须发生在当前图片内部；当前图片成功或记入失败后，才允许进入下一张。

脚本实现必须使用类似逻辑：

```text
for image in images:
    process_one_image(image)
    save_result(image)
    then continue to next image
```

禁止使用：

```text
ThreadPoolExecutor
ProcessPoolExecutor
asyncio.gather
parallel map
批量把多张图同时发起生图请求
```

## 死命令：唯一生图通道是 Codex

唯一允许的图片生成/编辑通道是 Codex 内置图片编辑/生图能力。

- 有本地源图路径时，每次只传当前这一张图，例如 `referenced_image_paths = [当前源图绝对路径]`。
- 如果 Codex 生图工具返回本地生成文件路径，把该结果保存到目标输出路径。
- 如果 Codex 生图工具只返回会话图片而没有可保存的本地路径，必须在报告中记录未能落盘，不要改用本地贴字、裁剪或重绘伪造结果。

本地代码只能用于扫描文件、创建目录、预检、复制非图片文件、保存 Codex 返回结果、统计、尺寸检查、报告、最终尺寸和体积优化。

翻译/生图阶段不允许本地代码用于覆盖文字、贴白块、本地重绘文字、本地裁剪成 1:1、本地拉伸/压缩/扩图、本地去白边、本地修边角。这些必须通过 Codex 生图 prompt 完成。

例外：最终图片优化阶段发生在所有翻译、生图、质检完成之后，只允许做确定性的尺寸、画布、JPEG 编码和体积控制，不允许改写文字、不允许贴字、不允许新增卖点、不允许改变商品含义。

## 无文字商品图也必须处理

无文字商品图、纯产品图、素材图、空白图、纯色图、透明图、无文案占位图只能跳过“文字翻译”，不能跳过“比例处理、瑕疵清理、最终优化”。

- 如果用户要求 `1:1`、方图、指定尺寸，无文字商品图/纯产品图/空白图也必须按该比例输出。
- 竖图、长图、拼接图、局部特写图在 1:1 任务中必须通过 Codex 图片编辑/生图整理成方图：保留产品主体和材质细节，清理边角和拼接感，补齐无文字背景；不得新增文字、卖点、图标或参数。
- 空白/无文字图片如果存在黑点、脏边、裁切残留、白边不整齐、透明边、压缩噪点、画布不完整、拼接线明显等瑕疵，仍然必须调用 Codex 修复。
- 只有当空白/无文字图片本身干净、比例已经合格、无需视觉修复时，才可以复制到原始生图目录作为中间文件；最终优化仍必须执行。

## 默认目录和收尾

文件夹任务默认使用：

```text
OUTPUT_DIR = 输入目录同级 / "项目名-目标语言中文名"
RAW_OUTPUT_DIR = 输入目录同级 / "项目名-目标语言中文名-原始生图"
```

翻译阶段把 Codex 原始结果保存到 `RAW_OUTPUT_DIR`；全部翻译和基础质检完成后，再统一运行：

```text
python scripts/final_optimize_images.py --input RAW_OUTPUT_DIR --output OUTPUT_DIR --size 800x800 --min-kb 900 --max-kb 1024
```

默认最终成品必须满足：

```text
最终成品尺寸：800x800 px
最终成品格式：JPG
最终文件体积：900KB-1024KB
```

如果最终 `.jpg` 已存在且大小大于 0，默认跳过；但如果已有输出不是目标比例、目标尺寸、JPG 格式或体积区间，必须视为不合格并重新优化或重新生成。

## 推荐执行顺序

1. 判断输入是单图还是文件夹。
2. 判断目标语言、输出后缀、是否要求 1:1、是否覆盖、是否只补缺。
3. 批量任务先运行 `scripts/preflight_fanyi.py` 生成处理清单。
4. 按清单逐张串行处理，跳过输出目录和原始生图目录。
5. 组装 prompt 前读取 `references/prompts.md`，可见文字翻译时读取 `references/glossary.md`。
6. 每次只把当前源图传给 Codex 生图。
7. 输出后按 `references/quality.md` 做基础质检；失败则当前图片内部重试。
8. 全部完成后运行最终优化脚本。
9. 检查最终优化报告，确认每张最终图片为 800x800 JPG，体积在 900KB-1024KB。

预检推荐命令：

```text
python scripts/preflight_fanyi.py --input INPUT_DIR --target-suffix 英语 --require-square
```

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
无文字/空白图处理：...
已存在跳过：...
失败：...
最终优化：800x800 JPG，900KB-1024KB
优化成功：...
优化失败：...
报告：...
优化报告：...
```

如果失败，说明失败数量、列出失败文件，并告诉用户可以继续补跑。
