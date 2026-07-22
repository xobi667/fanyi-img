# xobi-img 工作流

## 任务确认矩阵

| 模式 | 开工必填 | 仅在影响结果时补问 |
|---|---|---|
| generate | 生成目标、输出比例 | 用途、风格、色彩、精确文字、变体数 |
| edit | 目标图、修改项、输出比例/保持原比例 | 精确尺寸、参考角色、素材角色 |
| localization | 源图、目标语言、输出比例/保持原比例 | 术语偏好 |
| batch | 单图模式必填项、整批操作 | 是否显式统一全批视觉 |

门禁前可以只读列目录、查看图片和读取元数据，以辨认目标图、Logo、参考图与素材；禁止生成、编辑、创建任务目录或输出成品。缺什么只问什么。只给素材而没有操作说明时不得把“做一下”猜成翻译、美化或换背景。

用户已给同语言的精确替换对时走 `edit/text_replacement`，无需目标语言；需要把现有文字翻成另一语言时才走 localization。

## 输入角色

多图先建立角色表：

- `target`：要编辑或翻译的目标图；
- `style_reference`：只提供风格；
- `logo`：只提供 Logo；
- `asset`：要合成的素材；
- `layout_reference`：只提供版式。

角色不明确时只追问不明确的部分。预检时使用 `--logo`、重复 `--exclude` 或 UTF-8 `--roles-file`，禁止把 Logo/参考素材当 target。角色文件可以是 `{"相对路径":"role"}`，也可以是包含 `path`、`role` 的对象数组。

## 跨平台任务目录

用户指定输出路径时优先使用。否则：

```text
有输入文件/目录：<输入父目录>/xobi-img-output/<安全任务名>-YYYYMMDD-HHMMSS/
纯文字生图：<当前工作目录>/xobi-img-output/<安全任务名>-YYYYMMDD-HHMMSS/
```

```text
任务目录/
  最终成品
  .xobi/
    source/
    work/
      task-state/
    manifest.json
    report.md
```

使用 `pathlib` 和 UTF-8；不写死盘符、用户名、斜杠、provider 或宿主工作区。原图不覆盖，任务根目录不放中间图。

宿主的“最接近画幅”只可保存为 `.xobi/work/` 下的 base；manifest 预分配的 final 必须符合已确认的精确宽高比或像素尺寸。只能等比缩放主体、自然扩展背景或按已授权 plan 重排，禁止拉伸后硬凑比例。

## generate

确认主题与比例后，描述构图、场景、光线、材质、色彩和禁用元素。未要求文字时保持完全无字。多个成品/变体拆成独立 task 和独立图片调用，不让四个 worker 重复生成同一成品。

纯文字单图和批量生图都先创建无输入 manifest：

```text
python scripts/preflight_images.py --mode generate --operation <生成摘要> --ratio <比例|宽×高> [--variants <正整数>] [--output-format <png|jpg|jpeg|webp|bmp|tiff>] [--alpha-policy <preserve|required|forbidden>] [--workers 4]
```

- generate 不传 `--input`，也不允许 `original`/“保持原比例”或 `--output-format source`；必须使用已确认的明确比例或尺寸。
- `--variants` 默认为 1。每个变体预分配独立的 `variant-001`、`variant-002`……task 与同名成品路径，item 的 `source` 为空，禁止四路重复处理同一变体。
- worker 数为 `min(4, --workers, 变体数)`；每个 worker 仍逐项串行，失败与降级规则和输入型 batch 相同。

## edit

查看当前目标图，记录主体、照片、图标、文字、边框、背景、光线、阴影和布局。把用户未点名区域列入 unchanged lock。默认传当前目标图作为唯一 target reference；宿主不支持参考编辑时报告限制，不能假装完成。

## localization

严格执行 [localization.md](localization.md)。默认流程是：

1. 查看源图；把纯背景承载面和每个有界非文字 element 分别写入结构化 `non_text_inventory`，由脚本重算每个文字框必须保护的相交 element；单图和批量都要在调用图片工具前为每个 task 落盘独立的 `.xobi/work/<task_id>-localization-plan.json`。
2. 用单独的 `status=pending` 更新登记并冻结计划；此时 attempts 必须为 0，不能同时传候选、失败或 success。冻结 artifact 的路径、哈希和内容随后由 update/verify 持续复核。
3. 把当前源图作为参考，执行 `text_only_reference_edit`，把图片工具返回只保存为 `.xobi/work/raw-edit-candidate-*`。
4. 运行 `compose_localization.py`，只把冻结计划中扣除所有相交非文字 element 后的文字掩膜合回 source，得到 lossless PNG `localized_base` 和 provenance；success 用 `--localization-composition-json` 登记 provenance，update/verify 从结构化 inventory、raw candidate 和冻结 plan 重新合成核对。漏保护、缩小保护区、框外变化或全局构图门禁失败均不得继续。
5. source/raw_edit_candidate/localized_base/final 并排检查非文字锁和文字块一一对应。每次图片调用连续登记 attempt，验收通过的候选也计入当前阶段质量预算。只有最终也是同尺寸 PNG 且没有 Logo 时，localized_base 与 final 可以指向同一文件；其他格式或尺寸使用 `resample_image.py` 确定性派生。
6. 质量失败最多做 2 次针对性参考编辑重试；每次用 `--failure-type quality --attempt-stage reference_edit` 记录到当前 task。
7. 当前图累计 3 次参考编辑质量失败后停下询问；第 4 次成功同样拒绝。只有当前 item 已记录绑定 `manifest_id + task_id + source_sha256` 的用户明确许可，才可纯生图重建。旧任务或同批另一图片的许可无效，冻结计划本身仍不修改；进入纯重建后不能退回参考编辑。

不得因为“AI 参考编辑难”跳过第 4、5 步。只说“翻译”不等于同意美化、重排、换图、增删卖点或纯重建。

当前 localization 硬像素锁只接受 `ratio_adaptation.required=false`。新比例缺少结构化、可重算坐标映射时 fail closed；自由文本 allowed_changes 不能放行。需要新比例时报告该限制并另行确认，不得用输出比例为未记录的改版辩护。

若用户给的是相同宽高比但不同的精确像素尺寸，把 `ratio_adaptation.required` 保持为 `false`，并记录 `target_size`、`size_resample={"required":true,"method":"whole_canvas_lanczos"}`。先验收源尺寸 `localized_base`，再整图等比确定性重采样得到 `final`；组合 Logo 任务则先得到 `prepared_base` 再叠加 Logo。不得用图片模型重新生成尺寸版。

```text
python scripts/resample_image.py --input <已验收localized_base> --output <manifest预分配final或prepared_base> --size <宽x高> --output-format <png|jpg|jpeg|webp|bmp|tiff|source>
```

该脚本只接受与 `localized_base` 宽高比完全相同的尺寸，使用 LANCZOS、原子写入且禁止覆盖输入；透明图转 JPG/BMP 会直接拒绝，不能静默铺底。用户要求与 source 同尺寸的 JPG/WebP/BMP/TIFF 时，也要以 localized_base 原尺寸运行该脚本做一次固定参数编码；直接另存或二次有损编码无法通过 stage derivation。

## 添加 Logo

完整规则只读取 [logo.md](logo.md)。工作顺序固定为：

1. 看完全部目标图并确认本次 Logo 资产、角色和哈希；只有用户明确要求时才允许默认模板。必要时先 dry-run 清理外围边，正式清理必须用 `--manifest` 登记规范化谱系，后续只使用 active Logo。
2. 对每张最终尺寸运行 `apply_logo.py --dry-run --geometry-json ...`。
3. 用 `visible_bbox` 判断真实冲突，写 `logo_plan.json` 和 layout family；需要重排时先冻结最终尺寸、无 Logo、移动前的 `conflict_reference_base`。组合翻译由已验收 `localized_base` 确定性编码/重采样得到，generate 保存发现冲突时的初始基底，普通同尺寸 edit 才可默认使用 source。
4. 无冲突图直接叠加；有冲突的 family 先做并验收 pilot，再冻结布局并处理成员。
5. 把移动后的最终尺寸无 Logo 底图记录为 `prepared_base`；运行逐模块 relocation guard，确认 `conflict_reference_base` 原 bbox 已清除、`prepared_bbox` 存在对应模块且多模块没有交换。保存可重算 `logo_relocation_validation` 后再叠加真实 Logo，叠加后不再交给 AI。
6. 生成按 family 分组的 source/prepared_base/final 三联联系表并验收；组合翻译任务另保留 localized_base。

```text
python scripts/normalize_logo.py --input <原始Logo> --output <.xobi/work/normalized-logo.png> --background <white|solid|transparent> --metadata-json <.xobi/work/logo-normalization.json> --manifest <.xobi/manifest.json>
python scripts/apply_logo.py --input <底图> --output <最终图> --logo <active Logo> --dry-run --geometry-json <.xobi/work/logo_geometry.json>
python scripts/apply_logo.py --input <底图> --output <最终图> --logo <active Logo> --safe-zone-approved [--opaque-approved]
```

`update_manifest.py --logo-geometry-json` 只接受上述 dry-run 产生并保存在 `.xobi/work` 的完整 wrapper 文件，拒绝手写裸 bbox；成功登记时同时传 `--logo-plan-file`，冲突 family 还必须传已冻结的 `--layout-families-file`。

## 输入型 batch 与 ZIP

```text
python scripts/preflight_images.py --input <路径> --mode <edit|localization> --operation <摘要> --ratio <比例|宽×高|original> [--target-language <语言>] [--output-format <png|jpg|jpeg|webp|bmp|tiff|source>] [--alpha-policy <preserve|required|forbidden>] [--logo <Logo>] [--exclude <路径或glob>] [--roles-file <JSON>] [--workers 4]
```

- 未指定时输出格式为 PNG，透明策略为 `preserve`。明确需要透明背景时使用 `--alpha-policy required`；不支持透明像素的 JPG/BMP 与 `required` 组合必须在预检时拒绝。
- 一张 target 对应一个 task；同 stem 的不同扩展名会预分配唯一输出，不能互相覆盖。
- ZIP 在解压前拒绝重复、仅大小写不同或路径穿越的 member。
- PSD/PSB 写入 `unsupported_inputs` 并跳过，不偷偷转换、不安装强制依赖。
- 每个 task 完成后通过 `update_manifest.py` 写独立 state 并在锁内合并；禁止直接改共享 JSON。

```text
python scripts/update_manifest.py --manifest <.xobi/manifest.json> --task-id <id> --worker-id <worker> --status pending --localization-plan-json <.xobi/work/<task_id>-localization-plan.json>
python scripts/compose_localization.py --source <原图> --candidate <raw_edit_candidate> --output <.xobi/work/localized-base.png> --plan <冻结计划> --provenance-json <.xobi/work/localization-composition.json>
python scripts/update_manifest.py --manifest <.xobi/manifest.json> --task-id <id> --worker-id <worker> --status success --attempts <n> --output <预分配路径> --localized-base <无损文字框合成底图> --localization-composition-json <composition provenance> [--prepared-base <Logo前底图>]
```

Localization 计划登记必须发生在任何图片 attempt 之前；success 命令不得首次传计划，候选返回后不得扩大 bbox 或改写译文。reference-edit success 必须登记可重算 composition provenance。失败记录使用 `--failure-type quality --attempt-stage reference_edit`；三次失败后，另用当前 task 的 `--pure-rebuild-approval <本图明确许可原文>` 写入任务级授权。纯重建质量失败和最终 success 都显式使用 `--attempt-stage pure_rebuild`，不传 text-box composition provenance，但冻结的 text-only plan 不变。不得手改或复制其他 task 的计划登记、composition 或授权块。

`attempts` 是当前 task 从开工起单调递增的总调用序号，不能在切换阶段后归零；阶段预算按 `attempt_stage` 分开计算。因此 3 次参考编辑后，首个获准纯重建使用下一总序号，但仍是纯重建阶段的第 1 次质量尝试。

`success` 会验证文件存在、位于任务根目录、不是源图/Logo、路径唯一、扩展名与真实编码格式一致、透明像素契约、比例/精确尺寸正确，并记录 SHA-256；与其他成功图内容哈希相同会被拒绝。

## 四路、重试与单路降级

1. 宿主明确禁止并行：开工即 `workers=1`。
2. 否则使用 `min(4, slots, tasks, host_limit)`；各 worker 只处理自己的 task，内部逐张串行。
3. Logo 同系列只有存在冲突重排时才进入 pilot 屏障；pilot 验收与 family lock 冻结前，不派发其余重排成员。全为 direct_overlay 的系列无需 pilot。其他模式只有用户要求共享布局或风格时才设置相应屏障。
4. 返回可验收候选即计该阶段 quality attempt；未通过时只对当前图做最多 2 次针对性重试。没有候选的基础设施错误独立计数，初次调用后最多重试 3 次并等待 2/5/10 秒。混合结果分别计数，总调用序号不归零。
5. 两个 worker 出现同类基础设施错误后，取消尚未执行的并行退避重试并暂停派发；选择最早的受影响 pending task，沿用其原始参考图、prompt 和隔离输出做一次单路探针。探针按实际结果计入该 task，禁止无归属调用。
6. 探针成功并完成候选验收：记录降级，`workers=1` 补 pending；探针失败：保留已成功项并报告阻塞。
7. 默认不重跑 success。用户明确要求重做某些任务或全部任务，或共享 family/style lock 被证明错误时，只新建并重做明确受影响范围。

## 风格锁与系列锁

- `batch_style_lock`：只有用户明确要求整批视觉统一时启用，约束全批抽象视觉规范。
- `layout_family_lock`：自动识别两张以上同系列时启用，只约束该系列的标题方向、层级、模块关系和间距。

它们互不替代。只有需要共享冲突重排布局的 family 先 pilot 再并行；全为 direct_overlay 的 family 不设 pilot。不同 family 不共享商品、文案、局部构图或会话参考图。

## 最终验收与交付

```text
python scripts/create_contact_sheet.py --manifest <.xobi/manifest.json> --output <.xobi/work/source-base-final.jpg>
python scripts/verify_manifest.py --manifest <.xobi/manifest.json>
```

只有验证通过才能声称“全部完成、无遗漏、无重复”。交付 ZIP 只包含任务根目录最终成品，排除 `.xobi/`。

普通生图、透明 PNG、保持原比例和非方图输出不得自动运行方图优化。只有用户明确要求尺寸/格式/体积，或当前对话已经确认既有规格时，才运行 `optimize_images.py`；不得把历史 800×800 JPG 规格套到所有翻译任务。
