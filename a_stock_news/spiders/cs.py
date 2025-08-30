# spiders/cs.py
from __future__ import annotations
from typing import List, Dict
import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from loguru import logger

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://stock.cnstock.com/",
}

LIST_URLS = [
    "https://stock.cnstock.com/",             # 股票频道首页
    "https://company.cnstock.com/",           # 公司新闻
    "https://news.cnstock.com/news/sns_yw/"   # 要闻
]

def _parse_time(s: str) -> str | None:
    try:
        dt = dtparser.parse(s, fuzzy=True)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

def _extract_text(soup: BeautifulSoup) -> str:
    candidates = [
        "div#appContent", "div.article", "div.content", "div#content",
        "div.txt", "article", "div.detail"
    ]
    for sel in candidates:
        node = soup.select_one(sel)
        if node:
            paras = [p.get_text(strip=True) for p in node.select("p") if p.get_text(strip=True)]
            if len("".join(paras)) >= 60:
                return "\n".join(paras)
    # fallback
    paras = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
    return "\n".join(paras[:200])

def fetch_list(limit: int = 50, timeout: float = 10.0) -> List[Dict]:
    items: List[Dict] = []
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=timeout) as client:
        for url in LIST_URLS:
            logger.info(f"[CS] 抓取列表页：{url}")
            try:
                r = client.get(url)
                r.raise_for_status()
            except Exception as e:
                logger.warning(f"[CS] 列表请求失败：{url} - {e}")
                continue

            soup = BeautifulSoup(r.text, "lxml")
            links = soup.select("ul li a, div.list li a, div.news_list a, h2 a")
            for a in links:
                title = a.get_text(strip=True)
                href = a.get("href") or ""
                if not title or not href:
                    continue
                if not href.startswith("http"):
                    href = httpx.URL(url).join(href)
                items.append({
                    "title": title,
                    "url": str(href),
                    "site": "CS",
                })
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break

    # 去重
    seen = set()
    uniq = []
    for it in items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        uniq.append(it)
    logger.info(f"[CS] 共解析到 {len(uniq)} 条列表")
    return uniq[:limit]

def fetch_detail(url: str, timeout: float = 10.0) -> Dict:
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=timeout) as client:
        r = client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        title = soup.select_one("h1, h2.title, h1#title")
        title = title.get_text(strip=True) if title else ""

        tm = None
        # 常见 meta 时间
        meta = soup.select_one("meta[property='article:published_time'], meta[name='publishdate']")
        if meta and meta.get("content"):
            tm = _parse_time(meta["content"])
        if not tm:
            node = soup.select_one("span.time, span.pubtime, div.info, div.time")
            if node:
                tm = _parse_time(node.get_text(" ", strip=True))

        content = _extract_text(soup)

        return {
            "title": title or "",
            "url": url,
            "published_at": tm,
            "content": content,
            "site": "CS",
        }
