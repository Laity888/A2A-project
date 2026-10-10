"""工单 08：提示词/参数记录与 config 的一致性校验（票 09 / 票 10 §8）。

漂移即红：`docs/prompts/*.md` 里记录的提示词原文与生效参数，必须与 `config/` 实际值一致。
（改提示词/参数却没同步更新记录 → 此测试失败，把票 09 的 DoD 变成自动闸门。）
"""

from __future__ import annotations

from pathlib import Path

from common.prompt_check import _iter_entries, validate


def test_records_exist():
    assert len(_iter_entries()) >= 10, "docs/prompts 下应有足量 promptspec 记录条目"


def test_prompt_params_consistent_with_config():
    errors = validate()
    assert not errors, "记录与 config 漂移：\n" + "\n".join(errors)


def _spec(tmp_path: Path, value: str) -> Path:
    (tmp_path / "x.md").write_text(
        "```promptspec\n"
        f"- id: probe\n  file: config/common.yaml\n  path: llm.model\n  value: {value}\n"
        "```\n",
        encoding="utf-8",
    )
    return tmp_path


def test_checker_flags_drift(tmp_path):
    _spec(tmp_path, "totally-wrong-model")
    errs = validate(tmp_path)
    assert errs and "totally-wrong-model" in errs[0]  # 漂移被抓


def test_checker_passes_when_matching(tmp_path):
    _spec(tmp_path, "qwen3.8-max")  # 与 config/common.yaml 实际值一致
    assert validate(tmp_path) == []
