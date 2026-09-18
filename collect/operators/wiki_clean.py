"""kb 线合流算子(2026-09-10):wiki 正文素材 → 统一 docs 池。

链路定位(原计划"合流"):Phase 1 的 pages-{lang} 语料是"素材档案"
(原始 wikitext 分节),本算子把它清洗成纯文本段落,以文档身份落
pages/ URL 寻址池(与采集线 docs 同构),记账 qid_docs.jsonl。
素材层的既定归宿:唯一消费者,顺序读一遍。

行契约:
- 源头行(iter_corpus_rows 产出):Phase 1 语料行 + 挂上的 qid
  {lang, title, page_id, qid, is_redirect, is_disambig, sections,
   text_sha256, byte_len}
- 文档行(WikiCleanStage 产出):{qid, lang, title, url, page_sha,
  n_passages, markdown, content_sha, page_bytes}
  url=维基页地址(page_sha=sha256(url),与采集线 docs 同一寻址口径)
- 落盘(QidDocsSinkStage,复用 AppendManifestStore):markdown →
  pages/<aa>/<sha256(url)>.md;账本行 {page_sha, qid, url, title,
  lang, authority:"wiki", n_passages, path, content_sha, page_bytes}

清洗策略:模板/引用/表格/注释剥除,链接取显示文本,标题记号转纯文本;
段落块 200-900 字(对齐 operators/page.py 的 passage 口径)。质量门:
重定向/消歧义/清洗后过短(缺省会 400 字节)认缺,只计数不落盘。
"""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import quote

from demiflow.collect.store import AppendManifestStore
from demiflow.data.plan import StreamStage

# passage 块口径(对齐 page.py:_MIN/_MAX_PASSAGE)
_MIN_PASSAGE = 120
_MAX_PASSAGE = 900
MIN_CLEAN_BYTES = 400          # 质量门:清洗后正文短于此认缺

_REF_RE = re.compile(r"<ref[^>]*?/>|<ref[^>]*?>.*?</ref>", re.S | re.I)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_TABLE_RE = re.compile(r"\{\|.*?\|\}", re.S)
_GALLERY_RE = re.compile(r"<gallery.*?</gallery>", re.S | re.I)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_LINK_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")
_EXT_LINK_RE = re.compile(r"\[(?:https?://|ftp://)[^\s\]]+\s+([^\]]+)\]")
_FILE_CAT_RE = re.compile(
    r"\[\[(?:File|Image|文件|檔案|Category|分类|分類)[^\]]*\]\]", re.I)
_QUOTE_RE = re.compile(r"'{2,5}")
_HEADING_RE = re.compile(r"^\s*={1,6}\s*(.+?)\s*={1,6}\s*$", re.M)
_ENTITY = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
           "&quot;": '"', "&#39;": "'"}
_WS_RE = re.compile(r"[ \t]+")
_BLANK_RE = re.compile(r"\n{3,}")


def clean_wikitext(text: str) -> str:
    """wikitext → 纯文本(模板迭代剥除,嵌套上限防死循环)。"""
    text = _REF_RE.sub(" ", text)
    text = _COMMENT_RE.sub(" ", text)
    text = _TABLE_RE.sub(" ", text)
    text = _GALLERY_RE.sub(" ", text)
    for _ in range(8):                       # 模板可嵌套,收敛即止
        stripped = re.sub(r"\{\{[^{}]*\}\}", " ", text)
        if stripped == text:
            break
        text = stripped
    text = _FILE_CAT_RE.sub(" ", text)       # 文件/分类链接整条剥除
    text = _LINK_RE.sub(r"\1", text)         # [[a|b]]→b、[[a]]→a
    text = _EXT_LINK_RE.sub(r"\1", text)     # [url text]→text
    text = _QUOTE_RE.sub("", text)
    text = _HEADING_RE.sub(r"\1", text)
    text = _HTML_TAG_RE.sub(" ", text)
    for k, v in _ENTITY.items():
        text = text.replace(k, v)
    text = _WS_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text)
    return text.strip()


def split_passages(text: str) -> list[str]:
    """纯文本 → 段落块(空行分块,过短并入下段,过长按句号硬切)。"""
    blocks, buf = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(buf) + len(para) < _MIN_PASSAGE:
            buf = f"{buf}\n{para}".strip()
            continue
        blocks.append(buf)
        buf = para
    if buf:
        blocks.append(buf)
    out: list[str] = []
    for b in blocks:
        while len(b) > _MAX_PASSAGE:         # 过长按句切
            cut = b.rfind("。", 0, _MAX_PASSAGE)
            cut = cut + 1 if cut > _MIN_PASSAGE // 2 else _MAX_PASSAGE
            out.append(b[:cut].strip())
            b = b[cut:]
        if b:
            out.append(b)
    return out


def wiki_url(lang: str, title: str) -> str:
    return f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"


class WikiCleanStage(StreamStage):
    """语料行 → 文档行(清洗+切段+质量门;认缺 None 只计数)。"""

    label = "wiki_clean"

    def __init__(self, min_clean_bytes: int = MIN_CLEAN_BYTES):
        self._min = min_clean_bytes
        self.skipped = 0

    async def __call__(self, row: dict):
        if row.get("is_redirect") or row.get("is_disambig") or not row.get("qid"):
            self.skipped += 1
            return None
        parts = [f"# {row['title']}\n"]
        n_pass = 0
        for sec in row.get("sections") or []:
            body = clean_wikitext(sec.get("text") or "")
            if not body:
                continue
            title = sec.get("title") or ""
            parts.append(f"\n## {title}\n\n{body}" if title else f"\n{body}")
            n_pass += len(split_passages(body))
        markdown = "\n".join(parts)
        if len(markdown.encode("utf-8")) < self._min:
            self.skipped += 1
            return None
        url = wiki_url(row["lang"], row["title"])
        md5_bytes = markdown.encode("utf-8")
        return {
            "qid": row["qid"],
            "lang": row["lang"],
            "title": row["title"],
            "page_id": row.get("page_id"),
            "url": url,
            "page_sha": hashlib.sha256(url.encode()).hexdigest(),
            "n_passages": n_pass,
            "markdown": markdown,
            "content_sha": hashlib.sha256(md5_bytes).hexdigest(),
            "page_bytes": len(md5_bytes),
        }


class QidDocsSinkStage(StreamStage):
    """文档行 → pages 池 .md + qid_docs 账本行(AppendManifestStore)。"""

    label = "qid_docs_sink"
    concurrency = 1                    # 单写者:账本追加有序

    def __init__(self, *, pages_root: str, manifest: str):
        self._pages_root = pages_root
        self._store = AppendManifestStore(manifest=manifest,
                                          lock_path=manifest + ".lock")
        self._store.load_index(key_of=lambda r: (r["qid"], r["page_sha"]))
        self.sunk = 0

    async def __call__(self, row: dict):
        rel = f"pages/{row['page_sha'][:2]}/{row['page_sha']}.md"
        record = {k: row[k] for k in
                  ("page_sha", "qid", "url", "title", "lang",
                   "content_sha", "page_bytes")}
        record.update({"authority": "wiki", "n_passages": row["n_passages"],
                       "page_id": row.get("page_id"),
                       "path": rel})
        await self._store.write(data=row["markdown"].encode("utf-8"),
                                blob_path=f"{self._pages_root}/{rel[6:]}",
                                key=(row["qid"], row["page_sha"]),
                                record=record)
        self.sunk += 1
        return row
