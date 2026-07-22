# xobi-img Localization Glossary

## 语言风格

### 英语

- 商品图标题默认沿用当前源图的大小写层级；只有源系列本来统一，或用户明确启用 `batch_style_lock` 时，才在同批统一 Title Case 或全大写。
- 促销角标可用 `FREE GIFT`、`UPGRADED` 等自然表达，但颜色、字重和醒目程度沿用源角标，不借翻译重设计。
- 正文要自然，不要机器直译。

### 泰语

- 使用能完整显示泰文连接与附标的清晰字体，并在目标语言可实现范围内匹配源图字体视觉；不得为了“广告感”改变原层级或重设计。
- 避免缺字或机械默认字体效果，但不能因此新增底板、描边或装饰。
- 标题字重默认匹配源标题；只有源标题本来加粗或 plan 明确记录时才加粗。
- 正文字距适中，不能堆叠错乱。
- 保持和原图色块、标签、图标风格一致。

### 日语

- 使用自然、简洁的日本电商表达，避免中文式直译。
- 片假名、数字、型号、单位和标点必须准确。
- 标题与正文使用清晰的日文字体视觉，不能出现缺字、乱码或不自然断行。

### 印尼语 / 越南语 / 马来语 / 西班牙语等

- 要使用对应语言自然电商表达。
- 不要逐字机械翻译。
- 字体必须完整支持目标语言，并尽量匹配源图字体视觉；不得自行改成另一套设计风格。

### 阿拉伯语

- 文字块内部使用从右到左书写；块内对齐与 `writing_direction` 必须写入 `text_layout_adaptation`。保持原比例时 `target_bbox` 必须等于 `source_bbox`，不得移动或扩大文字模块；只有用户明确指定新比例时，才可按已批准的最小画布适配重算坐标，块顺序、信息层级和非文字布局仍保持。
- 不要打乱字符连接。
- 标签和按钮内文字要保持可读。

## 规格、数量、单位

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
1个 = 1 pc
2个 = 2 pcs
一包 = 1 pack
一对 = 1 pair
一套 = 1 set
3件套 = 3-piece set
送1包图钉 = Includes 1 pack of pins
```

默认：

- 英文标准优先 `1 pc / 2 pcs`。
- 如果用户明确喜欢 `1pcs / 2pcs`，就统一用这个风格。
- 不允许把 `1包` 翻成 `1 pc`。
- 不允许漏掉数量。

尺寸、数字、分隔符、空格和大小写默认逐字保持；只翻译语言中的单位词，不得借翻译统一格式。只有用户明确要求格式规范化时才可使用例如：

```text
3.5mm -> 3.5 mm 或 3.5mm，整套保持一致
4CM -> 4 cm 或 4CM，整套保持一致
90*210cm -> 90 × 210 cm
```

`target_text_source=user_exact` 时上述术语和格式建议全部让位于用户给出的 `requested_target_text`，逐 Unicode 字符保持。

## 铝箔隔热棉

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

## 门帘 / 磁吸门帘

```text
门帘 = Door Curtain
防蚊门帘 = Anti-Mosquito Door Curtain / Insect Screen Door
磁吸门帘 = Magnetic Screen Door
魔术贴 = Hook-and-Loop Tape / Adhesive Hook-and-Loop Tape
加宽魔术贴 = Extra-Wide Hook-and-Loop Tape
顶部加宽 = Extra-Wide Top Strip / Widened Top Edge
两侧魔术贴 = Side Hook-and-Loop Tape
送图钉 = Includes Pins
送1包图钉 = Includes 1 Pack of Pins
静音设计 = Quiet Closure
内置磁铁 = Built-in Magnets
包边布 = Reinforced Edge Fabric
加密网纱 = Dense Mesh
防尘防虫 = Keeps Dust and Bugs Out
通风透气 = Breathable Mesh
适用木门/铁门/大理石门/不锈钢门/瓷砖门框 = Suitable for wooden, metal, marble, stainless steel, and tile door frames
```

只有源文明确出现“磁吸”或“1包”时才能加入对应含义；不得根据商品外观或常见套装自行补全。
