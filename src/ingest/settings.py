"""入库参数装配：合并 config/ingest.yaml + common.yaml（Milvus）+ agents/retrieval.yaml（模型/切分）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from common.config import CONFIG_DIR, REPO_ROOT, load_agent, load_common

INGEST_CONFIG_PATH = CONFIG_DIR / "ingest.yaml"


@dataclass(frozen=True)
class IngestSettings:
    # crawl
    base_url: str
    sitemap_url: str
    allow_prefix: str
    exclude_prefixes: tuple[str, ...]
    output_dir: Path
    archive_html: bool
    html_dir: Path
    delay_seconds: float
    timeout_seconds: float
    user_agent: str
    respect_robots: bool
    content_selectors: tuple[str, ...]
    title_selectors: tuple[str, ...]
    # milvus
    milvus_host: str
    milvus_port: int
    database: str
    collection: str
    ensure_database: bool
    batch_size: int
    # 模型 / 切分（对齐 edu_rag-main，票 04）
    embedding_model_path: str
    device: str
    parent_chunk_size: int
    child_chunk_size: int
    chunk_overlap: int
    index_nlist: int
    index_drop_ratio_build: float


def _load_ingest_yaml() -> dict[str, Any]:
    if not INGEST_CONFIG_PATH.exists():
        raise FileNotFoundError(f"缺少入库配置：{INGEST_CONFIG_PATH}")
    with INGEST_CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_ingest_settings() -> IngestSettings:
    raw = _load_ingest_yaml()
    crawl = raw["crawl"]
    selectors = raw.get("selectors", {})
    ingest = raw.get("ingest", {})

    common = load_common()
    retrieval = load_agent("retrieval").settings["retrieval"]
    milvus = common.infra.milvus

    output_dir = REPO_ROOT / crawl["output_dir"]
    html_dir = (REPO_ROOT / crawl["html_dir"]) if crawl.get("html_dir") else (output_dir / "html")

    return IngestSettings(
        base_url=crawl["base_url"],
        sitemap_url=crawl["sitemap_url"],
        allow_prefix=crawl["allow_prefix"],
        exclude_prefixes=tuple(crawl.get("exclude_prefixes", [])),
        output_dir=output_dir,
        archive_html=bool(crawl.get("archive_html", True)),
        html_dir=html_dir,
        delay_seconds=float(crawl.get("delay_seconds", 0.5)),
        timeout_seconds=float(crawl.get("timeout_seconds", 20)),
        user_agent=crawl.get("user_agent", "a2a-cs-corpus-builder/0.1"),
        respect_robots=bool(crawl.get("respect_robots", True)),
        content_selectors=tuple(selectors.get("content", ["article", "main"])),
        title_selectors=tuple(selectors.get("title", ["h1"])),
        milvus_host=milvus.host,
        milvus_port=milvus.port,
        database=milvus.database,
        collection=milvus.collection,
        ensure_database=bool(ingest.get("ensure_database", True)),
        batch_size=int(ingest.get("batch_size", 32)),
        embedding_model_path=retrieval["embedding_model_path"],
        device=retrieval.get("embedding_device", "cpu"),
        parent_chunk_size=int(retrieval["parent_chunk_size"]),
        child_chunk_size=int(retrieval["child_chunk_size"]),
        chunk_overlap=int(retrieval["chunk_overlap"]),
        index_nlist=int(retrieval.get("index_nlist", 128)),
        index_drop_ratio_build=float(retrieval.get("index_drop_ratio_build", 0.2)),
    )
