# xobi-img 提示词规则

统一采用：目标变化 → 必须保持 → 文字 → 输出比例 → 禁止项。单次 prompt 只描述当前任务。

## generate

```text
Create a {RATIO} image for {PURPOSE}. Show {SUBJECT AND COMPOSITION}.
Use {STYLE, LIGHTING, MATERIALS, COLORS}. Include only {REQUIRED ELEMENTS}.
No text, letters, numbers, logos, watermarks, or pseudo-text unless exact text is explicitly listed.
```

## edit

```text
Use the target reference image and perform only: {REQUESTED CHANGES}.
Preserve {SUBJECT GEOMETRY, POSITION, VIEW, MATERIAL, LIGHTING, SHADOWS, BACKGROUND, LAYOUT, UNCHANGED TEXT}.
Output ratio: {RATIO OR ORIGINAL}. Do not alter unspecified areas.
```

多参考图逐一声明角色，禁止含糊地写“参考这些图片”。

## localization 默认纯生图

先查看源图，建立当前图片独立文字锁，再用简短 prompt 直接重建，不传生成参考图：

```text
Recreate a {RATIO} e-commerce image showing {PRODUCT AND COMPOSITION} in {TARGET_LANGUAGE}.
STRICT NO-ADDITION RULE: Translate only the visible text that already exists in the source image. Do not add, invent, infer, complete, or hallucinate new text, slogans, selling points, labels, badges, parameters, footer text, or decorative wording. Do not use filenames, folder names, product appearance, or category names to create text. Blank/no-text areas must remain text-free.
TEXT SCOPE LOCK: The source has exactly {COUNT} visible text blocks at {POSITIONS}. Replace them one-for-one with exactly: {TRANSLATED_TEXT_BLOCKS}. No other text and no watermark.
Preserve the product, quantity, natural proportions, material, colors, layout hierarchy, icons, background meaning, and selling-point regions described from the source.
```

用户明确要求参考图编辑时，把同样的三道锁用于 edit prompt，不改变文字范围。

## 商品几何锁

任何 edit、localization 和比例转换都加入：

```text
PRODUCT GEOMETRY LOCK: Preserve the product's natural aspect ratio, silhouette, thickness, width-to-height relationship, component proportions, material texture, and perspective. Never stretch, squash, widen, narrow, flatten, elongate, or locally enlarge the product to fill the canvas. Adapt only by proportional scaling, extending text-free background, adding balanced space, or naturally recomposing the unchanged product.
```

## 长文字

依次采用同义精简 → 自然换行 → 调整文字框 → 适度缩小字号。禁止压缩字宽、拉长字高、字距粘连、重叠、裁切、溢出和扭曲。核心含义、数量、型号和单位不得丢失。

## 对象、背景、Logo 与合成

- 删除对象：描述删除后的合理补全，并锁定其余区域。
- 替换对象：明确位置、尺度、透视、光线和接触阴影。
- 换背景：锁定前景轮廓和材质，使背景光线、色温和景深匹配。
- Logo：AI 只生成无 Logo 底图，并为脚本给出的左上角 safe_zone 留出完全空白的背景区域。若原文字位于该区域，必须把完整文字自然移到 Logo 右侧或下方，不能删除、缩小到不可读或覆盖。真实 Logo 由 `apply_logo.py` 最后叠加，prompt 中禁止要求 AI 绘制 Logo。
- 合成：逐张声明只抽取的元素，禁止携带无关主体和文字。

Logo 冲突重排 prompt 必须加入：

```text
TOP-LEFT LOGO SAFE ZONE: Keep rectangle {SAFE_ZONE_PIXELS} completely free of all text, products, icons, badges, and important visual content. Preserve every original text block exactly, but naturally reposition any conflicting top text to the right of or below this reserved area. Do not draw, imitate, spell, or include the logo itself; the exact logo will be overlaid later from the original asset.
```

## 透明背景

要求主体边缘干净、无阴影污染、无文字水印。宿主无法直接输出透明通道时，可先生成高对比隔离背景，再做确定性离线去色；不能静默切换未授权服务。

## BATCH_STYLE_LOCK

只共享抽象视觉规范，不共享单图商品、译文、参数、构图或会话图片。每图 prompt 摘录相同的字体视觉、层级、对齐、边距、色板、标签和自然商品尺度，并继续服从本图独立内容锁。
