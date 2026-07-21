# xobi-img 工作流

## 任务确认矩阵

| 模式 | 开工必填 | 可选信息 |
|---|---|---|
| generate | 生成目标、输出比例 | 用途、风格、色彩、文字、变体数 |
| edit | 修改项、输出比例/保持原比例、目标图 | 精确尺寸、风格参考、Logo、素材 |
| localization | 目标语言、输出比例/保持原比例、源图 | 精确尺寸、术语偏好、是否参考图编辑 |
| batch | 单图模式所需信息、整批操作 | 是否统一视觉、覆盖/补缺、ZIP |

只给素材没有操作说明时必须停下询问。不要把“帮我处理一下”“做一下”猜成翻译、换背景或美化。

## 输入角色

多图先建立角色表：

- `target`：要编辑或翻译的目标图；
- `style_reference`：只提供风格；
- `logo`：只提供 Logo；
- `asset`：要合成的素材；
- `layout_reference`：只提供版式。

角色不明确时先问。不得把参考图主体、文字或产品误带入目标图。

## 跨平台任务目录

用户指定输出路径时优先使用。否则：

```text
有输入文件/目录：<输入父目录>/xobi-img-output/<安全任务名>-YYYYMMDD-HHMMSS/
纯文字生图：<当前工作目录>/xobi-img-output/<安全任务名>-YYYYMMDD-HHMMSS/
```

批量目录：

```text
任务目录/
  source/       # 只读源图副本或路径清单
  output/       # 最终成品
  work/         # 中间图、风格锁、联系表
  manifest.json
  report.md
```

使用 `pathlib` 处理路径和 UTF-8 JSON/Markdown，不写死盘符、用户名、斜杠或宿主工作区。

## generate

明确主题、用途、比例、构图、场景、光线、材质、色彩和禁用元素。用户要求精确文字时逐字引用并验收；未要求文字时保持完全无字。

## edit

先查看源图并记录尺寸、比例、主体、背景、光线、阴影、文字和未指定区域。只改变用户点名内容。默认参考图编辑；若宿主不支持参考图编辑，必须说明限制，不能假装完成。

### 添加 Logo

1. 先确定最终画布尺寸。所有比例按短边归一化：`scale = min(width, height) / 4000`，Logo 模板及其内部位置、安全边距统一乘此 scale，并锚定左上角 `(0,0)`。
2. 对每张最终画布分别运行 Logo 脚本 dry-run 获取 safe_zone；1:1 的坐标不能复用到 16:9、9:16 或其他尺寸。
3. 查看底图，逐项判断文字、产品、Logo 以外的重要内容是否进入 safe_zone。
4. 有冲突时先生成无 Logo 新底图：保留原有全部文字和内容，只重新安排冲突区域，效果应类似把顶部标题移到 Logo 右侧，而不是删除标题或直接覆盖。
5. 再次查看底图，确认 safe_zone 为空。
6. 使用真实模板确定性叠加 Logo；禁止 AI 生成 Logo。
7. 查看最终图，确认 Logo 完整、大小和位置一致，且没有遮挡任何文字或产品。

800×800 默认可先运行：

```text
python scripts/apply_logo.py --input <无Logo底图> --output <最终图> --dry-run
python scripts/apply_logo.py --input <无Logo底图> --output <最终图> --safe-zone-approved
```

例如短边为 800 时 scale=0.2；短边为 900 的 1600×900 或 900×1600 图片时 scale=0.225。横竖画布只改变可用空间，不改变 Logo 纵横比或左上角锚点。

## localization

默认纯生图重建：视觉查看当前源图，写出商品/构图描述、文字锁和准确译文，再直接生成，不传源图作为生成参考。用户明确要求编辑原图时才使用参考图。

只翻译可见原文。无文字图片只在用户要求比例、修复或其他修改时处理，并保持无字。1:1 既有翻译规格可在生成验收后运行 `optimize_images.py --preset localization-square`。

## batch 与 ZIP

运行预检脚本创建任务目录和唯一清单：

```text
python scripts/preflight_images.py --input <路径> --mode <edit|localization> --operation <修改摘要> --ratio <比例|original> [--target-language <语言>] [--workers 4]
```

- 一张输入图对应一个 task；不把多图同传当批量。
- ZIP 解压前检查路径穿越，忽略隐藏系统元数据，逐图处理并保留文件名主体。
- 输入数、应处理数、成功数、跳过数和失败数必须与 manifest 和磁盘一致。
- 补跑沿用原 task_id，跳过已成功成品。
- 每张图成功、跳过或失败后立即运行 `scripts/update_manifest.py` 更新当前 task，禁止等整批结束才凭记忆补写。
- 交付 ZIP 默认只含 output 目录最终成品。

## 批量视觉统一

只有明确要求整批视觉/排版/系列风格统一时启用。写入 `work/batch_style_lock.json`，至少包含版本、画布、字体视觉、层级、对齐、边距、间距、色板、标签风格、商品尺度和 `content_isolation=true`。

完成后运行：

```text
python scripts/create_contact_sheet.py --input <任务目录/output> --output <任务目录/work/batch_contact_sheet.jpg
```

查看总览后只重生异常图；共享基准错误或用户明确要求全部重做时才整批重做。

## 最终优化

普通生图、透明 PNG、保持原比例和非 1:1 输出不得自动运行方图优化。只有用户明确要求确定尺寸/格式/体积，或 localization 使用既有方图规格时运行：

```text
python scripts/optimize_images.py --input <原始成品目录> --output <最终目录> --preset localization-square
```

已有不合格输出需要重做时显式加 `--overwrite`。
