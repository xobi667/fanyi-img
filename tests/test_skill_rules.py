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
        }
        for path in sorted(required):
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertTrue(path.is_file(), f"缺少规则文件：{path}")

    def test_localization_defaults_to_pure_generation_without_references(self) -> None:
        no_reference = re.compile(
            r"(?:不传|不得传|禁止传|REFERENCE INPUT:\s*NONE).{0,80}(?:参考图|reference)",
            re.IGNORECASE | re.DOTALL,
        )

        for path in LOCALIZATION_POLICY_FILES:
            text = read_utf8(path)
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertIn("pure_generation_localization", text)
                self.assertRegex(text, no_reference)

        prompt_text = read_utf8(PROMPTS)
        self.assertIn("PURE GENERATION LOCALIZATION", prompt_text)
        self.assertIn("REFERENCE INPUT: NONE", prompt_text)

    def test_localization_plan_and_ratio_are_explicit_and_auditable(self) -> None:
        localization = read_utf8(LOCALIZATION)
        prompts = read_utf8(PROMPTS)
        quality = read_utf8(QUALITY)
        combined = "\n".join((localization, prompts, quality))

        self.assertIn("output_ratio", combined)
        self.assertIn("target_size", combined)
        self.assertRegex(localization, re.compile(r"每张图.{0,160}localization_plan", re.DOTALL))
        self.assertIn("source_sha256", localization)
        self.assertIn("text_blocks", localization)
        self.assertIn("non_text_inventory", localization)
        self.assertRegex(localization, re.compile(r"保持原比例.{0,160}(?:画布|裁切|布局).{0,80}保持", re.DOTALL))
        self.assertIn("minimum canvas adaptation", prompts)

    def test_affirmative_default_pure_generation_policy_is_mandatory(self) -> None:
        for path in LOCALIZATION_POLICY_FILES + (PROMPTS,):
            text = read_utf8(path)
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertRegex(
                    text,
                    re.compile(r"(?:默认.{0,50}纯生图|pure_generation_localization)", re.IGNORECASE | re.DOTALL),
                )
        runtimes = read_utf8(RUNTIMES)
        self.assertRegex(runtimes, re.compile(r"(?:不传|不得传).{0,80}(?:参考图|reference)", re.IGNORECASE | re.DOTALL))
        self.assertRegex(runtimes, re.compile(r"Logo.{0,100}(?:唯一|例外)", re.IGNORECASE | re.DOTALL))

    def test_text_only_content_lock_is_mandatory_without_rebuild_approval(self) -> None:
        prompt_text = read_utf8(PROMPTS)
        localization_text = read_utf8(LOCALIZATION)
        skill_text = read_utf8(SKILL)

        self.assertIn("STRICT CONTENT LOCK:", prompt_text)
        self.assertIn("STRICT NO-ADDITION RULE:", prompt_text)
        self.assertIn("pure_generation_localization", localization_text)
        self.assertRegex(skill_text, re.compile(r"纯生图.{0,80}(?:只替换|唯一授权变化|只翻译)", re.DOTALL))
        self.assertIn("task_id", localization_text)
        self.assertIn("source_sha256", localization_text)
        self.assertRegex(localization_text, re.compile(r"3 次.{0,80}(?:报告失败|不得登记第 4 次成功)", re.DOTALL))
        self.assertIn("user_exact", localization_text)
        self.assertIn("requested_target_text", localization_text)
        self.assertIn("non_text_inventory", localization_text)
        self.assertRegex(localization_text, re.compile(r"不得运行.{0,80}compose_localization", re.DOTALL))
        self.assertRegex(localization_text, re.compile(r"不需要.{0,80}(?:授权|许可)", re.DOTALL))

    def test_logo_policy_separates_collision_and_spacing(self) -> None:
        text = read_utf8(LOGO)

        self.assertRegex(text, re.compile(r"`visible_bbox`.{0,80}只用它判断.{0,30}遮挡", re.DOTALL))
        self.assertRegex(text, re.compile(r"`safe_zone`.{0,80}只用于.{0,40}(?:间距|布局|锚点)", re.DOTALL))
        self.assertRegex(text, re.compile(r"不得用它扩大冲突范围"))

    def test_logo_is_the_only_explicitly_requested_reference_exception(self) -> None:
        for path in (SKILL, WORKFLOW, RUNTIMES, LOGO):
            text = read_utf8(path)
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertRegex(
                    text,
                    re.compile(r"用户明确要求添加 Logo", re.IGNORECASE),
                )

        skill_text = read_utf8(SKILL)
        logo_text = read_utf8(LOGO)
        self.assertRegex(skill_text, re.compile(r"源图本来含有 Logo.{0,100}不构成例外", re.DOTALL))
        self.assertRegex(logo_text, re.compile(r"源图已有 Logo.{0,100}不构成例外", re.DOTALL))
        self.assertRegex(
            logo_text,
            re.compile(r"`logo_conflict`.{0,80}唯一允许.{0,40}参考图", re.DOTALL),
        )

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

    def test_gate_asks_only_for_missing_information(self) -> None:
        for path in (SKILL, WORKFLOW):
            text = read_utf8(path)
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertIn("缺什么只问什么", text)

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
