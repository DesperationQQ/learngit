# spiders/ths.py
# -*- coding: utf-8 -*-
"""
同花顺（10jqka）新闻爬虫
对外：fetch_list(limit=80, since_days=2, keyword=None) -> List[Dict]
"""
from __future__ import annotations

import os
import re
import time
import random
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

import httpx
from loguru import logger

SOURCE = "ths"
UA_LIST = [
    # 轮换 UA，降低风控命中
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.4 Safari/605.1.15",
]

CHANNEL_URLS = [
    "https://news.10jqka.com.cn/today_list/",
    "https://news.10jqka.com.cn/cjzx_list/",
]

ARTICLE_URL_RE = re.compile(
    r"https?://news\.10jqka\.com\.cn/\d{8}/c\d+\.shtml", flags=re.I
)

# ---------------------------
# 基础
# ---------------------------
def _prepare_proxy_env() -> None:
    http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if http_proxy or https_proxy:
        logger.info(f"[{SOURCE}] proxy via http={http_proxy} https={https_proxy}")
    else:
        logger.info(f"[{SOURCE}] no proxy in env (trust_env=True)")


def _make_client() -> httpx.Client:
    timeout = httpx.Timeout(30.0, connect=10.0, read=12.0, write=12.0)
    headers = {
        "User-Agent": random.choice(UA_LIST),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://news.10jqka.com.cn/",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    return httpx.Client(
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
        http2=False,     # 避免缺少 h2 依赖报错
        trust_env=True,
    )


def _strip_tags(s: str) -> str:
    s = re.sub(r"(?is)<script[^>]*>.*?</script>", "", s or "")
    s = re.sub(r"(?is)<style[^>]*>.*?</style>", "", s)
    s = re.sub(r"(?is)<!--.*?-->", "", s)
    s = re.sub(r"(?is)<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _to_dt(dt_str: str) -> Optional[datetime]:
    if not dt_str:
        return None
    dt_str = dt_str.strip()
    fmts = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
    ]
    for f in fmts:
        try:
            dt = datetime.strptime(dt_str, f)
            return dt.replace(tzinfo=timezone(timedelta(hours=8)))
        except Exception:
            pass
    return None


def _is_recent(pub: Optional[datetime], since_days: int) -> bool:
    if not pub:
        return True
    now = datetime.now(timezone(timedelta(hours=8)))
    return pub >= now - timedelta(days=since_days)


# ---------------------------
# 解析文章
# ---------------------------
def _smart_decode(resp: httpx.Response) -> str:
    # 优先按 meta 中的 gbk/gb2312 处理
    content = resp.content or b""
    low = content.lower()
    try_gbk = any(x in low for x in [b"charset=gbk", b"charset=\"gbk\"", b"gb2312"])
    try_utf8 = b"charset=utf-8" in low or b"charset=\"utf-8\"" in low
    if try_gbk:
        try:
            return content.decode("gb18030", "ignore")
        except Exception:
            pass
    if try_utf8:
        try:
            return content.decode("utf-8", "ignore")
        except Exception:
            pass
    # 兜底
    try:
        return resp.text
    except Exception:
        return content.decode("utf-8", "ignore")


def _extract_title(html: str) -> Optional[str]:
    pats = [
        r'property=["\']og:title["\']\s*content=["\']([^"\']+)["\']',
        r'name=["\']title["\']\s*content=["\']([^"\']+)["\']',
        r'<h1[^>]*id=["\']?artTitle["\']?[^>]*>(.*?)</h1>',
        r'<h2[^>]*id=["\']?artTitle["\']?[^>]*>(.*?)</h2>',
        r'<h1[^>]*class=["\'](?:art_tit|atc-title|title|main-title|article-title)[^"\']*["\'][^>]*>(.*?)</h1>',
        r'<h2[^>]*class=["\'](?:art_tit|atc-title|title|main-title|article-title)[^"\']*["\'][^>]*>(.*?)</h2>',
        r'<div[^>]*class=["\'](?:title|art_tit|atc-title|article-title)[^"\']*["\'][^>]*>\s*<h1[^>]*>(.*?)</h1>',
        r'<title[^>]*>(.*?)</title>',
        r'<h1[^>]*>(.*?)</h1>',  # 最后兜底
    ]
    for p in pats:
        m = re.search(p, html, flags=re.I | re.S)
        if m:
            return _strip_tags(m.group(1))
    return None


def _extract_pubtime(html: str) -> Optional[datetime]:
    cands: List[str] = []

    m = re.search(
        r'property=["\']article:published_time["\']\s*content=["\']([^"\']+)["\']',
        html, flags=re.I,
    )
    if m:
        cands.append(m.group(1))

    m = re.search(
        r'id=["\']pubtime_baidu["\'][^>]*>\s*([0-9]{4}[-/\.][0-9]{1,2}[-/\.][0-9]{1,2}\s+[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)',
        html, flags=re.I,
    )
    if m:
        cands.append(m.group(1))

    m = re.search(
        r'class=["\']time["\'][^>]*>\s*([0-9]{4}[-/\.][0-9]{1,2}[-/\.][0-9]{1,2}\s+[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)',
        html, flags=re.I,
    )
    if m:
        cands.append(m.group(1))

    m = re.search(
        r'发布(?:时间|于)[：:]\s*([0-9]{4}[-/\.][0-9]{1,2}[-/\.][0-9]{1,2}\s+[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)',
        html, flags=re.I,
    )
    if m:
        cands.append(m.group(1))

    m = re.search(r'"pubDate"\s*:\s*"([^"]+)"', html, flags=re.I)
    if m:
        cands.append(m.group(1))

    for s in cands:
        dt = _to_dt(s)
        if dt:
            return dt
    return None


def _extract_summary(html: str) -> Optional[str]:
    m = re.search(
        r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']',
        html, flags=re.I,
    )
    if m:
        return _strip_tags(m.group(1))[:300]

    blocks = re.findall(
        r'<div[^>]+class=["\'](?:art_main|atc-content|content|article|news-content|main-text|article-content)[^"\']*["\'][^>]*>(.*?)</div>',
        html, flags=re.I | re.S,
    )
    if blocks:
        text = _strip_tags(" ".join(blocks))
        return text[:300] if text else None

    ps = re.findall(r"<p[^>]*>(.*?)</p>", html, flags=re.I | re.S)
    if ps:
        text = _strip_tags(" ".join(ps))
        return text[:300] if text else None
    return None


def _fetch_article(client: httpx.Client, url: str) -> Optional[Dict]:
    try:
        r = client.get(url)
        r.raise_for_status()
        html = _smart_decode(r)
    except Exception as e:
        logger.warning(f"[{SOURCE}] 文章抓取失败：{url} - {e}")
        return None

    title = _extract_title(html)
    if not title:
        logger.warning(f"[{SOURCE}] 未能解析标题：{url}")
        return None

    pub = _extract_pubtime(html)
    summary = _extract_summary(html)
    ts = int((pub or datetime.now(timezone(timedelta(hours=8)))).timestamp())

    return {
        "source": SOURCE,
        "title": title,
        "url": url,
        "published_at": pub.isoformat() if pub else "",
        "ts": ts,
        "summary": summary or "",
    }


# ---------------------------
# 列表抓取
# ---------------------------
def _extract_candidates(client: httpx.Client, channel_url: str) -> List[str]:
    logger.info(f"[{SOURCE}] 抓取频道页：{channel_url}")
    try:
        r = client.get(channel_url)
        r.raise_for_status()
        html = _smart_decode(r)
    except Exception as e:
        logger.warning(f"[{SOURCE}] 频道页抓取失败：{channel_url} - {e}")
        return []

    links = ARTICLE_URL_RE.findall(html)
    seen, result = set(), []
    for u in links:
        if u not in seen:
            seen.add(u)
            result.append(u)
    logger.info(f"[{SOURCE}] 本页经筛选的“像文章”链接：{len(result)} 条")
    return result


# ---------------------------
# 对外主函数
# ---------------------------
def fetch_list(limit: int = 80, since_days: int = 2, keyword: Optional[str] = None) -> List[Dict]:
    _prepare_proxy_env()
    rows: List[Dict] = []

    with _make_client() as client:
        all_candidates: List[str] = []
        for ch in CHANNEL_URLS:
            all_candidates.extend(_extract_candidates(client, ch))

        # 去重
        dedup, candidates = set(), []
        for u in all_candidates:
            if u not in dedup:
                dedup.add(u)
                candidates.append(u)

        logger.info(f"[{SOURCE}] 候选链接总数（去重后）：{len(candidates)}")

        for url in candidates:
            if len(rows) >= limit:
                break

            time.sleep(0.2 + random.random() * 0.2)  # 轻限速

            item = _fetch_article(client, url)
            if not item:
                continue

            # 近 N 天
            pub_dt = None
            if item.get("published_at"):
                try:
                    pub_dt = datetime.fromisoformat(item["published_at"])
                except Exception:
                    pub_dt = None
            if not _is_recent(pub_dt, since_days):
                continue

            # 关键字
            if keyword:
                k = str(keyword).lower()
                text = (item.get("title", "") + " " + item.get("summary", "")).lower()
                if k not in text:
                    continue

            rows.append(item)

    return rows
