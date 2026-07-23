# xobi-img 工作流

## 任务确认矩阵

| 模式 | 开工必填 | 仅在影响结果时补问 |
|---|---|---|
| generate | 生成目标、输出比例 | 用途、风格、色彩、精确文字、变体数 |
| edit | 目标图、修改项、输出比例/保持原比例 | 精确尺寸、素材角色 |
| localization | 源图、目标语言、输出比例/保持原比例 | 术语偏好、模糊文字 |
| commerce_main_image | 明确制作/重做/优化整张主图、目标商品/素材、平台或通用电商、视觉方向、比例、文字策略 | 平台硬规范、精确文案、变体数 |
| 添加 Logo | 目标图、本次 active Logo 资产、输出比例/保持原比例 | 外围边清理方式、精确尺寸 |
| batch | 单图模式必填项、整批操作 | 是否显式统一全批视觉 |

门禁前可以只读列目录、查看图片和读取元数据，以辨认目标图、Logo、参考图与素材；禁止生成、编辑、创建任务目录或输出成品。缺什么只问什么。只给素材而没有操作说明时不得把“做一下”猜成翻译、美化或换背景。

用户已给同语言精确替换对时走 `edit/text_replacement`，无需目标语言；需要把现有文字翻成另一语言时走 localization。

## 默认图片调用方式

新 manifest 中 generate、普通 edit 和 `commerce_main_image` 继续使用无参考纯生图；localization 单独执行恢复后的 fanyi 参考图翻译；Logo 继续使用自身的确定性叠加与冲突重排规则。

- generate、普通 edit 和 `commerce_main_image` 的图片模型调用不传 target、source、reference、附件、最近会话图片或任何隐式图片上下文。
- edit 执行 `pure_generation_edit`，整张重建但只允许用户点名项变化；其余商品、文字、对象、数量、背景、位置和版式锁定。
- localization 把当前 task 的原始源图作为唯一参考，只替换原图已有文字。不得附带失败候选、另一 task、Logo、pilot 或其他图片。
- `commerce_main_image` 只在用户明确要求制作、重做或优化整张主图时启用；无输入仍以 generate、有输入仍以 edit 预检，但用冻结艺术指导授权创意呈现。单独翻译和普通 edit 不得升级到该 route。
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

## commerce_main_image

只在用户明确要求“做主图”“重做主图”或“优化主图”等整张主图任务时读取并执行 [main-image.md](main-image.md)。用户只说“翻译主图”、修改一处、换背景或泛称“优化图片”时不走该 route。

1. 只读查看目标商品与素材，确认平台或“通用电商”、视觉方向、比例和文字策略；缺什么只问什么。
2. 无输入使用基础 `mode=generate`，有输入使用基础 `mode=edit`；manifest 另写 `workflow=commerce_main_image`，并在操作摘要、独立主图计划与报告中记录同一 workflow，不混淆基础 mode 与创意 workflow。
3. 冻结商品内容锁与艺术指导：单一焦点、商品优先、商品占比/安全边距、最少信息层、构图、尺度/透视、真实材质、光影/接触阴影、背景/色彩、保持比例长边 256/160 缩略图门禁和禁用样式。
4. 使用 `PURE GENERATION COMMERCE MAIN IMAGE` prompt 调用原生图片模型；不得传 source、target、asset、style/layout reference、最近会话图片或 pilot 图片。
5. 按 [main-image.md](main-image.md) 的真实 CLI 对 manifest 预分配 final 路径运行 `scripts/create_main_image_review.py`，把候选原始字节冻结为独立 full snapshot，再检查全尺寸/保持比例长边 256/保持比例长边 160 三档并绑定当前 candidate、冻结 plan、evidence 和 assessment。每个有候选的 attempt 都必须在同一次 update 中登记自己的 finalized review；任一审美项不合格就是带 `passed=false` review 的 quality failure，按精确原因最多针对性重试 2 次，不得因为商品和文字内容正确就通过丑图。
6. 批量按平台、比例、品类、视觉方向和文字策略分 family。每个 family 先做一张内部 pilot，三档验收通过后再并行其他成员；不要求用户逐张确认，也不把 pilot 图片作为参考。

无输入与有输入分别使用真实 preflight 参数；`--exact-text` 只随 `user_exact` 使用，`preserve_existing_exact` 只可用于有输入的 edit：

```text
python scripts/preflight_images.py --mode generate --workflow commerce_main_image --operation <明确制作/重做/优化主图摘要> --ratio <比例|宽×高> --platform-profile <平台|通用电商> --visual-direction <视觉方向> --text-policy <no_text|user_exact> [--exact-text <用户精确文案>] [--variants <数量>]
python scripts/preflight_images.py --input <目标图|目录|ZIP> --mode edit --workflow commerce_main_image --operation <明确制作/重做/优化主图摘要> --ratio <比例|宽×高|original> --platform-profile <平台|通用电商> --visual-direction <视觉方向> --text-policy <no_text|preserve_existing_exact|user_exact> [--exact-text <用户精确文案>]
```

plan 登记、三档 review 与 success 绑定固定按以下真实顺序；不得省略或合并 attempts=0 的独立 pending plan 更新。`--attempts` 使用当前全局总值加 1，不是固定候选序号；质量候选仍最多 3 个。失败 attempt 命令和 assessment 结构见 [main-image.md](main-image.md)：

```text
python scripts/update_manifest.py --manifest <任务目录/.xobi/manifest.json> --task-id <task_id> --status pending --worker-id <worker_id> --main-image-plan-json <已冻结main_image_plan.json>
python scripts/create_main_image_review.py prepare --manifest <任务目录/.xobi/manifest.json> --task-id <task_id> --candidate <manifest预分配final路径> --plan-json <已冻结main_image_plan.json> [--output-dir <.xobi/work/独立证据目录>]
python scripts/create_main_image_review.py finalize --manifest <任务目录/.xobi/manifest.json> --task-id <task_id> --candidate <manifest预分配final路径> --plan-json <已冻结main_image_plan.json> --evidence-dir <prepare输出目录> --assessment-json <已填写的绑定模板.json> [--review-json <evidence-dir内review.json>]
python scripts/update_manifest.py --manifest <任务目录/.xobi/manifest.json> --task-id <task_id> --status <pending|failed> --worker-id <worker_id> --attempts <当前总attempt+1> --attempt-stage commerce_main_image --failure-type quality --error <精确失败原因> --main-image-quality-review-json <passed=false的review.json>
python scripts/update_manifest.py --manifest <任务目录/.xobi/manifest.json> --task-id <task_id> --status success --worker-id <worker_id> --output <manifest预分配final路径> --attempts <当前总attempt+1> --attempt-stage commerce_main_image --main-image-quality-review-json <通过的review.json>
```

用户明确要求添加 Logo 时，先把尚未叠加 active Logo 的通过候选以 pending commerce attempt 同时绑定 `passed=true` review 和 `--base-output`，再进入原有 Logo 流程；该 accepted commerce attempt 立即封闭主图图片阶段，禁止再追加主图 quality/infrastructure attempt。Logo 完成时沿用该冻结 review，不重新传主图 review。Logo 的唯一参考例外和确定性叠加规则不变。

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
2. 运行 `scripts/preflight_fanyi.py`，为每张源图建立独立 task、worker、原始候选与最终输出；在第一次图片调用前冻结逐块译文和完整内容锁。
3. 使用 fanyi 参考图翻译 prompt 调用原生图片模型。当前源图是唯一参考：本地文件使用 `referenced_image_paths`，仅会话图片使用覆盖当前源图所需的最小 `num_last_images_to_include`，两者不得同时使用。返回完整 `.xobi/work/fanyi-raw-candidate-*`。
4. 把 source/candidate 并排查看，逐字核对译文，并逐项核对所有非文字内容、数量、商品、背景、位置、构图和版式。候选只要出现额外变化就失败。
5. 验收通过的完整候选直接成为无 Logo 的翻译视觉内容，或作为组合 Logo 任务的 `localized_base`。不得运行 `compose_localization.py`、文字框蒙版、局部裁贴或像素回填。
6. 每次重试都重新引用原始源图，绝不把失败候选作为下一次参考。初次结果后最多 2 次针对性重试，三次仍失败就停止并报告。
7. 1:1 且用户没有覆盖旧版交付规格时，最后运行 `scripts/final_optimize_images.py`，交付 `800×800 JPG`、`900–1024KB`；这一步只做旧版尺寸与体积优化，不改画面内容。

保持原比例时锁定画布与版式。用户明确新比例时，`ratio_adaptation.allowed_changes` 只能登记 `minimal_canvas_adaptation`、`proportional_subject_scaling` 和 `necessary_text_reflow`，不授权改商品形状、背景风格、信息数量和层级；若新比例与“不改版式”无法兼得，开工前确认选择。宿主不能直接生成精确规格时报告限制，不默认本地重采样。

## 添加 Logo

完整规则只读取 [logo.md](logo.md)。工作顺序固定为：

1. 看完全部目标图并确认本次 Logo 资产、角色和哈希；只有用户明确要求时才允许默认模板。必要时先 dry-run 清理外围边，正式清理登记规范化谱系。
2. 对每张最终尺寸运行 `apply_logo.py --dry-run --geometry-json ...`，用 `visible_bbox` 判断真实冲突。
3. 仅添加 Logo 时，源图或用户明确要求的确定性尺寸/格式转换结果直接作为待叠加 base，不先运行 `pure_generation_edit`。组合任务先完成对应的 generate、普通 edit 或 fanyi 翻译阶段；第一阶段禁止生成本次 active Logo。
4. 无冲突时 Logo 阶段不调用图片模型，直接把合格底图记录为 `prepared_base` 并执行 `direct_overlay`；最终 success 更新不传 `--attempts` 或 `--attempt-stage`，不生成 attempt record，保留前序 attempts 总数。有冲突时写 `logo_plan.json` 和 layout family，把尚未叠加本次 active Logo 的底图登记为 `conflict_reference_base`。
5. `logo_conflict` 是唯一参考编辑例外：只有真实冲突门禁通过且实际调用图片模型重排时，才用当前全局 attempt 加 1 登记独立 `attempt_stage=logo_conflict`。只把 `conflict_reference_base` 作为参考，只移动冲突信息模块。存在冲突的 family 先验收 pilot，再冻结布局并处理成员。每个可读候选传入并永久保留该次唯一的 `.xobi/work` `--prepared-base`；不得复用路径或 SHA-256。没有候选的 infrastructure attempt 禁止夹带 prepared/output 等候选产物。无冲突、`direct_overlay`、确定性叠加和最终 success 更新都不得增加图片 attempt。
6. 运行逐模块 relocation guard，确认原位清除、目标位对应，且其他区域无变化；合格结果记录为 `prepared_base`。候选登记为 accepted pending/success 时必须在同一次 update 复算并把完全相同的 `logo_relocation_validation` 绑定到 item 与 attempt，不能等最终叠加后补验。候选通过后 Logo 图片阶段立即封口。
7. 使用本次真实 Logo 资产做最后一步确定性叠加；这一步无论接在 `direct_overlay` 还是 `logo_conflict` 后都不增加图片 attempt。叠加后禁止再次交给 AI；生成 source/conflict_reference_base/prepared_base/final 对照表并验收。

```text
python scripts/normalize_logo.py --input <原始Logo> --output <.xobi/work/normalized-logo.png> --background <white|solid|transparent> --metadata-json <.xobi/work/logo-normalization.json> --manifest <.xobi/manifest.json>
python scripts/apply_logo.py --input <底图> --output <最终图> --logo <active Logo> --dry-run --geometry-json <.xobi/work/logo_geometry.json>
python scripts/apply_logo.py --input <底图> --output <最终图> --logo <active Logo> --safe-zone-approved [--opaque-approved]
```

## 输入型 batch 与 ZIP

```text
python scripts/preflight_images.py --input <路径> --mode edit --operation <摘要> --ratio <比例|宽×高|original> [--output-format <png|jpg|jpeg|webp|bmp|tiff|source>] [--alpha-policy <preserve|required|forbidden>] [--logo <Logo>|--use-default-logo] [--exclude <路径或glob>] [--roles-file <JSON>] [--workers 4]
python scripts/preflight_fanyi.py --input <路径> --target-language <语言> --ratio <比例|宽×高|original> [--workers 4]
```

- 未指定时输出格式为 PNG，透明策略为 `preserve`。明确需要透明背景时使用 `--alpha-policy required`；不支持透明像素的格式必须在预检时拒绝。
- 一张 target 对应一个 task；同 stem 的不同扩展名预分配唯一输出，不能互相覆盖。
- ZIP 在解压前拒绝重复、仅大小写不同或路径穿越的 member。
- PSD/PSB 写入 `unsupported_inputs` 并跳过，不偷偷转换、不安装强制依赖。
- 每个 task 完成后通过 `update_manifest.py` 写独立 state 并在锁内合并；禁止直接改共享 JSON。

Localization 的计划必须在图片调用前冻结；候选返回后不得扩大授权或改写译文。每次调用只引用当前 task 的原始源图，失败重试仍从原图开始。普通 generate/edit 继续使用无参考纯生图；任何模式真实进入 Logo 冲突时，仍按 Logo 规则冻结并登记独立 `logo_conflict` attempt。

success 必须验证文件存在、位于任务根目录、不是源图/Logo、路径唯一、扩展名与真实编码一致、透明契约、比例/尺寸正确，并记录 SHA-256；不同 task 的最终输出路径和内容哈希不得重复。

## 四路、重试与单路降级

1. 宿主明确禁止并行：开工即 `workers=1`。
2. 否则使用 `min(4, slots, tasks, host_limit)`；各 worker 只处理自己的 task，内部逐张串行。
3. Logo 同系列只有存在冲突重排时才进入 pilot 屏障；全为 direct_overlay 的系列无需 pilot。`commerce_main_image` 批量始终按 family 先过内部 pilot 再并行；其他模式只有用户明确要求共享布局或风格时才设置相应屏障。
4. 返回候选即计该阶段 quality attempt；未通过时只对当前图做最多 2 次针对性重试。没有候选的基础设施错误独立计数，初次调用后最多重试 3 次并等待 2/5/10 秒。
5. 两个 worker 出现同类基础设施错误后，取消尚未执行的并行退避重试并暂停派发；选择最早受影响的 pending task，沿用其冻结 prompt 和隔离输出做一次单路探针。探针按实际结果计入该 task，禁止无归属调用。
6. 探针成功并验收后记录降级，`workers=1` 补 pending；探针失败则保留已成功项并报告。
7. 默认不重跑 success。用户明确要求重做，或共享 family/style lock 被证明错误时，只重做明确受影响范围。

## 风格锁与系列锁

- `batch_style_lock`：只有用户明确要求整批视觉统一时启用，约束全批抽象视觉规范。
- `layout_family_lock`：自动识别两张以上同系列时启用，只约束该系列的标题方向、层级、模块关系和间距。
- `main_image_family`：`commerce_main_image` 按平台、比例、品类、视觉方向和文字策略自动划分；每个 family 先有内部 pilot，再把已验收的抽象艺术指导传给成员。

这些锁互不替代。Logo 冲突重排 family 与主图 family 分别按自己的规则先 pilot 再并行；全为 direct_overlay 的 Logo family 不设 pilot。不同 family 不共享商品、文案、局部构图或图片上下文。

## 最终验收与交付

```text
python scripts/create_contact_sheet.py --manifest <.xobi/manifest.json> --output <.xobi/work/stage-review.jpg>
python scripts/verify_manifest.py --manifest <.xobi/manifest.json>
```

翻译联系表使用 source/pure_generation_candidate/final；Logo 联系表使用 source/conflict_reference_base/prepared_base/final，direct_overlay 可省略与 source/base 重复的列。只有验证通过才能声称“全部完成、无遗漏、无重复”。交付 ZIP 只包含任务根目录最终成品，排除 `.xobi/`。只有用户明确要求额外尺寸、格式或体积转换时才执行对应本地工具；不得把后处理当默认图片生产方式，也不得借后处理改变视觉布局。

`commerce_main_image` 还必须保留 `scripts/create_main_image_review.py` 生成并绑定的全尺寸/保持比例长边 256/保持比例长边 160 review 证据；三档都通过后才能交付。
