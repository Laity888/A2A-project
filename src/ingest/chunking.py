"""分层父子切分（对齐 edu_rag-main 的 process_documents，票 04）。

单篇 Markdown → MarkdownTextSplitter 先切父块(1200/overlap50)，再把每个父块切子块(300/overlap50)。
子块携带 parent_id / parent_content / source / source_url / category / timestamp。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownTextSplitter

from ingest.corpus import CrawledDoc


@dataclass
class Chunk:
    text: str
    parent_id: str
    parent_content: str
    source: str
    category: str
    source_url: str
    timestamp: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def chunk_doc(
    doc: CrawledDoc,
    parent_chunk_size: int,
    child_chunk_size: int,
    chunk_overlap: int,
    timestamp: str | None = None,
) -> list[Chunk]:
    ts = timestamp or _now_iso()
    base = Document(
        page_content=doc.markdown,
        metadata={
            "source": doc.slug,
            "source_url": doc.url,
            "category": doc.category,
            "timestamp": ts,
        },
    )
    parent_splitter = MarkdownTextSplitter(
        chunk_size=parent_chunk_size, chunk_overlap=chunk_overlap
    )
    child_splitter = MarkdownTextSplitter(
        chunk_size=child_chunk_size, chunk_overlap=chunk_overlap
    )

    chunks: list[Chunk] = []
    for j, parent in enumerate(parent_splitter.split_documents([base])):
        parent_id = f"{doc.slug}_parent_{j}"
        for child in child_splitter.split_documents([parent]):
            chunks.append(
                Chunk(
                    text=child.page_content,
                    parent_id=parent_id,
                    parent_content=parent.page_content,
                    source=doc.slug,
                    category=doc.category,
                    source_url=doc.url,
                    timestamp=ts,
                )
            )
    return chunks


def chunk_docs(
    docs: list[CrawledDoc],
    parent_chunk_size: int,
    child_chunk_size: int,
    chunk_overlap: int,
) -> list[Chunk]:
    ts = _now_iso()
    out: list[Chunk] = []
    for doc in docs:
        out.extend(
            chunk_doc(doc, parent_chunk_size, child_chunk_size, chunk_overlap, timestamp=ts)
        )
    return out
