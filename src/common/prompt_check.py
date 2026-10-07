"""提示词/参数记录的一致性校验（工单 08，票 09）。

约定：`docs/prompts/<agent>.md` 里放一个 ```promptspec``` YAML 块，逐条声明
「记录值」及其来源 `file`（相对仓库根的 config 文件）与 `path`（点号路径）。
本模块把每条记录值与 config 实际值比对，任何漂移返回错误列表（供 pytest 断言）。

条目形态：
  - id: reception.triage
    file: config/agents/reception.yaml
    path: prompts.triage
    text: |            # 文本类：逐字（rstrip 后）比对
      ...
  - id: reception.retrieval_top_k
    file: config/agents/reception.yaml
    path: orchestration.retrieval_top_k
    value: 3           # 标量类：str 比对
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs" / "prompts"

SPEC_FENCE = re.compile(r"```promptspec\n(.*?)\n```", re.S)


def _load_yaml(rel_path: str, cache: dict[str, Any]) -> Any:
    if rel_path not in cache:
        p = REPO_ROOT / rel_path
        if not p.exists():
            raise FileNotFoundError(f"记录引用了不存在的配置：{rel_path}")
        cache[rel_path] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return cache[rel_path]


def _resolve(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise KeyError(dotted)
    return cur


def _iter_entries(docs_dir: Path | None = None) -> list[dict]:
    docs_dir = docs_dir or DOCS_DIR
    entries: list[dict] = []
    for md in sorted(docs_dir.glob("*.md")):
        if md.name.upper() == "README.MD":
            continue
        text = md.read_text(encoding="utf-8")
        for block in SPEC_FENCE.findall(text):
            parsed = yaml.safe_load(block)
            if isinstance(parsed, list):
                for e in parsed:
                    e = dict(e)
                    e["_source_doc"] = md.name
                    entries.append(e)
    return entries


def validate(docs_dir: Path | None = None) -> list[str]:
    errors: list[str] = []
    cache: dict[str, Any] = {}
    for e in _iter_entries(docs_dir):
        eid = e.get("id", "?")
        try:
            cfg = _load_yaml(e["file"], cache)
            actual = _resolve(cfg, e["path"])
        except (FileNotFoundError, KeyError) as exc:
            errors.append(f"[{eid}] 来源不可达：{exc}")
            continue

        if "text" in e:
            recorded = str(e["text"]).rstrip()
            real = str(actual).rstrip()
            if recorded != real:
                errors.append(f"[{eid}] 提示词原文与 {e['file']}:{e['path']} 不一致")
        elif "value" in e:
            if str(e["value"]) != str(actual):
                errors.append(
                    f"[{eid}] 记录值 {e['value']!r} ≠ 实际 {actual!r}（{e['file']}:{e['path']}）"
                )
        else:
            errors.append(f"[{eid}] 条目既无 text 也无 value")
    return errors


if __name__ == "__main__":
    errs = validate()
    if errs:
        print("提示词/参数一致性校验失败：")
        for x in errs:
            print("  -", x)
        raise SystemExit(1)
    print(f"一致性校验通过：{len(_iter_entries())} 条记录与 config 一致")
