"""入库编排入口（工单 02）。

推荐两步（先归档再入库）：
    python -m ingest run --crawl-only          # ① 抓 /docs，把 HTML+Markdown 归档到 data/ragflow_docs/
    python -m ingest run --reuse               # ② 读归档语料 → 切分 → BGE-M3 → upsert Milvus
其它：
    python -m ingest run                        # 一步到位：抓取→切分→嵌入→入库
    python -m ingest run --limit 5              # 只处理前 5 页（冒烟）
用项目 .venv 的 python 跑；建议把输出写进日志文件便于实时查看（勿用 tail 管道，会缓冲）：
    PYTHONPATH=src ./.venv/Scripts/python.exe -u -m ingest run --crawl-only > logs/crawl.log 2>&1 &
    # 然后随时读 logs/crawl.log 看进度
日志走统一格式；参数全部来自 config；不硬编码。
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from common.logging_setup import setup_logging
from ingest import corpus
from ingest.chunking import chunk_docs
from ingest.corpus import CrawledDoc
from ingest.settings import IngestSettings, load_ingest_settings
from ingest.store import MilvusStore

logger = logging.getLogger(__name__)


def load_saved_corpus(settings: IngestSettings) -> list[CrawledDoc]:
    out: Path = settings.output_dir
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    docs: list[CrawledDoc] = []
    for slug, meta in manifest.items():
        md_file = out / f"{slug}.md"
        if not md_file.exists():
            continue
        docs.append(
            CrawledDoc(
                url=meta["url"],
                slug=slug,
                category=meta["category"],
                title=meta["title"],
                markdown=md_file.read_text(encoding="utf-8"),
            )
        )
    logger.info("复用已落盘语料：%d 篇", len(docs))
    return docs


def crawl_only(limit: int | None = None) -> int:
    """只抓 + 归档（HTML + Markdown + manifest），不加载模型、不写 Milvus。"""
    settings = load_ingest_settings()
    docs = corpus.crawl(settings, limit=limit, save=True)
    logger.info(
        "归档完成：%d 篇 → md: %s / html: %s",
        len(docs),
        settings.output_dir,
        settings.html_dir if settings.archive_html else "(html 未启用)",
    )
    return len(docs)


def run(limit: int | None = None, reuse: bool = False) -> int:
    settings = load_ingest_settings()

    if reuse:
        docs = load_saved_corpus(settings)
    else:
        docs = corpus.crawl(settings, limit=limit, save=True)

    if not docs:
        logger.error("没有可入库的文档，终止。")
        return 0

    chunks = chunk_docs(
        docs,
        parent_chunk_size=settings.parent_chunk_size,
        child_chunk_size=settings.child_chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    logger.info("切分完成：文档 %d 篇 → 子块 %d 条", len(docs), len(chunks))

    store = MilvusStore(settings)
    store.ensure_collection()
    written = store.upsert_chunks(chunks)
    logger.info(
        "入库完成：集合 %s.%s 现有约 %d 条",
        settings.database,
        settings.collection,
        store.count(),
    )
    sample = chunks[0]
    logger.info(
        "样例子块 source=%s category=%s url=%s parent_id=%s 文本长=%d",
        sample.source,
        sample.category,
        sample.source_url,
        sample.parent_id,
        len(sample.text),
    )
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="RAGFlow 语料入库（工单 02）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="抓取→切分→嵌入→入库")
    run_p.add_argument("--limit", type=int, default=None, help="只处理前 N 页（冒烟）")
    run_p.add_argument("--reuse", action="store_true", help="复用 data/ragflow_docs 已落盘语料")
    run_p.add_argument(
        "--crawl-only", action="store_true",
        help="只抓取并归档（HTML+Markdown），不加载模型、不写 Milvus",
    )
    args = parser.parse_args()

    settings = load_ingest_settings()
    setup_logging("INFO")
    if args.cmd == "run":
        if args.crawl_only:
            crawl_only(limit=args.limit)
        else:
            run(limit=args.limit, reuse=args.reuse)


if __name__ == "__main__":
    main()
