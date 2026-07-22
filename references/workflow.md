# xobi-img 工作流

## 任务确认矩阵

| 模式 | 开工必填 | 仅在影响结果时补问 |
|---|---|---|
| generate | 生成目标、输出比例 | 用途、风格、色彩、精确文字、变体数 |
| edit | 目标图、修改项、输出比例/保持原比例 | 精确尺寸、素材角色 |
| localization | 源图、目标语言、输出比例/保持原比例 | 术语偏好、模糊文字 |
| 添加 Logo | 目标图、本次 active Logo 资产、输出比例/保持原比例 | 外围边清理方式、精确尺寸 |
| batch | 单图模式必填项、整批操作 | 是否显式统一全批视觉 |

门禁前可以只读列目录、查看图片和读取元数据，以辨认目标图、Logo、参考图与素材；禁止生成、编辑、创建任务目录或输出成品。缺什么只问什么。只给素材而没有操作说明时不得把“做一下”猜成翻译、美化或换背景。

用户已给同语言精确替换对时走 `edit/text_replacement`，无需目标语言；需要把现有文字翻成另一语言时走 localization。

## 默认图片调用方式

新 manifest 的图片模型策略必须精确等价于：

```json
{
  "default": "pure_generation",
  "reference_images_allowed": false,
  "logo_exception": ["deterministic_overlay", "conflict_relocation"]
}
```

`reference_images_allowed=false` 同时覆盖 target/source/reference/attachment/recent-image 和宿主等价字段；唯一模型参考例外是已通过真实冲突门禁的 `logo_conflict`，确定性 Logo overlay 本身不调用模型。

- generate、edit 和 localization 全部默认调用无参考图的原生纯生图。协调者可以查看源图、盘点文字和视觉内容，但图片模型调用不传 target、source、reference、附件、最近会话图片或任何隐式图片上下文。
- edit 执行 `pure_generation_edit`，整张重建但只允许用户点名项变化；其余商品、文字、对象、数量、背景、位置和版式锁定。
- localization 执行 `pure_generation_localization`，整张重建但只允许把已有文字替换为译文；商品、内容、数量、背景和版式锁定。
- 图片模型返回完整候选。候选通过验收后直接作为成品视觉内容；除 Logo 最后一步确定性叠加外，不使用本地蒙版、裁贴、文字框合成或像素回填。
- 只有用户明确要求添加 Logo 并确认本次 `logo` 资产时才启用 Logo 例外；源图已有 Logo 或盘点时发现 Logo 都不算。该添加任务中唯一的参考编辑例外是 Logo 冲突底图：本次 active Logo 会遮挡信息模块时，可把尚未叠加本次 active Logo 的 `conflict_reference_base` 作为唯一参考，只重排冲突模块；源图原有 Logo 仍必须保留。

## 输入角色

多图先建立角色表：

- `target`：要编辑或翻译的目标图；
- `style_reference`：只供协调者查看并转写风格；
- `logo`：只用于最后确定性叠加；
- `asset`：只供协调者盘点并转写允许合成的素材；
- `layout_reference`：只供协调者查看并转写版式。

角色不明确时只追问不明确部分。预检时使用 `--logo`、重复 `--exclude` 或 UTF-8 `--roles-file`，禁止把 Logo/参考素材当 target。默认纯生图调用仍不附带上述图片；角色只控制盘点、prompt 和 Logo 专属流程。

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

## generate

确认主题与比例后，描述构图、场景、光线、材质、色彩和禁用元素。未要求文字时保持完全无字。多个成品/变体拆成独立 task 和独立图片调用，不让四个 worker 重复生成同一成品。

```text
python scripts/preflight_images.py --mode generate --operation <生成摘要> --ratio <比例|宽×高> [--variants <正整数>] [--output-format <png|jpg|jpeg|webp|bmp|tiff>] [--alpha-policy <preserve|required|forbidden>] [--logo <Logo>|--use-default-logo] [--workers 4]
```

generate 不传 `--input`，也不允许 `original`/“保持原比例”或 `--output-format source`；必须使用已确认比例或尺寸。`--variants` 默认为 1，每个变体预分配独立 task 和成品路径。

generate 可登记本次 active Logo，但该资产只供后续冲突判断和确定性叠加，绝不作为纯生图参考，也不得出现在第一阶段生成内容中。先验收“尚未叠加本次 active Logo”的生成 base，再执行 Logo 流程。

## edit

1. 查看目标图，逐项记录主体、商品、照片、人物、图标、Logo、文字、数量、边框、背景、光线、阴影、裁切、位置和布局。
2. 把用户点名变化写入 `allowed_changes`，把其余内容写入完整 `unchanged_content_lock`。
3. 组装 `PURE GENERATION EDIT` prompt，明确 `REFERENCE INPUT: NONE`；原生图片调用不传目标图。
4. 逐图验收候选。只有点名变化完成且未点名内容都保持时才成功；否则从同一纯生图阶段最多针对性重试 2 次。

用户明确新比例时，把 `minimal_canvas_adaptation`、`proportional_subject_scaling` 和确有必要的布局适配逐项写入本次 `allowed_changes`；未登记的原位置与版式仍锁定。禁止借新比例拉伸商品或自由重排。

宿主无法用无参考纯生图完成时报告失败，不得静默切换参考编辑或本地编辑。

## localization

严格执行 [localization.md](localization.md)：

1. 查看源图，逐块冻结原文、准确译文、位置、角色、顺序和排版，并盘点全部商品、照片、人物、Logo、图标、徽章、边框、色块、背景、阴影、纹理、数量、裁切、位置、构图和版式。
2. 在 attempts=0 时为每个 task 落盘并登记独立 `.xobi/work/<task_id>-localization-plan.json`，固定 `mode=pure_generation_localization`、`reference_policy=none`、逐块译文和完整内容锁。
3. 使用 `PURE GENERATION LOCALIZATION` prompt 调用原生图片模型，不传源图、参考图或最近会话图片；返回完整 `.xobi/work/pure-generation-candidate-*`。
4. 把 source/candidate 并排查看，逐字核对译文，并逐项核对所有非文字内容、数量、商品、背景、位置、构图和版式。候选只要出现额外变化就失败。
5. 验收通过的完整候选直接成为 final 的视觉内容；允许复制、移动、重命名和登记哈希，不得运行 `compose_localization.py`、文字框蒙版、局部裁贴、像素回填或第二次 AI 编辑。
6. 每次图片调用连续登记 `attempt_stage=pure_generation`。初次结果后最多 2 次针对性重试，每图共 3 个 quality attempts；三次仍失败就停止并报告，不能登记第 4 次成功。

不存在“先参考编辑三次，再取得纯生图授权”的流程；`text_only_reference_edit`、`pure_rebuild_approval` 和 composition provenance 只允许离线读取、验证、诊断或导出旧 manifest。旧任务不得新增 reference-edit/pure-rebuild 图片 attempt；要继续处理必须迁移到当前无参考纯生图策略。

保持原比例时锁定画布与版式。用户明确新比例时，`ratio_adaptation.allowed_changes` 只能登记 `minimal_canvas_adaptation`、`proportional_subject_scaling` 和 `necessary_text_reflow`，不授权改商品形状、背景风格、信息数量和层级；若新比例与“不改版式”无法兼得，开工前确认选择。宿主不能直接生成精确规格时报告限制，不默认本地重采样。

## 添加 Logo

完整规则只读取 [logo.md](logo.md)。工作顺序固定为：

1. 看完全部目标图并确认本次 Logo 资产、角色和哈希；只有用户明确要求时才允许默认模板。必要时先 dry-run 清理外围边，正式清理登记规范化谱系。
2. 对每张最终尺寸运行 `apply_logo.py --dry-run --geometry-json ...`，用 `visible_bbox` 判断真实冲突。
3. 仅添加 Logo 时，源图或用户明确要求的确定性尺寸/格式转换结果直接作为待叠加 base，不先运行 `pure_generation_edit`。组合任务只有在还要求生成、普通编辑或翻译时，才先完成对应的无参考纯生图阶段；第一阶段禁止生成本次 active Logo。
4. 无冲突时 Logo 阶段不调用图片模型，直接把合格底图记录为 `prepared_base`；有冲突时写 `logo_plan.json` 和 layout family，把尚未叠加本次 active Logo 的底图登记为 `conflict_reference_base`。
5. `logo_conflict` 是唯一参考编辑例外：只把 `conflict_reference_base` 作为参考，只移动冲突信息模块。存在冲突的 family 先验收 pilot，再冻结布局并处理成员。
6. 运行逐模块 relocation guard，确认原位清除、目标位对应，且其他区域无变化；合格结果记录为 `prepared_base`。
7. 使用本次真实 Logo 资产做最后一步确定性叠加。叠加后禁止再次交给 AI；生成 source/conflict_reference_base/prepared_base/final 对照表并验收。

```text
python scripts/normalize_logo.py --input <原始Logo> --output <.xobi/work/normalized-logo.png> --background <white|solid|transparent> --metadata-json <.xobi/work/logo-normalization.json> --manifest <.xobi/manifest.json>
python scripts/apply_logo.py --input <底图> --output <最终图> --logo <active Logo> --dry-run --geometry-json <.xobi/work/logo_geometry.json>
python scripts/apply_logo.py --input <底图> --output <最终图> --logo <active Logo> --safe-zone-approved [--opaque-approved]
```

## 输入型 batch 与 ZIP

```text
python scripts/preflight_images.py --input <路径> --mode <edit|localization> --operation <摘要> --ratio <比例|宽×高|original> [--target-language <语言>] [--output-format <png|jpg|jpeg|webp|bmp|tiff|source>] [--alpha-policy <preserve|required|forbidden>] [--logo <Logo>|--use-default-logo] [--exclude <路径或glob>] [--roles-file <JSON>] [--workers 4]
```

- 未指定时输出格式为 PNG，透明策略为 `preserve`。明确需要透明背景时使用 `--alpha-policy required`；不支持透明像素的格式必须在预检时拒绝。
- 一张 target 对应一个 task；同 stem 的不同扩展名预分配唯一输出，不能互相覆盖。
- ZIP 在解压前拒绝重复、仅大小写不同或路径穿越的 member。
- PSD/PSB 写入 `unsupported_inputs` 并跳过，不偷偷转换、不安装强制依赖。
- 每个 task 完成后通过 `update_manifest.py` 写独立 state 并在锁内合并；禁止直接改共享 JSON。

Localization 计划登记发生在任何图片 attempt 之前；success 不得首次传计划，候选返回后不得扩大授权或改写译文。新任务的计划模式固定为 `pure_generation_localization`，普通翻译图片调用只使用 `attempt_stage=pure_generation`，不使用 `reference_edit`、`pure_rebuild` 或 `--pure-rebuild-approval`。普通 generate/edit 的第一阶段由 manifest `image_model_policy` 锁定为无参考纯生图，不使用 localization 专属 stage；任何模式真实进入 Logo 冲突时，必须在独立 pending 更新中先冻结 plan、geometry、decision、前序已接受 base 与 `conflict_reference_base`，再用后续唯一 attempt 登记 `attempt_stage=logo_conflict`。无 Logo、direct_overlay、无真实冲突和首次 attempt 都拒绝。

success 必须验证文件存在、位于任务根目录、不是源图/Logo、路径唯一、扩展名与真实编码一致、透明契约、比例/尺寸正确，并记录 SHA-256；不同 task 的最终输出路径和内容哈希不得重复。

## 四路、重试与单路降级

1. 宿主明确禁止并行：开工即 `workers=1`。
2. 否则使用 `min(4, slots, tasks, host_limit)`；各 worker 只处理自己的 task，内部逐张串行。
3. Logo 同系列只有存在冲突重排时才进入 pilot 屏障；全为 direct_overlay 的系列无需 pilot。其他模式只有用户明确要求共享布局或风格时才设置相应屏障。
4. 返回候选即计该阶段 quality attempt；未通过时只对当前图做最多 2 次针对性重试。没有候选的基础设施错误独立计数，初次调用后最多重试 3 次并等待 2/5/10 秒。
5. 两个 worker 出现同类基础设施错误后，取消尚未执行的并行退避重试并暂停派发；选择最早受影响的 pending task，沿用其冻结 prompt 和隔离输出做一次单路探针。探针按实际结果计入该 task，禁止无归属调用。
6. 探针成功并验收后记录降级，`workers=1` 补 pending；探针失败则保留已成功项并报告。
7. 默认不重跑 success。用户明确要求重做，或共享 family/style lock 被证明错误时，只重做明确受影响范围。

## 风格锁与系列锁

- `batch_style_lock`：只有用户明确要求整批视觉统一时启用，约束全批抽象视觉规范。
- `layout_family_lock`：自动识别两张以上同系列时启用，只约束该系列的标题方向、层级、模块关系和间距。

两者互不替代。只有 Logo 冲突重排 family 先 pilot 再并行；全为 direct_overlay 的 family 不设 pilot。不同 family 不共享商品、文案、局部构图或图片上下文。

## 最终验收与交付

```text
python scripts/create_contact_sheet.py --manifest <.xobi/manifest.json> --output <.xobi/work/stage-review.jpg>
python scripts/verify_manifest.py --manifest <.xobi/manifest.json>
```

翻译联系表使用 source/pure_generation_candidate/final；Logo 联系表使用 source/conflict_reference_base/prepared_base/final，direct_overlay 可省略与 source/base 重复的列。只有验证通过才能声称“全部完成、无遗漏、无重复”。交付 ZIP 只包含任务根目录最终成品，排除 `.xobi/`。只有用户明确要求额外尺寸、格式或体积转换时才执行对应本地工具；不得把后处理当默认图片生产方式，也不得借后处理改变视觉布局。
