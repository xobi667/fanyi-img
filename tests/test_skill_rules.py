from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPO_ROOT / "SKILL.md"
REFERENCES = REPO_ROOT / "references"

LOCALIZATION = REFERENCES / "localization.md"
WORKFLOW = REFERENCES / "workflow.md"
PROMPTS = REFERENCES / "prompts.md"
QUALITY = REFERENCES / "quality.md"
RUNTIMES = REFERENCES / "runtimes.md"
LOGO = REFERENCES / "logo.md"
MAIN_IMAGE = REFERENCES / "main-image.md"

LOCALIZATION_POLICY_FILES = (SKILL, LOCALIZATION, WORKFLOW, RUNTIMES)
CONCURRENCY_POLICY_FILES = (SKILL, WORKFLOW, QUALITY, RUNTIMES)

COMMON_MOJIBAKE = (
    "\u951f\u65a4\u62f7",
    "\u70eb\u70eb\u70eb",
    "\u5c6f\u5c6f\u5c6f",
    "\u00ef\u00bb\u00bf",
    "\u00e2\u20ac\u2122",
    "\u00e2\u20ac\u0153",
    "\u00e2\u20ac",
    "\u00c3\u00a4",
    "\u00c3\u00a5",
    "\u00c3\u00a9",
    "\u00c2\u00b7",
    "\u00c2\u00a0",
    "\u00e6\u2013\u2021",
    "\u00e4\u00b8\u00ad",
    "\u00e7\u0161\u201e",
    "\u00e5\u203a\u00be",
    "\u00e8\u00af\u2018",
)


def read_utf8(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise AssertionError(f"不是有效 UTF-8：{path.relative_to(REPO_ROOT)}") from exc


def markdown_files() -> list[Path]:
    return sorted(
        path
        for path in REPO_ROOT.rglob("*.md")
        if ".git" not in path.parts and ".xobi" not in path.parts
    )


class SkillRuleRegressionTests(unittest.TestCase):
    def test_required_policy_files_exist(self) -> None:
        required = {
            SKILL,
            LOCALIZATION,
            WORKFLOW,
            PROMPTS,
            QUALITY,
            RUNTIMES,
            LOGO,
            MAIN_IMAGE,
        }
        for path in sorted(required):
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertTrue(path.is_file(), f"缺少规则文件：{path}")

    def test_localization_uses_only_the_original_source_reference(self) -> None:
        for path in LOCALIZATION_POLICY_FILES:
            text = read_utf8(path)
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertRegex(text, re.compile(r"(?:当前|原始).{0,40}源图.{0,80}(?:唯一|只)", re.DOTALL))

        prompt_text = read_utf8(PROMPTS)
        self.assertIn("referenced_image_paths", prompt_text)
        self.assertIn("num_last_images_to_include", prompt_text)
        self.assertRegex(prompt_text, re.compile(r"两者.{0,20}(?:不能|不得).{0,20}共存", re.DOTALL))
        self.assertNotIn("PURE GENERATION LOCALIZATION", prompt_text)

    def test_localization_plan_and_ratio_are_explicit_and_auditable(self) -> None:
        localization = read_utf8(LOCALIZATION)
        prompts = read_utf8(PROMPTS)
        quality = read_utf8(QUALITY)
        combined = "\n".join((localization, prompts, quality))

        self.assertRegex(localization, re.compile(r"目标图片.{0,40}目标语言.{0,40}输出比例", re.DOTALL))
        self.assertRegex(localization, re.compile(r"保持原比例.{0,20}保持原画幅、裁切和布局", re.DOTALL))
        self.assertIn("800×800", combined)
        self.assertIn("900–1024KB", combined)
        self.assertIn("final_optimize_images.py", combined)

    def test_localization_reference_policy_and_other_modes_are_separate(self) -> None:
        combined = "\n".join(read_utf8(path) for path in LOCALIZATION_POLICY_FILES + (PROMPTS,))
        self.assertIn("referenced_image_paths", combined)
        self.assertIn("num_last_images_to_include", combined)
        self.assertRegex(combined, re.compile(r"失败候选.{0,80}(?:不得|不引用|禁止)", re.DOTALL))
        runtimes = read_utf8(RUNTIMES)
        self.assertRegex(runtimes, re.compile(r"generate/edit/commerce_main_image.{0,80}纯生图", re.DOTALL))

    def test_text_only_content_lock_is_mandatory_without_rebuild_approval(self) -> None:
        prompt_text = read_utf8(PROMPTS)
        localization_text = read_utf8(LOCALIZATION)
        skill_text = read_utf8(SKILL)

        self.assertIn("STRICT CONTENT LOCK:", prompt_text)
        self.assertIn("STRICT NO-ADDITION RULE:", prompt_text)
        self.assertNotIn("pure_generation_localization", localization_text)
        self.assertRegex(skill_text, re.compile(r"参考图翻译.{0,80}(?:只替换|只翻译)", re.DOTALL))
        self.assertRegex(localization_text, re.compile(r"每张最多 3 个.{0,40}质量尝试", re.DOTALL))
        self.assertRegex(localization_text, re.compile(r"用户提供精确目标文案.{0,80}逐字照写", re.DOTALL))
        self.assertIn("compose_localization.py", localization_text)
        self.assertIn("final_optimize_images.py", localization_text)

    def test_logo_policy_separates_collision_and_spacing(self) -> None:
        text = read_utf8(LOGO)

        self.assertRegex(text, re.compile(r"`visible_bbox`.{0,80}只用它判断.{0,30}遮挡", re.DOTALL))
        self.assertRegex(text, re.compile(r"`safe_zone`.{0,80}只用于.{0,40}(?:间距|布局|锚点)", re.DOTALL))
        self.assertRegex(text, re.compile(r"不得用它扩大冲突范围"))

    def test_logo_reference_route_remains_explicit_and_separate(self) -> None:
        for path in (SKILL, WORKFLOW, RUNTIMES, LOGO):
            text = read_utf8(path)
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertRegex(
                    text,
                    re.compile(r"用户明确要求添加 Logo", re.IGNORECASE),
                )

        skill_text = read_utf8(SKILL)
        logo_text = read_utf8(LOGO)
        localization_text = read_utf8(LOCALIZATION)
        self.assertRegex(skill_text, re.compile(r"源图已有 Logo.{0,40}不等于用户要求添加 Logo", re.DOTALL))
        self.assertRegex(logo_text, re.compile(r"源图已有 Logo.{0,100}不构成 Logo 例外", re.DOTALL))
        self.assertRegex(
            logo_text,
            re.compile(r"`logo_conflict`.{0,80}Logo 流程中唯一允许.{0,40}参考图", re.DOTALL),
        )
        self.assertRegex(localization_text, re.compile(r"源图.{0,80}唯一.{0,40}`referenced_image_paths`", re.DOTALL))

        agent_text = read_utf8(REPO_ROOT / "agents" / "openai.yaml")
        self.assertRegex(agent_text, re.compile(r"无(?:真实)?遮挡.{0,40}不(?:额外)?调用(?:图片)?模型"))
        self.assertRegex(agent_text, re.compile(r"有遮挡.{0,60}局部参考重排"))

    def test_ratio_pilot_and_attempt_wording_cannot_expand_reference_inputs(self) -> None:
        skill_text = read_utf8(SKILL)
        prompts_text = read_utf8(PROMPTS)
        quality_text = read_utf8(QUALITY)
        glossary_text = read_utf8(REFERENCES / "glossary.md")

        self.assertIn("它们不是“翻译”自动附带的权限", skill_text)
        self.assertRegex(prompts_text, re.compile(r"FAMILY_PILOT.{0,180}不得把 pilot 图片", re.DOTALL))
        self.assertIn("Do not attach or reference the pilot image", prompts_text)
        self.assertIn("返回任何可读取候选就计质量 attempt", quality_text)
        self.assertRegex(glossary_text, re.compile(r"保持原比例时 `target_bbox` 必须等于 `source_bbox`"))

    def test_logo_policy_requires_family_pilot_and_three_way_review(self) -> None:
        text = read_utf8(LOGO)

        self.assertRegex(text, re.compile(r"family.{0,40}pilot", re.IGNORECASE | re.DOTALL))
        self.assertRegex(text, re.compile(r"pilot.{0,80}冻结", re.IGNORECASE | re.DOTALL))
        self.assertRegex(text, re.compile(r"冻结.{0,120}最多四路", re.DOTALL))
        self.assertIn("source/conflict_reference_base/prepared_base/final", text)
        self.assertIn("1036 x 309", text)
        self.assertIn("/ 4000", text)

    def test_logo_conflict_candidates_are_evidence_bound_and_stage_closed(self) -> None:
        combined = "\n".join((read_utf8(SKILL), read_utf8(LOGO), read_utf8(QUALITY)))

        for marker in ("candidate_path", "SHA-256", "candidate_width", "candidate_height"):
            with self.subTest(marker=marker):
                self.assertIn(marker, combined)
        self.assertRegex(
            combined,
            re.compile(r"基础设施.{0,160}(?:禁止|不得).{0,80}(?:prepared_base|候选产物)", re.DOTALL),
        )
        self.assertRegex(
            combined,
            re.compile(r"(?:验收通过|accepted).{0,100}(?:阶段).{0,40}(?:封口|封闭)", re.DOTALL),
        )
        self.assertRegex(
            combined,
            re.compile(r"accepted.{0,180}(?:同一次|同次).{0,120}logo_relocation_validation", re.IGNORECASE | re.DOTALL),
        )

    def test_gate_asks_only_for_missing_information(self) -> None:
        for path in (SKILL, WORKFLOW):
            text = read_utf8(path)
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertIn("缺什么只问什么", text)

    def test_commerce_main_image_is_explicit_and_cannot_expand_translation(self) -> None:
        skill_text = read_utf8(SKILL)
        main_image = read_utf8(MAIN_IMAGE)
        localization = read_utf8(LOCALIZATION)

        for text in (skill_text, main_image):
            self.assertRegex(text, re.compile(r"只有用户明确要求.{0,80}(?:做|制作|重做|优化).{0,30}主图", re.DOTALL))
            self.assertIn("commerce_main_image", text)
        self.assertRegex(main_image, re.compile(r"翻译主图.{0,180}(?:不启用|禁止借主图规则美化)", re.DOTALL))
        self.assertRegex(localization, re.compile(r"只翻译|唯一(?:允许的|授权)内容变化", re.DOTALL))

    def test_commerce_main_image_gate_and_art_direction_are_frozen(self) -> None:
        main_image = read_utf8(MAIN_IMAGE)
        required = (
            "platform_profile",
            "visual_direction",
            "output_ratio",
            "text_policy",
            "product_content_lock",
            "single_focus",
            "hero_occupancy",
            "safe_margin",
            "information_hierarchy",
            "camera_and_scale",
            "lighting_and_shadow",
            "material_response",
            "background_and_color",
            "forbidden_patterns",
        )
        for field in required:
            with self.subTest(field=field):
                self.assertIn(field, main_image)
        self.assertRegex(main_image, re.compile(r"第一次图片调用前.{0,200}(?:确认|冻结)", re.DOTALL))
        self.assertRegex(main_image, re.compile(r"缺什么只问什么", re.DOTALL))

    def test_commerce_main_image_prompt_and_thumbnail_quality_gate_are_hard(self) -> None:
        prompts = read_utf8(PROMPTS)
        quality = read_utf8(QUALITY)
        main_image = read_utf8(MAIN_IMAGE)
        combined = "\n".join((prompts, quality, main_image))

        self.assertIn("PURE GENERATION COMMERCE MAIN IMAGE", prompts)
        self.assertIn("REFERENCE INPUT: NONE", prompts)
        self.assertIn("PRODUCT AND CONTENT LOCK", prompts)
        self.assertIn("TEXT POLICY", prompts)
        for marker in ("黄黑", "红角标", "粗描边", "椭圆贴片", "拥挤拼贴", "过饱和", "编造"):
            with self.subTest(marker=marker):
                self.assertIn(marker, combined)
        self.assertIn("create_main_image_review.py", combined)
        self.assertRegex(combined, re.compile(r"全尺寸.{0,120}256.{0,120}160", re.DOTALL))
        self.assertRegex(quality, re.compile(r"审美不合格.{0,80}quality failure", re.IGNORECASE | re.DOTALL))
        self.assertIn("full-original", main_image)
        self.assertIn("passed=false", main_image)
        self.assertRegex(
            combined,
            re.compile(r"每个.{0,40}(?:有候选|候选).{0,40}attempt.{0,100}review", re.IGNORECASE | re.DOTALL),
        )
        self.assertRegex(combined, re.compile(r"assessment.{0,80}evidence", re.IGNORECASE | re.DOTALL))

    def test_concurrency_backoff_and_single_worker_fallback_are_locked(self) -> None:
        single_worker = re.compile(r"workers\s*=\s*1|降为\s*1(?:\s*路)?")

        for path in CONCURRENCY_POLICY_FILES:
            text = read_utf8(path)
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertIn("2/5/10", text)
                self.assertRegex(text, single_worker)

    def test_all_markdown_is_clean_utf8_without_mojibake(self) -> None:
        paths = markdown_files()
        self.assertTrue(paths, "仓库中没有 Markdown 文件")

        for path in paths:
            text = read_utf8(path)
            relative = path.relative_to(REPO_ROOT)
            with self.subTest(path=relative):
                self.assertNotIn("\ufffd", text, f"发现 Unicode 替换字符：{relative}")
                self.assertIsNone(
                    re.search(r"\?{3,}", text),
                    f"发现连续三个以上问号：{relative}",
                )
                for marker in COMMON_MOJIBAKE:
                    self.assertNotIn(marker, text, f"发现疑似乱码 {marker!r}：{relative}")


if __name__ == "__main__":
    unittest.main()
