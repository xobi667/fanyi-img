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

    def test_localization_defaults_to_source_reference_text_only_edit(self) -> None:
        source_reference = re.compile(
            r"当前(?:源|目标)图.{0,40}(?:唯一\s*)?(?:target\s*)?(?:参考|reference)",
            re.IGNORECASE | re.DOTALL,
        )

        for path in LOCALIZATION_POLICY_FILES:
            text = read_utf8(path)
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertIn("text_only_reference_edit", text)
                self.assertRegex(text, source_reference)

        prompt_text = read_utf8(PROMPTS)
        self.assertRegex(
            prompt_text,
            re.compile(r"默认.{0,20}当前源图.{0,30}target reference", re.DOTALL),
        )

    def test_localization_ratio_exception_is_explicit_and_auditable(self) -> None:
        localization = read_utf8(LOCALIZATION)
        prompts = read_utf8(PROMPTS)
        quality = read_utf8(QUALITY)
        combined = "\n".join((localization, prompts, quality))

        self.assertIn("ratio_adaptation", combined)
        self.assertIn("RATIO_ADAPTATION", combined)
        self.assertRegex(localization, re.compile(r"保持原比例.{0,100}绝对锁定", re.DOTALL))
        self.assertRegex(localization, re.compile(r"新比例.{0,260}fail closed", re.DOTALL))
        self.assertRegex(localization, re.compile(r"ratio_adaptation\.required=true.{0,140}拒绝", re.DOTALL))
        self.assertNotRegex(
            prompts,
            re.compile(r"different ratio.{0,160}minimal (?:canvas|background|layout)", re.IGNORECASE | re.DOTALL),
        )
        self.assertNotIn("as closely as the native editor allows", localization)
        self.assertRegex(localization, re.compile(r"单图和批量任务都必须.{0,120}localization_plan", re.DOTALL))
        self.assertIn("size_resample", combined)
        self.assertIn("target_size", combined)
        self.assertRegex(
            localization,
            re.compile(r"相同宽高比.{0,320}整张画布.{0,60}等比确定性重采样", re.DOTALL),
        )

    def test_no_affirmative_default_pure_generation_policy(self) -> None:
        affirmative_default = re.compile(
            r"默认\s*(?:采用|使用|执行|切换(?:为|到)|走)?\s*(?:纯生图|纯重建)",
            re.IGNORECASE,
        )
        negative_prefix = re.compile(
            r"(?:不得|禁止|不能|不允许|并非|不是)[^。；;\n]{0,16}$"
        )
        forbidden_runtime_phrases = (
            "localization 不传参考",
            "localization不传参考",
            "默认 localization 不传",
            "默认localization不传",
        )

        for path in markdown_files():
            text = read_utf8(path)
            relative = path.relative_to(REPO_ROOT)
            for line_number, line in enumerate(text.splitlines(), start=1):
                for match in affirmative_default.finditer(line):
                    prefix = line[: match.start()]
                    with self.subTest(path=relative, line=line_number):
                        self.assertRegex(
                            prefix,
                            negative_prefix,
                            f"发现肯定式默认纯生图政策：{relative}:{line_number}: {line}",
                        )

                folded = line.casefold()
                for phrase in forbidden_runtime_phrases:
                    with self.subTest(path=relative, line=line_number, phrase=phrase):
                        self.assertNotIn(phrase, folded)

    def test_non_text_lock_and_pure_rebuild_permission_are_mandatory(self) -> None:
        prompt_text = read_utf8(PROMPTS)
        localization_text = read_utf8(LOCALIZATION)
        skill_text = read_utf8(SKILL)

        self.assertIn("STRICT NON-TEXT LOCK:", prompt_text)
        self.assertRegex(
            localization_text,
            re.compile(r"只有用户.{0,80}明确许可.{0,40}纯生图重建", re.DOTALL),
        )
        self.assertIn("pure_rebuild_user_authorized", localization_text)
        self.assertRegex(skill_text, re.compile(r"纯生图.{0,30}明确许可|明确许可.{0,30}纯生图"))
        self.assertIn("manifest_id", localization_text)
        self.assertIn("task_id", localization_text)
        self.assertIn("source_sha256", localization_text)
        self.assertRegex(localization_text, re.compile(r"3 次.{0,40}参考编辑质量失败", re.DOTALL))
        self.assertIn("许可一张不许可整批", localization_text)
        self.assertIn("target_text_source=user_exact", localization_text)
        self.assertIn("requested_target_text", localization_text)
        self.assertIn("--localization-composition-json", localization_text)
        self.assertIn("background_surface", localization_text)
        self.assertIn("字符串旧格式直接 fail closed", localization_text)
        self.assertIn("完整覆盖脚本计算出的交集", localization_text)
        self.assertRegex(
            localization_text,
            re.compile(r"raw candidate.{0,100}重新执行.{0,80}合成", re.IGNORECASE | re.DOTALL),
        )
        self.assertRegex(
            localization_text,
            re.compile(r"pure_rebuild.{0,160}唯一明确例外", re.IGNORECASE | re.DOTALL),
        )

    def test_logo_policy_separates_collision_and_spacing(self) -> None:
        text = read_utf8(LOGO)

        self.assertRegex(text, re.compile(r"`visible_bbox`.{0,80}只用它判断.{0,30}遮挡", re.DOTALL))
        self.assertRegex(text, re.compile(r"`safe_zone`.{0,80}只用于.{0,40}(?:间距|布局|锚点)", re.DOTALL))
        self.assertRegex(text, re.compile(r"不得用它扩大冲突范围"))

    def test_logo_policy_requires_family_pilot_and_three_way_review(self) -> None:
        text = read_utf8(LOGO)

        self.assertRegex(text, re.compile(r"family.{0,40}pilot", re.IGNORECASE | re.DOTALL))
        self.assertRegex(text, re.compile(r"pilot.{0,80}冻结", re.IGNORECASE | re.DOTALL))
        self.assertRegex(text, re.compile(r"冻结.{0,120}最多四路", re.DOTALL))
        self.assertIn("source/base/final", text)
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
