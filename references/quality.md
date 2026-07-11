# fanyi Quality Checks

## 当前图片文字锁

为了防止模型自由发挥、把其它图片内容串进来，批量脚本必须尽量为每张图建立“当前图片文字锁”。

```text
当前图片文字锁 = 当前这一张源图中可见的文字块清单 + 文字块数量 + 大概位置。
输出结果只能包含这些文字块的目标语言翻译。
输出结果如果明显多出文字块、参数栏、卖点栏、图标标签、赠品、尺寸厚度等原图没有的内容，必须判定为失败，不能计入成功。
```

执行要求：

- 每处理一张图，日志里必须记录当前源图路径、当前输出路径、当前 prompt 只针对这一张。
- 如果能做 OCR/视觉识别，先识别当前源图的可见文字块，写入 prompt 的 `VISIBLE SOURCE TEXT BLOCKS`。
- prompt 中可以明确写：`The visible source text blocks are approximately: ...`。
- 对特别简单的图，例如原图只有 `卷收悬停`，prompt 必须额外强调：`The source image has only one text block. The output must have only one translated text block.`
- 输出后必须做人工/脚本质检记录。
- 重生时 prompt 必须更严：`Previous attempt failed because it added extra text. Generate again with ONLY the existing source text translated.`

典型失败示例：

```text
原图只有：卷收悬停
输出却出现：Stay Cool, Heat Insulation, UV Protection, Privacy Protection, FREE GIFT, THICKNESS, SIZE, 3.5mm, 90×210cm
=> 失败，不能保存为成功图，必须重生。
```

## 批量并发、重试、补缺

并发规则：

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

## 批量结束核对

批量结束后必须核对：

```text
成功生图数 + 无文字/空白图处理数 + 已存在跳过数 == 输入图片总数
```

如果用户要求 1:1，还必须核对每张输出图片宽高相等。

最终优化启用时，还必须核对：

```text
最终优化成功数 + 最终优化跳过数 == 输入图片总数
每张最终成品图片都是 800x800 JPG
每张最终成品图片体积都在 900KB-1024KB
```

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
无文字/空白图处理数
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

## 主图 / 详情 / SKU 质检

### 主图

- 画面干净、醒目。
- 只翻译原图已有文案。
- 适合平台主图，但不能为了“主图效果”新增卖点或装饰文字。
- 原图没有小字，就不要生成小字。

### 详情

- 只翻译详情图原本存在的说明。
- 必须分段、分层级、保证可读。
- 长段落可在同义范围内压缩成短句，但不能新增原文没有的功能、参数、承诺。

### SKU

- 规格、型号、尺寸、颜色、厚度、数量必须以原图可见文字为准。
- 文件名或 SKU 名只用于命名输出文件，不能进入图片画面。
- 不要为了美观删掉 SKU 关键信息。
- 不要把 SKU 文件名中的规格/颜色/属性补进图里，除非这些字在原图里可见。
