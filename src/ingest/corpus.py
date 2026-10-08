"""RAGFlow /docs 抓取：sitemap → 逐页取正文 → Markdown + 元数据（票 03）。

只抓 allow_prefix(/docs)、排除 exclude_prefixes(/blog)；robots 允许、sitemap 存在。
每页产出 CrawledDoc(url, slug, category, title, markdown)；可选落盘 .md + manifest.json 以便复现。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as _to_markdown

from ingest.settings import IngestSettings

logger = logging.getLogger(__name__)

# 正文里剔除这些容器（导航/页脚/侧栏），避免污染切片
_DROP_TAGS = ("script", "style", "nav", "footer", "header", "aside", "form")


@dataclass(frozen=True)
class CrawledDoc:
    url: str
    slug: str
    category: str
    title: str
    markdown: str
    html: str = ""


def _netloc(url: str) -> str:
    return urlparse(url).netloc


def docs_urls_from_sitemap(sitemap_text: str, settings: IngestSettings) -> list[str]:
    locs = re.findall(r"<loc>(.*?)</loc>", sitemap_text)
    base_netloc = _netloc(settings.base_url)
    prefix = settings.allow_prefix
    seen: set[str] = set()
    out: list[str] = []
    for raw in locs:
        url = raw.strip()
        path = urlparse(url).path.rstrip("/")
        if _netloc(url) != base_netloc:
            continue
        if not path.startswith(prefix):
            continue
        if any(path == e or path.startswith(e.rstrip("/") + "/") for e in settings.exclude_prefixes):
            continue
        if path in ("", "/docs"):  # 跳过总入口索引
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _slug_and_category(url: str, allow_prefix: str) -> tuple[str, str]:
    path = urlparse(url).path.strip("/")  # docs/<a>/<b>
    parts = [p for p in path.split("/") if p]
    if parts and parts[0] == allow_prefix.strip("/"):
        parts = parts[1:]
    if not parts:
        parts = ["index"]
    slug = parts[-1] or "index"
    category = parts[0] if len(parts) > 1 else "general"
    return slug, category


def _select_container(soup: BeautifulSoup, settings: IngestSettings) -> BeautifulSoup | None:
    for sel in settings.content_selectors:
        node = soup.select_one(sel)
        if node and len(node.get_text(strip=True)) > 40:
            return node
    return None


def page_to_doc(url: str, html: str, settings: IngestSettings) -> CrawledDoc | None:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(list(_DROP_TAGS)):
        tag.decompose()
    container = _select_container(soup, settings)
    if container is None:
        logger.warning("未找到正文容器，跳过：%s", url)
        return None
    h1 = soup.select_one(settings.title_selectors[0]) if settings.title_selectors else None
    title = h1.get_text(strip=True) if h1 else url.rsplit("/", 1)[-1]
    markdown = _to_markdown(str(container), heading_style="ATX", strip=["img"])
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    if len(markdown) < 50:  # 分类索引页多为空壳，剔除
        logger.info("正文过短，跳过：%s", url)
        return None
    slug, category = _slug_and_category(url, settings.allow_prefix)
    return CrawledDoc(
        url=url, slug=slug, category=category, title=title, markdown=markdown, html=html
    )


def crawl(settings: IngestSettings, *, limit: int | None = None,
          save: bool = True) -> list[CrawledDoc]:
    headers = {"User-Agent": settings.user_agent}
    with requests.Session() as session:
        session.headers.update(headers)
        sm = session.get(settings.sitemap_url, timeout=settings.timeout_seconds)
        urls = docs_urls_from_sitemap(sm.text, settings)
        if limit:
            urls = urls[:limit]
        logger.info("待抓取 /docs 页数：%d", len(urls))

        docs: list[CrawledDoc] = []
        for i, url in enumerate(urls, start=1):
            try:
                resp = session.get(url, timeout=settings.timeout_seconds)
                if resp.status_code != 200:
                    logger.warning("HTTP %s 跳过：%s", resp.status_code, url)
                else:
                    doc = page_to_doc(url, resp.content.decode("utf-8", "ignore"), settings)
                    if doc:
                        docs.append(doc)
            except requests.RequestException as exc:
                logger.warning("抓取失败 %s：%s", url, exc)
            if settings.delay_seconds:
                time.sleep(settings.delay_seconds)
            if i % 20 == 0:
                logger.info("已抓 %d/%d，有效 %d", i, len(urls), len(docs))

    if save:
        _save_corpus(docs, settings)
    logger.info("抓取完成：有效文档 %d 篇", len(docs))
    return docs


def _save_corpus(docs: list[CrawledDoc], settings: IngestSettings) -> None:
    out: Path = settings.output_dir
    out.mkdir(parents=True, exist_ok=True)
    html_dir: Path = settings.html_dir
    if settings.archive_html:
        html_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    for doc in docs:
        (out / f"{doc.slug}.md").write_text(doc.markdown, encoding="utf-8")
        entry: dict[str, Any] = {
            "url": doc.url, "category": doc.category, "title": doc.title,
        }
        if settings.archive_html and doc.html:
            (html_dir / f"{doc.slug}.html").write_text(doc.html, encoding="utf-8")
            entry["html"] = f"html/{doc.slug}.html"
        manifest[doc.slug] = entry
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "语料已落盘：%s（%d 篇 md%s）",
        out,
        len(docs),
        f" + {html_dir}（html）" if settings.archive_html else "",
    )
