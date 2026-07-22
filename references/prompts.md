# xobi-img 提示词规则

统一顺序：唯一目标变化 → 不变内容锁 → 文字/素材 → 比例 → 禁止项。每个 task、每个 attempt 使用独立 prompt；只描述当前图。

## generate

```text
Create a {RATIO} image for {PURPOSE}. Show {SUBJECT AND COMPOSITION}.
Use {STYLE, LIGHTING, MATERIALS, COLORS}. Include only {REQUIRED ELEMENTS}.
No text, letters, numbers, logos, watermarks, or pseudo-text unless exact text is explicitly listed.
```

## edit

```text
Use the current target reference image and perform only: {REQUESTED CHANGES}.
UNCHANGED CONTENT LOCK: Preserve exactly {SUBJECTS, PHOTOS, ICONS, LOGOS, TEXT, COLORS, BORDERS, BACKGROUND, POSITIONS, LAYOUT, LIGHTING, SHADOWS} except the explicitly requested change.
Output ratio: {RATIO OR ORIGINAL}. Do not alter, redesign, beautify, replace, add, or remove anything else.
```

多参考图逐一写明角色。只把当前目标图作为 target；其他图片只能承担用户明确的 logo/asset/style/layout 角色。

## localization 默认参考图只换字

默认把当前源图作为 target reference，使用 [localization.md](localization.md) 的逐图 plan。prompt 必须包含：

```text
TEXT-ONLY REFERENCE EDIT. Use the current source image as the authoritative visual reference.
Replace only the existing visible text glyphs from these source regions: {SOURCE_TEXT_REGIONS}. Place them only in the approved {TARGET_TEXT_REGIONS}; target regions may differ only where TEXT_LAYOUT_ADAPTATION explicitly permits it.
Replace the source blocks one-for-one with exactly: {TRANSLATED_TEXT_BLOCKS}.

STRICT NON-TEXT LOCK: Every non-text element is immutable. Preserve the exact product and all photos, people, icons, logos, badges, borders, color blocks, background, shadows, textures, quantities, order, and relationships. Do not redraw, restyle, beautify, distort, add, remove, or replace any non-text content. Reconstruct only the minimal pixels directly behind the replaced glyphs.

STRUCTURED NON-TEXT INVENTORY: {NON_TEXT_INVENTORY}. Every bounded element that intersects a source or target text bbox is immutable and must appear in the computed protected set.

PROTECTED NON-TEXT REGIONS: Preserve pixel-for-pixel every frozen protected region inside a text bbox: {PROTECTED_NON_TEXT_REGIONS}. These pixels are not part of the editable text mask. Do not cover, erase, recolor, redraw, move, crop, or replace them. Do not shrink, omit, or relabel an intersecting element as background.

STRICT NO-ADDITION RULE: Do not add, invent, infer, complete, or hallucinate any new slogan, selling point, label, badge, parameter, footer, watermark, decoration, object, or pseudo-text. Blank/no-text areas must remain text-free.

TEXT SCOPE LOCK: The source has exactly {COUNT} visible text blocks at {SOURCE_POSITIONS}. Keep the same block count, semantic role, block reading order, hierarchy, colors, and relationships. Preserve alignment and writing direction unless the per-block TEXT_LAYOUT_ADAPTATION explicitly records a target-language change such as RTL alignment. Use only {TARGET_POSITIONS}; a target box may expand only when that adaptation is approved. Preserve every numeric value, model, quantity, dimension, currency, and unit meaning exactly. Translate unit words only into their direct target-language equivalent and normalize punctuation or spacing only when that language requires it; never change a value or invent a quantity.

VERBATIM TARGET LOCK: For every block marked user_exact, reproduce REQUESTED_TARGET_TEXT character-for-character. Do not shorten, paraphrase, polish, correct, or substitute it. Only the separately approved wrapping, target bbox, and font-size adaptation may change.

RATIO LAYOUT LOCK: Preserve exact non-text positions, spacing, composition, crop, scale, and canvas structure. Only the per-block TEXT_LAYOUT_ADAPTATION already frozen in the plan may adjust target-text wrapping, font size, alignment, writing direction, or TARGET_BBOX. This localization prompt is valid only when the output keeps the source aspect ratio. A different aspect ratio must stop before the image call and cannot be smuggled in as a layout change.

Output ratio: {RATIO OR ORIGINAL}. RATIO_ADAPTATION: NONE. This is not a redesign or a full-image recreation.
```

用户指定相同宽高比的新精确像素尺寸时，不把缩放许可写进图片 prompt。先按源图画布完成并验收 `localized_base`，再对整张画布做一次等比确定性重采样生成 `final`；plan 记录 `target_size` 与 `size_resample.method=whole_canvas_lanczos`。

用户指定的新宽高比与源图不同时，当前 localization 必须在 prompt 组装和图片调用之前 fail closed；让用户改为保持原比例，或另开明确授权的比例适配任务。不得生成包含 `RATIO_ADAPTATION` 画布扩展或模块移动许可的翻译 prompt。

图片工具返回只记为 `raw_edit_candidate`。不得因 prompt 声明了 NON-TEXT LOCK 就直接交付；必须用调用前已冻结的 localization plan 运行 `compose_localization.py`，只合入文字 bbox 扣除受保护非文字区后的像素，并通过保护区及框外逐像素锁。候选返回后禁止修改计划来包住它已经误改的区域。

质量重试时只增加本次失败点，例如某一文字块拼写、漏译或边界；不得放松 NON-TEXT LOCK。初次候选加 2 次参考编辑质量重试仍失败时停止，先询问用户；没有候选的基础设施失败独立按 [quality.md](quality.md) 的 4 次预算处理。未记录明确许可时禁止使用纯生图 prompt。

## 获准后的纯重建

只有当前 item 的 `pure_rebuild_approval` 已绑定本次 `manifest_id + task_id + source_sha256`、对应 3 次 `reference_edit` 质量失败且保存本图用户许可证据后才能使用。全局开关、旧任务授权和同批其他图片授权均无效。先在 prompt 中逐项列出 source content inventory：全部商品/照片/图标/Logo/徽章/边框/色块、数量、顺序、相对位置和文字块。纯重建仍不得擅自美化或增删；若不能守住内容清单，报告失败。

## 商品几何锁

涉及商品的 edit、localization、Logo 冲突重排和比例转换加入：

```text
PRODUCT GEOMETRY LOCK: Preserve the product's natural aspect ratio, silhouette, thickness, width-to-height relationship, component proportions, material texture, and perspective. Never stretch, squash, widen, narrow, flatten, elongate, or locally enlarge the product. Adapt only by proportional scaling or a natural recomposition explicitly required by the user.
```

## 长译文

没有用户精确目标稿时，先在写 plan 前选定忠实、自然且不增删事实的译文；写入 `translation` 后逐字锁定。用户提供精确目标稿时直接标记 `user_exact`，禁止任何精简或改写。随后只依次尝试自然换行 → 在获准文字框内调整排版 → 适度缩小字号。不得压缩字宽、拉长字高、改变整个版式、移动非文字元素、粘连、重叠、裁切、溢出或扭曲。数量、型号、尺寸、单位和核心含义不可删。

## Logo 冲突重排

详细判定以 [logo.md](logo.md) 为唯一真源。`direct_overlay` 不调用图片工具。只有信息模块与 `VISIBLE_BBOX_PIXELS` 实际相交时，才对无 Logo 底图使用：

```text
SOURCE CONTENT LOCK: Preserve one-for-one every original product, photo, person, text block, icon, gift thumbnail, badge, label, border, color block, quantity, order, and relationship listed in {SOURCE_INVENTORY}. Add nothing and omit nothing.

ACTUAL COLLISION: The future Logo visible pixels occupy {VISIBLE_BBOX_PIXELS}. Move only the complete conflicting information module {CONFLICTING_MODULES}; do not move unrelated content. A thumbnail/icon/badge and its attached wording are one indivisible module.

TOP-LEFT LOGO BALANCE: Use {SAFE_ZONE_PIXELS} only as the comfortable post-rearrangement spacing boundary. It is an invisible layout constraint, not the collision test. Never draw its border, a white box, a panel, a top bar, a placeholder, or a full-width blank strip. Preserve the original background naturally through it.

MODULE ANCHOR: Place the nearest moved information-module outer edge inside {RIGHT_MODULE_START_RANGE} when using the right layout, or {BELOW_MODULE_START_RANGE} when using the below layout. The safe zone already contains the gap; do not add another gap. Choose the smallest-change layout that remains balanced.

FAMILY LAYOUT LOCK: Follow accepted pilot {FAMILY_PILOT}: {DIRECTION}, {MODULE_ANCHOR}, {TYPE_HIERARCHY}, {MODULE_SPACING}. Preserve this image's own wording and product; do not invent a different layout strategy.

Do not draw, imitate, spell, or include the Logo itself. The exact asset will be overlaid afterward.
```

## 对象、背景、合成与透明图

- 删除对象：描述删除后的合理补全，并锁定其余区域。
- 替换对象：明确位置、尺度、透视、光线和接触阴影。
- 换背景：锁定前景轮廓、材质和商品几何，只改变背景。
- 合成：逐张声明只抽取的素材，禁止携带无关主体或文字。
- 透明背景：主体边缘干净、无文字水印；宿主不能输出透明通道时如实说明，不静默切换服务。

## BATCH_STYLE_LOCK

只共享用户明确要求的抽象视觉规范，不共享单图商品、译文、参数、构图、参考图或会话上下文。自动 `layout_family_lock` 只在同系列内部引用已验收 pilot。
