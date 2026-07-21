# Codex 内置生图提示词规则

提示词应明确、可观察、可验收。统一采用“目标—保持—文字—输出—禁止项”结构。

## 新图 generate

写明：

- 用途和主体；
- 构图、镜头、场景、光线、材质和色彩；
- 画幅或尺寸（仅用户有要求时）；
- 必须出现和必须避免的元素；
- 若无文字需求：`画面中不得出现任何文字、字母、数字、Logo、水印或伪文字。`

示例骨架：

`为[用途]创建一张[风格]图片。主体是[主体与动作]，位于[构图位置]；场景[背景]，光线[光线]，色彩[色彩]。必须包含[元素]。不得出现[禁止项]。`

## 改图 edit

必须同时写变化和锁定项：

`以参考图中的目标图片为基础，只执行以下修改：[修改项]。严格保持[主体几何、位置、视角、材质、光线、阴影、背景、版式、未指定文字]不变。不要重绘或改动未点名区域。输出保持原比例；不得添加新文字、Logo、水印或装饰。`

对多参考图明确角色：

`图1是编辑目标；图2只提供风格；图3只提供 Logo/素材。不要把图2或图3的主体复制进图1，除非修改项明确要求。`

## 图片翻译 localization

翻译提示词必须包含三道锁：

1. 文字锁：只替换源图中实际存在的文字；逐项核对，不漏译、不新增。
2. 商品锁：商品形状、数量、比例、纹理、颜色、部件、摆放和遮挡关系不变。
3. 版式锁：文字层级、对齐、行数、色块、图标、背景、留白和促销结构尽量保持。

骨架：

`将参考图中所有可见的[源语言]文字准确替换为[目标语言]。只翻译原图已有文字，不新增标题、卖点、品牌、价格、角标或说明。严格保持商品、人物、背景、图标、布局、颜色、光线、阴影、透视和未指定区域不变。译文自然、拼写正确，单位和数量不得改变。输出比例为[比例]。`

品牌名、型号、数字、单位、网址和专有词是否翻译，以用户要求和 `glossary.md` 为准。看不清的文字不要编造。

localization 必须体现以下强约束，但单次调用只摘录当前图片需要的字段：

```text
STRICT NO-ADDITION RULE: Translate only the visible text that already exists in the source image. Do not add, invent, infer, complete, or hallucinate any new text, slogans, selling points, labels, badges, icons, parameters, footer text, or decorative wording. Do not use the filename, SKU name, folder name, product category, or visual appearance to create text. If an area has no text in the original image, keep it completely text-free.

TEXT SCOPE LOCK: Identify the exact visible source text blocks. Replace every existing block with one matching translated block. Do not create extra text blocks. Preserve the original number and approximate positions of text blocks.
```

目标语言较长时固定采用：同义精简 → 自然换行 → 调整文字框 → 适度缩小字号。禁止压缩字宽、拉长字高、挤压字距、重叠、裁切、溢出或扭曲文字。

## 文字与 Logo

- 用户给出精确文案时，提示词中逐字引用，并要求只出现一次。
- 生成后逐字验收大小写、标点、数字、单位和换行。
- 用户没授权改 Logo 时，保持原 Logo 不变。
- 用户要求添加 Logo 时，明确来源图角色、位置、尺寸和禁止变形。

## 对象增删、背景替换与合成

- 删除对象：同时描述删除后的合理补全，锁定其余内容。
- 替换对象：描述新对象的位置、尺度、透视、光线和接触阴影。
- 换背景：锁定前景主体轮廓和细节，使新背景的光线、色温和景深匹配。
- 合成：逐张声明来源，只抽取用户指定元素；避免把参考图的无关文字和主体带入成品。

## 比例转换与商品几何锁

edit、localization 及任何画幅转换都必须加入商品几何锁：

```text
PRODUCT GEOMETRY LOCK: Preserve the product's natural aspect ratio, silhouette, thickness, width-to-height relationship, component proportions, material texture, and perspective. Never stretch, squash, widen, narrow, flatten, elongate, or locally enlarge the product to fill the canvas. Adapt to a new ratio only by extending text-free background, adding balanced breathing room, or naturally recomposing the unchanged product.
```

## 无文字商品图

源图无文字时明确写：保持完全无字，只处理用户要求的比例、背景、边缘或瑕疵；不得生成标签、图标、卖点、参数、包装文字或伪字。禁止用“补齐卖点、增加电商标签、让信息更完整”等会诱导新增文案的措辞。

## 用户明确要求统一时的 BATCH_STYLE_LOCK

只有用户明确要求整批视觉/排版/系列风格统一时使用。主协调者把以下结构以真实值写入 `<任务目录>/batch_style_lock.json`：

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
  "reference_source": "代表图相对路径或 null",
  "content_isolation": true
}
```

使用 UTF-8 原子写入；`content_isolation` 必须为 `true`。不得保存单图译文、参数、商品内容或会话图片。每图 prompt 只摘录当前任务需要的风格字段，并强调：统一视觉语言但保留本图自己的信息结构，不复制其它图片内容。worker 只读；修订由主协调者递增版本。

## 透明背景

要求主体边缘干净、无阴影污染、无文字水印。内置能力无法直接稳定输出透明通道时，可先生成高对比纯色隔离背景，再用本地确定性去色；禁止改走 key 型服务。

## 重试

只针对可见失败增加约束，例如“文字多出一行”“商品形状被改”“背景未替换”。不要在重试中扩大创作范围，也不要改换外部模型。