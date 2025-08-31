# spiders/cs.py
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from loguru import logger

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
    "Referer": "https://www.cs.com.cn/",
}

CS_HOST = "www.cs.com.cn"

# 尽量选“滚动/快讯/要闻”类入口；有的频道可能偶尔 404，代码会忽略失败继续下一条
CHANNELS = [
    "https://www.cs.com.cn/roll/",          # 滚动新闻
    "https://www.cs.com.cn/xwzx/",          # 新闻中心
    "https://www.cs.com.cn/ssgs/",          # 上市公司
    "https://www.cs.com.cn/cj/",            # 财经
    "https://www.cs.com.cn/stock/",         # 股票
]

def _clean_text(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def _parse_dt_from_text(text: str) -> Optional[datetime]:
    if not text:
        return None
    # 常见格式：2025-08-30 15:20 或带“来源”“作者”
    m = re.search(r"(\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2})(?:\s+(\d{1,2}:\d{2})(?::\d{2})?)?", text)
    dt = None
    if m:
        try:
            dt = dtparser.parse(m.group(0), dayfirst=False, yearfirst=True)
        except Exception:
            dt = None
    if dt is None:
        try:
            dt = dtparser.parse(text, fuzzy=True, dayfirst=False, yearfirst=True)
        except Exception:
            return None
    CST = timezone(timedelta(hours=8))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    else:
        dt = dt.astimezone(CST)
    return dt

def _prepare_proxy_env() -> None:
    http_p = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    https_p = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not (http_p or https_p):
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
    logger.info(f"[CS] proxy via {os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy')}")

def _make_client() -> httpx.Client:
    _prepare_proxy_env()
    transport = httpx.HTTPTransport(retries=3)
    client = httpx.Client(
        headers=HEADERS,
        timeout=httpx.Timeout(20.0, connect=20.0),
        follow_redirects=True,
        verify=True,
        trust_env=True,
        http2=False,
        transport=transport,
    )
    logger.info(f"[CS] httpx={httpx.__version__}, trust_env=True")
    return client

def _is_article_like(href: str) -> bool:
    if not href:
        return False
    u = urlparse(href)
    if u.netloc and CS_HOST not in u.netloc:
        return False
    # 中国证券网大多是 .shtml / .html
    if not (href.lower().endswith(".shtml") or href.lower().endswith(".html")):
        return False
    # 排除明显的索引页/导航页
    bad = ("index.html", "index.shtml", "/roll/index", "/xwzx/index")
    if any(b in href for b in bad):
        return False
    return True

def _collect_article_urls(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    urls: List[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        abs_url = urljoin(base_url, href)
        if _is_article_like(abs_url):
            urls.append(abs_url)
    # 去重且限定域名
    seen, out = set(), []
    for u in urls:
        if CS_HOST in urlparse(u).netloc and u not in seen:
            seen.add(u)
            out.append(u)
    return out

def _fetch_html_with_fallback(url: str) -> Optional[str]:
    try:
        with _make_client() as client:
            r = client.get(url)
            if r.status_code == 200 and r.text:
                return r.text
    except Exception as e:
        logger.warning(f"[CS] httpx 详情失败：{url} - {e}")
    # 降级 requests（忽略证书，尽量拿到内容）
    try:
        import requests
        proxies = {}
        if os.environ.get("HTTP_PROXY"):
            proxies["http"] = os.environ["HTTP_PROXY"]
        if os.environ.get("HTTPS_PROXY"):
            proxies["https"] = os.environ["HTTPS_PROXY"]
        r = requests.get(
            url,
            headers=HEADERS,
            timeout=20,
            verify=False,
            allow_redirects=True,
            proxies=proxies or None,
        )
        if r.status_code == 200 and r.text:
            return r.text
    except Exception as e:
        logger.warning(f"[CS] requests 降级仍失败：{url} - {e}")
    return None

def _parse_detail(url: str) -> Optional[Dict]:
    html = _fetch_html_with_fallback(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")

    # 标题
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = _clean_text(h1.get_text())
    if not title:
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content"):
            title = _clean_text(og["content"])

    # 时间：常见在“来源/作者/发布时间”附近；也尝试 meta
    pub_text = ""
    time_node = soup.find(
        lambda tag: tag.name in ("span", "div", "p")
        and tag.get_text()
        and re.search(r"\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}", tag.get_text())
    )
    if time_node:
        pub_text = _clean_text(time_node.get_text())
    if not pub_text:
        meta_time = soup.find("meta", attrs={"name": re.compile(r"(publish|pub|date|time)", re.I)})
        if meta_time and meta_time.get("content"):
            pub_text = meta_time["content"].strip()
    pub_dt = _parse_dt_from_text(pub_text)

    # 正文：常见容器命名
    content = ""
    article = soup.find("div", id=re.compile(r"(content|cont|article|art|txt)", re.I)) \
              or soup.find("div", class_=re.compile(r"(content|cont|article|art|txt|detail)", re.I)) \
              or soup.find("article")
    if article:
        paragraphs = [_clean_text(p.get_text(" ", strip=True)) for p in article.find_all(["p", "div"])]
        content = _clean_text(" ".join([p for p in paragraphs if p]))

    if not title and not content:
        return None

    return {
        "url": url,
        "title": title or "",
        "pub_dt": pub_dt,  # datetime 或 None
        "content": content or "",
        "source": "cs",
    }

def _fetch_list_from_html(channel_url: str) -> List[str]:
    with _make_client() as client:
        logger.info(f"[CS] 抓取频道页：{channel_url}")
        r = client.get(channel_url)
        r.raise_for_status()
        html = r.text
    urls = _collect_article_urls(html, base_url=channel_url)
    logger.info(f"[CS] 本页经筛选的“像文章”链接：{len(urls)} 条")
    return urls

def fetch_list(limit: int = 50,
               since_days: Optional[int] = None,
               keyword: Optional[str] = None) -> List[Dict]:
    # 1) 汇总多个频道的列表链接
    all_urls: List[str] = []
    for ch in CHANNELS:
        try:
            urls = _fetch_list_from_html(ch)
            all_urls.extend(urls)
        except Exception as e:
            logger.warning(f"[CS] 列表抓取失败：{ch} - {e}")

    # 2) 去重
    uniq, seen = [], set()
    for u in all_urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)

    # 3) 解析详情（多抓一些再过滤）
    candidate = uniq[: max(limit * 3, 150)]
    results: List[Dict] = []
    CST = timezone(timedelta(hours=8))
    since_edge = None
    if since_days:
        since_edge = datetime.now(CST) - timedelta(days=since_days)

    for url in candidate:
        d = _parse_detail(url)
        if not d:
            continue

        # since_days 过滤
        if since_edge and d.get("pub_dt"):
            pub_dt = d["pub_dt"]
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=CST)
            else:
                pub_dt = pub_dt.astimezone(CST)
            if pub_dt < since_edge:
                continue

        # 关键词过滤（标题或正文）
        if keyword:
            kw = keyword.lower()
            hay = (d.get("title", "") + " " + d.get("content", "")).lower()
            if kw not in hay:
                continue

        results.append(d)
        if len(results) >= limit:
            break

    # 4) 时间倒序
    def _key(x):
        dt = x.get("pub_dt")
        if dt is None:
            return datetime(1970, 1, 1, tzinfo=CST)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=CST)
        return dt.astimezone(CST)

    results.sort(key=_key, reverse=True)
    return results
