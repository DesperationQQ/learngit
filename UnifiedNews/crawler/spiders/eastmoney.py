# -*- coding: utf-8 -*-
"""
东方财富新闻抓取
输出字段：title, url, pub_time(datetime), source('eastmoney'), category, summary
"""
from __future__ import annotations
import os
import re
import time
import datetime as dt
from typing import List, Dict, Any
import httpx
from bs4 import BeautifulSoup
from loguru import logger

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# 仅保留稳定频道，避免 404 噪音；后续需要可再扩展
CHANNELS: list[tuple[str, str]] = [
    ("https://finance.eastmoney.com/yaowen.html", "要闻"),
]

# ---------------- httpx ----------------
def _prepare_proxy_env() -> None:
    http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if http_proxy or https_proxy:
        logger.info(f"[eastmoney] proxy via {http_proxy or https_proxy}")
    else:
        logger.info("[eastmoney] no proxy in env (trust_env=True)")

def _make_client() -> httpx.Client:
    # httpx 0.28+：要么给 default，要么四个超时都给
    timeout = httpx.Timeout(connect=10.0, read=25.0, write=20.0, pool=10.0)
    logger.info(f"[eastmoney] httpx={httpx.__version__}, trust_env=True")
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml",
        },
        http2=True,
        trust_env=True,
    )

def _try_get(client: httpx.Client, url: str, retries: int = 3, backoff: float = 1.5) -> httpx.Response:
    last_err: Exception | None = None
    for i in range(retries):
        try:
            r = client.get(url)
            r.raise_for_status()
            return r
        except httpx.HTTPStatusError as e:
            # 404/410 基本不是临时错误，不重试
            if e.response.status_code in (404, 410):
                raise
            last_err = e
        except httpx.RequestError as e:  # 包含超时/连接错误
            last_err = e
        sleep = backoff ** i
        time.sleep(sleep if sleep < 5 else 5)
    assert last_err is not None
    raise last_err

# ---------------- utils ----------------
_abs = re.compile(r"^https?://", re.I)

def _abs_url(u: str, base: str) -> str:
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    if _abs.match(u):
        return u
    if u.startswith("/"):
        m = re.match(r"^(https?://[^/]+)/", base)
        return (m.group(1) if m else base.rstrip("/")) + u
    return u

def _is_article_like(url: str) -> bool:
    if "eastmoney.com" not in url:
        return False
    return (
        "/a/" in url
        or re.search(r"/20\d{2}[-/]?\d{1,2}[-/]?\d{1,2}", url) is not None
        or url.endswith(".html")
    )

_date_regex = re.compile(
    r"(?P<y>20\d{2})[年\-/\.](?P<m>\d{1,2})[月\-/\.](?P<d>\d{1,2})[日\sT]*"
    r"(?P<h>\d{1,2}):(?P<mi>\d{1,2})(?::(?P<s>\d{1,2}))?",
)

def _parse_dt(s: str) -> dt.datetime | None:
    if not s:
        return None
    m = _date_regex.search(s)
    if not m:
        return None
    y, mth, d = int(m["y"]), int(m["m"]), int(m["d"])
    h, mi, ss = int(m["h"]), int(m["mi"]), int(m["s"] or 0)
    try:
        return dt.datetime(y, mth, d, h, mi, ss)
    except ValueError:
        return None

def _text(el) -> str:
    return re.sub(r"\s+", " ", (el.get_text(strip=True) if el else "")).strip()

# ---------------- parse ----------------
def _extract_candidates(client: httpx.Client, channel_url: str) -> List[str]:
    logger.info(f"[eastmoney] 抓取频道页：{channel_url}")
    r = _try_get(client, channel_url)
    soup = BeautifulSoup(r.text, "lxml")
    links = []
    for a in soup.find_all("a", href=True):
        u = _abs_url(a.get("href", ""), channel_url)
        if _is_article_like(u):
            links.append(u)
    uniq = list(dict.fromkeys(links))
    logger.info(f"[eastmoney] 本页经筛选的“像文章”链接：{len(uniq)} 条")
    return uniq

def _pick_title(soup: BeautifulSoup) -> str:
    for sel in [
        "h1#newsTitle", "h1#artTitle", "div.xwtitle h1", "div.title h1",
        "h1#ArtTitle", "h1", "div.article-title h1", "div.headline h1",
    ]:
        el = soup.select_one(sel)
        if el and _text(el):
            return _text(el)
    t = soup.title.string if soup.title else ""
    return re.sub(r"_?东方财富.*$", "", t or "").strip()

def _pick_time(soup: BeautifulSoup) -> dt.datetime | None:
    candidates = []
    for sel in [
        "div.time", "span.time", "p.time", "div#newsInfo", "div.em-time",
        "div.title", "div.infotime", "div.article-info", "div.pubtime",
    ]:
        el = soup.select_one(sel)
        if el:
            candidates.append(el.get_text(" ", strip=True))
    page_text = soup.get_text(" ", strip=True)
    if len(page_text) > 6000:
        page_text = page_text[:6000]
    candidates.append(page_text)
    for s in candidates:
        d = _parse_dt(s)
        if d:
            return d
    return None

def _pick_summary(soup: BeautifulSoup) -> str:
    for sel in [
        "div#ContentBody", "div#articleContent", "div#newsContent",
        "div.txtinfos", "div.article-content", "div.em-article",
        "div#ContentBody p", "div#articleContent p", "div#newsContent p",
    ]:
        els = soup.select(sel)
        if els:
            ps = els if sel.endswith(" p") else els[0].find_all("p")
            text = " ".join(_text(p) for p in ps if _text(p))[:200]
            if text:
                return text
    m = soup.find("meta", attrs={"name": "description"})
    if m and m.get("content"):
        return m["content"].strip()[:200]
    return ""

def _fetch_article(client: httpx.Client, url: str) -> Dict[str, Any] | None:
    try:
        r = _try_get(client, url, retries=3)
        soup = BeautifulSoup(r.text, "lxml")
        title = _pick_title(soup)
        pub_time = _pick_time(soup)
        summary = _pick_summary(soup)
        if not title:
            return None
        return {
            "title": title,
            "url": url,
            "pub_time": pub_time,
            "source": "eastmoney",
            "summary": summary,
        }
    except httpx.HTTPStatusError as e:
        logger.warning(f"[eastmoney] 文章状态错误：{url} - {e.response.status_code}")
        return None
    except Exception as e:
        logger.warning(f"[eastmoney] 文章解析失败：{url} - {e}")
        return None

# ---------------- public API ----------------
def fetch_list(limit: int = 50, since_days: int = 1, keyword: str | None = None) -> List[Dict[str, Any]]:
    """
    :param limit: 返回的最大条数
    :param since_days: 仅保留近 N 天
    :param keyword: 命中 title 或 summary 才保留；None 不筛
    """
    _prepare_proxy_env()
    cutoff = dt.datetime.now() - dt.timedelta(days=since_days)
    rows: list[dict[str, Any]] = []

    with _make_client() as client:
        # 汇总候选链接
        candidates: list[tuple[str, str]] = []
        for url, cate in CHANNELS:
            try:
                for u in _extract_candidates(client, url):
                    candidates.append((u, cate))
            except Exception as e:
                logger.warning(f"[eastmoney] 列表抓取失败：{url} - {e}")

        # 去重并逐篇抓取（顺序抓取，配合重试降低超时）
        seen = set()
        for url, cate in candidates:
            if url in seen:
                continue
            seen.add(url)

            art = _fetch_article(client, url)
            if not art:
                continue
            art["category"] = cate

            # 时间筛选（无法解析时间的不丢弃，但不参与时间过滤）
            pt: dt.datetime | None = art.get("pub_time")
            if pt and pt < cutoff:
                continue

            # 关键词（不区分大小写）
            if keyword:
                kw = keyword.lower()
                t_hit = kw in (art.get("title") or "").lower()
                s_hit = kw in (art.get("summary") or "").lower()
                if not (t_hit or s_hit):
                    continue

            rows.append(art)
            if len(rows) >= limit:
                break

        # 排序后截断
        rows.sort(key=lambda r: r.get("pub_time") or dt.datetime.min, reverse=True)
        return rows[:limit]
