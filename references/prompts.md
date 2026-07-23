# xobi-img 提示词规则

统一顺序：执行方式 → 唯一目标变化 → 不变内容锁 → 文字/素材 → 比例 → 禁止项。每个 task、每个 attempt 使用独立 prompt；只描述当前图。

generate、普通 edit 和 commerce_main_image 继续使用纯生图且不传参考图。localization 恢复 fanyi 参考图编辑：只传当前 task 的源图，禁止附带失败候选或其他图片。Logo 冲突底图仍按 Logo 专属规则处理。

## generate

```text
PURE GENERATION. REFERENCE INPUT: NONE. Create a {RATIO} image for {PURPOSE}. Show {SUBJECT AND COMPOSITION}.
Use {STYLE, LIGHTING, MATERIALS, COLORS}. Include only {REQUIRED ELEMENTS}.
No text, letters, numbers, logos, watermarks, or pseudo-text unless exact text is explicitly listed.
If this task includes a later active Logo overlay, do not draw, imitate, spell, or include that active Logo in this generation. Preserve any separately listed pre-existing Logo as source content when applicable.
```

## edit

```text
PURE GENERATION EDIT. REFERENCE INPUT: NONE. Recreate the complete image from the frozen source inventory and perform only: {REQUESTED CHANGES}.
SOURCE INVENTORY: {COMPLETE_SOURCE_INVENTORY}.
UNCHANGED CONTENT LOCK: Reproduce exactly the same subjects, products, people, photos, icons, logos, text, quantities, colors, borders, background, positions, layout, lighting, shadows, crop, scale, and relationships except the explicitly requested change.
Output ratio: {RATIO OR ORIGINAL}. Do not alter, redesign, beautify, replace, add, or remove anything else.
```

多素材任务先逐一写明角色并把允许使用的视觉信息转写进当前 prompt；target、asset、style 或 layout 图片一律不传给图片模型，即使用户要求“参考”也只由协调者查看后转写。只有用户明确要求添加 Logo、真实发生遮挡并通过 [logo.md](logo.md) 门禁时，才可把尚未叠加本次 active Logo 的 `conflict_reference_base` 作为唯一参考。

## commerce_main_image 显式创意主图

只有用户明确要求制作、重做或优化整张主图时使用。完整边界、艺术指导与验收以 [main-image.md](main-image.md) 为唯一真源。无输入按基础 `generate` 预检，有输入按基础 `edit` 预检；图片调用都不传参考图。每个 task 使用：

```text
PURE GENERATION COMMERCE MAIN IMAGE. REFERENCE INPUT: NONE. CREATIVE ROUTE: commerce_main_image.
Create one complete {RATIO} commerce main image for {PLATFORM_PROFILE}. Follow the frozen visual direction exactly: {VISUAL_DIRECTION}.

PRODUCT AND CONTENT LOCK: {PRODUCT_CONTENT_LOCK}. Preserve the exact product identity, quantity, model, color, components, natural proportions, real structure, verified facts, and required Logo status. Do not invent unseen product structures or alter any locked fact.

TEXT POLICY: {TEXT_POLICY}. Use exactly {EXACT_TEXT}. If no_text, add no headline, caption, marketing copy, badge, watermark, or pseudo-text; still preserve only the locked brand marks and printed text physically present on the product or packaging. Never invent, paraphrase, embellish, or infer a selling point, parameter, certification, discount, rating, gift, or claim.

ART DIRECTION LOCK: Use one visual focal point with the product as the first reading layer. Target product occupancy {HERO_OCCUPANCY} and safe margin {SAFE_MARGIN}. Use only the minimum necessary information hierarchy: {INFORMATION_HIERARCHY}. Keep the product complete, recognizable, naturally proportioned, and clearly separated from the background.

CAMERA, SCALE, AND COMPOSITION: {CAMERA_AND_SCALE}; {COMPOSITION}. Keep perspective, horizon, prop scale, crop, balance, and negative space physically credible. Never stretch, squash, locally enlarge, crowd, or make the product float.

LIGHTING, SHADOW, AND MATERIAL: {LIGHTING_AND_SHADOW}; {MATERIAL_RESPONSE}. Use coherent key light, highlight control, contact shadow, edge separation, texture scale, roughness, reflection, transmission, and fabric/metal/glass/plastic response appropriate to the real product. Avoid waxy or plasticized surfaces, dirty or double shadows, repeated textures, fake gloss, and unsupported highlights.

BACKGROUND AND COLOR: {BACKGROUND_AND_COLOR}. Keep the background clean, the product separated by controlled value/color contrast, and saturation/highlights restrained.

THUMBNAIL GATE: When proportionally reduced with no crop or stretch to a 256px longest edge and a 160px longest edge, the product, single focal point, and silhouette must remain immediately recognizable. If text is allowed, the primary message must remain readable; secondary text must not be required to understand the image.

FORBIDDEN COMMERCIAL CLICHES: no cheap yellow-black promotional strips, random red corner badges, thick outlines, oval stickers or pill patches, crowded collages, fake 3D lettering/buttons/floating icons, oversaturation, fabricated claims, or pseudo-text.

If this task includes a later active Logo overlay, do not draw, imitate, spell, or include that active Logo. The exact asset is handled only by the separate Logo workflow.
```

主图重试只补强当前失败轴，例如 `weak_single_focus`、`hero_occupancy_or_margin_fail`、`cluttered_hierarchy`、`material_unrealistic`、`scale_or_perspective_fail`、`lighting_or_shadow_fail`、`thumbnail_256_fail`、`thumbnail_160_fail` 或 `cheap_cliche_or_invented_claim`；不得放松商品与精确文字锁，也不得把失败候选作为下一次参考。

## localization：fanyi 参考图只换字

把当前源图作为唯一 target reference，使用 [localization.md](localization.md) 的逐图文字锁。prompt 必须包含：

```text
Use the current source image as the sole authoritative edit reference.
Translate every visible source-language text block into {TARGET_LANGUAGE}.
Replace only text that already exists in the source image.

STRICT NO-ADDITION RULE: Do not add, invent, infer, complete, or hallucinate any new title, slogan, selling point, label, badge, parameter, footer, watermark, decoration, icon, object, or pseudo-text. Areas without text in the source must remain text-free.

TEXT SCOPE LOCK: Keep the same number, semantic scope, approximate position, hierarchy, color relationship, alignment, and reading order of text blocks. Preserve every number, model, quantity, size, currency, and unit meaning.

STRICT CONTENT LOCK: Keep the exact product, people, photos, Logo, icons, borders, color blocks, background, shadows, textures, quantities, crop, scale, positions, spacing, composition, and layout. Do not redesign, beautify, restyle, recolor, move, add, remove, or replace any non-text content.

PRODUCT GEOMETRY LOCK: Preserve natural aspect ratio, silhouette, thickness, component proportions, material texture, and perspective. Never stretch, squash, widen, narrow, flatten, elongate, or locally enlarge the product.

Output ratio: {RATIO}. This is a translation edit, not a redesign.
```

本地源图使用 `referenced_image_paths`；仅会话图片使用能覆盖当前源图的最小 `num_last_images_to_include`，两者不能共存。每次重试重新引用原始源图，不引用失败候选。候选通过后直接作为 fanyi 原始翻译成品，不运行 `compose_localization.py`。1:1 且用户未覆盖旧版交付规格时，最后运行 `final_optimize_images.py`。

## 商品几何锁

涉及商品的 edit、localization、Logo 冲突重排和比例转换加入：

```text
PRODUCT GEOMETRY LOCK: Preserve the product's natural aspect ratio, silhouette, thickness, width-to-height relationship, component proportions, material texture, and perspective. Never stretch, squash, widen, narrow, flatten, elongate, or locally enlarge the product. Adapt only by proportional scaling or a natural recomposition explicitly required by the user.
```

## 长译文

没有用户精确目标稿时，先在写 plan 前选定忠实、自然且不增删事实的译文；写入 `translation` 后逐字锁定。用户提供精确目标稿时直接标记 `user_exact`，禁止任何精简或改写。随后只依次尝试自然换行 → 在获准文字框内调整排版 → 适度缩小字号。不得压缩字宽、拉长字高、改变整个版式、移动非文字元素、粘连、重叠、裁切、溢出或扭曲。数量、型号、尺寸、单位和核心含义不可删。

## Logo 冲突重排

详细判定以 [logo.md](logo.md) 为唯一真源。`direct_overlay` 不调用图片工具。只有信息模块与 `VISIBLE_BBOX_PIXELS` 实际相交时，才进入唯一允许传参考图的 `logo_conflict` 阶段：把尚未叠加本次 active Logo 的 `conflict_reference_base` 作为唯一参考，只做局部重排；源图原有 Logo 仍必须保留，并使用：

`FAMILY_PILOT` 只能展开为 manifest 中已冻结的方向、锚点、层级和间距等文字锁；不得把 pilot 图片、其他成员图片或最近会话图片作为第二张参考输入。

```text
SOURCE CONTENT LOCK: Preserve one-for-one every original product, photo, person, text block, icon, gift thumbnail, badge, label, border, color block, quantity, order, and relationship listed in {SOURCE_INVENTORY}. Add nothing and omit nothing.

ACTUAL COLLISION: The future Logo visible pixels occupy {VISIBLE_BBOX_PIXELS}. Move only the complete conflicting information module {CONFLICTING_MODULES}; do not move unrelated content. A thumbnail/icon/badge and its attached wording are one indivisible module.

TOP-LEFT LOGO BALANCE: Use {SAFE_ZONE_PIXELS} only as the comfortable post-rearrangement spacing boundary. It is an invisible layout constraint, not the collision test. Never draw its border, a white box, a panel, a top bar, a placeholder, or a full-width blank strip. Preserve the original background naturally through it.

MODULE ANCHOR: Place the nearest moved information-module outer edge inside {RIGHT_MODULE_START_RANGE} when using the right layout, or {BELOW_MODULE_START_RANGE} when using the below layout. The safe zone already contains the gap; do not add another gap. Choose the smallest-change layout that remains balanced.

FAMILY LAYOUT LOCK: Follow the text-only constraints recorded for accepted pilot {FAMILY_PILOT}: {DIRECTION}, {MODULE_ANCHOR}, {TYPE_HIERARCHY}, {MODULE_SPACING}. Do not attach or reference the pilot image. Preserve this image's own wording and product; do not invent a different layout strategy.

Do not draw, imitate, spell, or include the Logo itself. The exact asset will be overlaid afterward.
```

## 对象、背景、合成与透明图

- 删除对象：纯生图重建，描述删除后的合理补全，并锁定其余区域。
- 替换对象：纯生图重建，明确位置、尺度、透视、光线和接触阴影。
- 换背景：纯生图重建，锁定前景轮廓、材质和商品几何，只改变背景。
- 合成：纯生图重建；先查看素材并把允许抽取的内容写入 prompt，原生图片调用仍不传参考图，禁止携带无关主体或文字。
- 透明背景：主体边缘干净、无文字水印；宿主不能输出透明通道时如实说明，不静默切换服务。

## BATCH_STYLE_LOCK

只共享用户明确要求的抽象视觉规范，不共享单图商品、译文、参数、构图、参考图或会话上下文。自动 `layout_family_lock` 只在同系列内部引用已验收 pilot。
