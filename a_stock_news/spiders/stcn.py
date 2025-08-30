# spiders/stcn.py —— STCN 快讯抓取：只抓 kuaixun.stcn.com；RSS 优先、HTML 兜底；近 N 天；关键词由 main.py 过滤
from __future__ import annotations

import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from loguru import logger

# ========= 配置 =========
PROXY_URL: Optional[str] = "http://127.0.0.1:7890"   # 按需改；无代理就设为 None
ONLY_DOMAIN = "kuaixun.stcn.com"                     # 只收快讯域
CN_TZ = timezone(timedelta(hours=8))

# RSS（可能为空/限流，故有 HTML 兜底）
RSS_URLS = [
    "https://kuaixun.stcn.com/rss/",
    "https://kuaixun.stcn.com/index.xml",
]
HTML_LIST_URLS = [
    "https://kuaixun.stcn.com/",
    "https://kuaixun.stcn.com/index.shtml",
]

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://kuaixun.stcn.com/",
}

# ========= 工具 =========
def _prepare_proxy_env():
    if PROXY_URL:
        os.environ["HTTP_PROXY"] = PROXY_URL
        os.environ["HTTPS_PROXY"] = PROXY_URL
        os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")
        logger.info(f"[STCN] proxy via {PROXY_URL}")
    else:
        for k in ("HTTP_PROXY", "HTTPS_PROXY"):
            os.environ.pop(k, None)

def _make_client() -> httpx.Client:
    _prepare_proxy_env()
    transport = httpx.HTTPTransport(retries=2)
    client = httpx.Client(
        headers=HEADERS,
        timeout=12.0,
        follow_redirects=True,
        trust_env=True,
        transport=transport,
    )
    logger.info(f"[STCN] httpx={httpx.__version__}, trust_env=True")
    return client

def _parse_time(text: str | None) -> str | None:
    if not text:
        return None
    try:
        dt = dtparser.parse(text, fuzzy=True)
        return dt.astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

def _extract_text(soup: BeautifulSoup) -> str:
    for sel in [
        "div.txt", "div.article", "div.artical-content", "div#ctrlfscont",
        "div#content", "div#article-content", "div.main-text", "article",
        "div.detail", "div#appContent",
    ]:
        node = soup.select_one(sel)
        if not node:
            continue
        paras = [p.get_text(strip=True) for p in node.select("p") if p.get_text(strip=True)]
        if len("".join(paras)) >= 60:
            return "\n".join(paras)
    paras = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
    return "\n".join(paras[:200])

def _looks_like_article(href: str) -> bool:
    """仅快讯域，.shtml/.html，且路径像日期"""
    try:
        u = urllib.parse.urlparse(href)
    except Exception:
        return False
    if not u.scheme.startswith("http"):
        return False
    host = (u.netloc or "").lower()
    if host != ONLY_DOMAIN:
        return False
    path = (u.path or "").lower()
    if not (path.endswith(".shtml") or path.endswith(".html")):
        return False
    if re.search(r"/20\d{2}(-|/|_)\d{2}([-/]\d{2})?", path):
        return True
    if re.search(r"/20\d{2}(0[1-9]|1[0-2])(\d{2})?/", path):
        return True
    return False

def date_from_url(href: str) -> datetime | None:
    """从 URL 提取日期（给 main.py 二次过滤也能复用）"""
    try:
        u = urllib.parse.urlparse(href)
        path = (u.path or "").lower()
    except Exception:
        return None

    m = re.search(r"/(20\d{2})-(\d{1,2})-(\d{1,2})/", path)
    if m:
        y, mo, d = map(int, m.groups())
        return datetime(y, mo, d, tzinfo=CN_TZ)

    m = re.search(r"/(20\d{2})-(\d{1,2})/", path)
    if m:
        y, mo = map(int, m.groups())
        return datetime(y, mo, 1, tzinfo=CN_TZ)

    m = re.search(r"/(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])/", path)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return datetime(y, mo, d, tzinfo=CN_TZ)

    m = re.search(r"/(20\d{2})(0[1-9]|1[0-2])/", path)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return datetime(y, mo, 1, tzinfo=CN_TZ)

    return None

# ========= 列表抓取 =========
def _fetch_list_from_rss(limit: int) -> List[Dict]:
    items: List[Dict] = []
    with _make_client() as client:
        for url in RSS_URLS:
            logger.info(f"[STCN] 抓取 RSS：{url}")
            try:
                r = client.get(url)
                r.raise_for_status()
            except Exception as e:
                logger.warning(f"[STCN] RSS 请求失败：{url} - {e}")
                continue

            soup = BeautifulSoup(r.text, "xml")
            found = kept = 0
            for it in soup.find_all("item"):
                title = (it.title or "").get_text(strip=True) if it.title else ""
                link = (it.link or "").get_text(strip=True) if it.link else ""
                pub  = (it.pubDate or "").get_text(strip=True) if it.pubDate else None
                if not title or not link:
                    continue
                found += 1
                if not _looks_like_article(link):
                    continue
                kept += 1
                items.append({
                    "title": title,
                    "url": link,
                    "published_at": _parse_time(pub),
                    "site": "STCN",
                })
                if len(items) >= limit:
                    break
            logger.info(f"[STCN] RSS 本页：发现 {found} 条，保留 {kept} 条")
            if len(items) >= limit:
                break
    return items[:limit]

def _fetch_list_from_html(limit: int) -> List[Dict]:
    items: List[Dict] = []
    with _make_client() as client:
        for url in HTML_LIST_URLS:
            logger.info(f"[STCN] 抓取快讯列表页：{url}")
            try:
                r = client.get(url)
                r.raise_for_status()
            except Exception as e:
                logger.warning(f"[STCN] 列表请求失败：{url} - {e}")
                continue

            soup = BeautifulSoup(r.text, "lxml")
            all_links = [a.get("href") or "" for a in soup.select("a[href]")]
            logger.info(f"[STCN] 本页共发现链接 {len(all_links)} 个（含导航）")

            kept = 0
            for a in soup.select("a[href]"):
                href = a.get("href") or ""
                title = a.get_text(strip=True)
                if not href or not title:
                    continue
                try:
                    href = str(httpx.URL(url).join(href))  # 绝对化
                except Exception:
                    continue
                if not _looks_like_article(href):
                    continue
                kept += 1
                items.append({"title": title, "url": href, "site": "STCN"})
                if len(items) >= limit:
                    break
            logger.info(f"[STCN] 本页通过筛选的“像文章”链接：{kept} 条")
            if len(items) >= limit:
                break

    # 去重
    uniq, seen = [], set()
    for it in items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        uniq.append(it)
    return uniq[:limit]

def fetch_list(limit: int = 50) -> List[Dict]:
    items = _fetch_list_from_rss(limit)
    if items:
        logger.info(f"[STCN] RSS 解析到 {len(items)} 条")
        return items
    logger.warning("[STCN] RSS 无数据，回退 HTML 方式")
    items = _fetch_list_from_html(limit)
    logger.info(f"[STCN] HTML 解析到 {len(items)} 条")
    return items

# ========= 详情抓取 =========
def fetch_detail(url: str) -> Dict:
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            with _make_client() as client:
                r = client.get(url)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "lxml")

                title_node = soup.select_one("h1, h2.title, h1#title")
                title = title_node.get_text(strip=True) if title_node else ""

                tm = None
                meta = soup.select_one(
                    "meta[property='article:published_time'], "
                    "meta[itemprop='datePublished'], meta[name='publishdate']"
                )
                if meta and meta.get("content"):
                    tm = _parse_time(meta["content"])
                if not tm:
                    tnode = soup.select_one("span.time, span.pubtime, div.info, div.time")
                    if tnode:
                        tm = _parse_time(tnode.get_text(" ", strip=True))

                content = _extract_text(soup)

                return {
                    "title": title or "",
                    "url": url,
                    "published_at": tm,
                    "content": content,
                    "site": "STCN",
                }
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.6)
                continue
            raise e
    raise last_err if last_err else RuntimeError("unknown error in fetch_detail")
