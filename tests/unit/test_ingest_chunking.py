"""工单 02 离线单测：分层父子切分（不碰网络/模型/Milvus）。"""

from __future__ import annotations

from ingest.chunking import chunk_doc, chunk_docs
from ingest.corpus import CrawledDoc

_MD = (
    "# 安装 RAGFlow\n\n"
    "本节介绍如何部署 RAGFlow。要求 Docker 与可用内存。\n\n"
    "## 前置条件\n\n"
    "- 操作系统 Linux/macOS/Windows\n- 至少 16GB 内存\n- 已安装 Docker\n\n"
    "## 步骤\n\n"
    "1. clone 仓库\n2. 执行 docker compose up -d\n3. 浏览器打开 Web 界面\n\n"
    + "补充说明段落。" * 40  # 撑长以触发父子多层切分
)


def _doc():
    return CrawledDoc(
        url="https://ragflow.com.cn/docs/install",
        slug="install",
        category="quickstart",
        title="安装 RAGFlow",
        markdown=_MD,
    )


def test_chunk_doc_parent_child_metadata():
    chunks = chunk_doc(_doc(), parent_chunk_size=1200, child_chunk_size=300, chunk_overlap=50)
    assert chunks, "应有子块"
    for c in chunks:
        assert c.parent_id.startswith("install_parent_")
        assert c.parent_content
        assert c.source == "install"
        assert c.category == "quickstart"
        assert c.source_url.endswith("/docs/install")
        assert c.timestamp
        # 子块文本是父块的一部分（父子关系成立）
        assert c.text in c.parent_content or len(c.text) <= len(c.parent_content)


def test_chunk_doc_uniform_timestamp_and_ids():
    chunks = chunk_doc(_doc(), 1200, 300, 50)
    assert len({c.timestamp for c in chunks}) == 1
    assert len({c.parent_id for c in chunks}) >= 1


def test_chunk_docs_across_multiple_docs():
    docs = [_doc(), CrawledDoc("https://ragflow.com.cn/docs/faq", "faq", "help", "FAQ", "# FAQ\n\n问答说明。" * 30)]
    chunks = chunk_docs(docs, 1200, 300, 50)
    sources = {c.source for c in chunks}
    assert {"install", "faq"} <= sources
