# spiders/stcn.py
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from loguru import logger

# -----------------------
# 常量 & 工具
# -----------------------

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
    "Referer": "https://www.stcn.com/",
}

REALTIME_CHANNELS = [
    # 证监会网站的“快讯”频道（实时更新）
    "https://www.stcn.com/kuaixun/",
    # 备用页（有些时候 index.shtml 存在，有时无）
    "https://www.stcn.com/kuaixun/index.shtml",
]

STCN_HOST = "www.stcn.com"


def _clean_text(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _parse_dt_from_text(text: str) -> Optional[datetime]:
    """
    解析“发布时间”。支持“2025-08-30 15:20”或 meta/页面混杂文字。
    返回 UTC+8 的 aware datetime；失败返回 None。
    """
    if not text:
        return None

    # 快速尝试：纯日期/时间
    m = re.search(r"(\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2})(?:\s+(\d{1,2}:\d{2})(?::\d{2})?)?", text)
    dt = None
    if m:
        try:
            dt = dtparser.parse(m.group(0), dayfirst=False, yearfirst=True)
        except Exception:
            dt = None

    # 兜底：让 dateutil 自己 try
    if dt is None:
        try:
            dt = dtparser.parse(text, fuzzy=True, dayfirst=False, yearfirst=True)
        except Exception:
            return None

    # 规范为东八区 aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
    else:
        dt = dt.astimezone(timezone(timedelta(hours=8)))
    return dt


def _prepare_proxy_env() -> None:
    """
    统一启用代理（优先沿用系统/环境变量；如无，则尝试 127.0.0.1:7890）
    """
    http_p = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    https_p = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not (http_p or https_p):
        # 你的环境里 Clash 多数为此端口
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
    logger.info(f"[STCN] proxy via {os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy')}")


def _make_client() -> httpx.Client:
    """
    创建一个尽量“稳”的 httpx 客户端：
    - http2=False：不少代理/目标站对 h2 不稳定；
    - HTTPTransport(retries=3)：传输层自动重试；
    - trust_env=True：继承系统/环境代理设置。
    """
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
    logger.info(f"[STCN] httpx={httpx.__version__}, trust_env=True")
    return client


def _is_article_like(href: str) -> bool:
    """
    过滤“像文章”的链接：
    - stcn 域名
    - .html 结尾
    - 排除明显频道页/导航页
    """
    if not href:
        return False
    u = urlparse(href)
    if u.netloc and STCN_HOST not in u.netloc:
        return False
    if not href.lower().endswith(".html"):
        return False
    # 排除导航/分类
    bad = ("index.html", "/kuaixun/index", "/kuaixun/index.shtml")
    if any(b in href for b in bad):
        return False
    return True


def _collect_article_urls(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    urls: List[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        # 绝对化
        abs_url = urljoin(base_url, href)
        if _is_article_like(abs_url):
            urls.append(abs_url)

    # 去重且只保留 stcn 域名
    seen = set()
    out = []
    for u in urls:
        if STCN_HOST in urlparse(u).netloc and u not in seen:
            seen.add(u)
            out.append(u)
    return out


# -----------------------
# 详情页解析（带降级抓取）
# -----------------------

def _fetch_html_with_fallback(url: str) -> Optional[str]:
    """
    先用 httpx 抓；失败时再降级用 requests（verify=False）。
    """
    # 1) httpx 首选
    try:
        with _make_client() as client:
            r = client.get(url)
            if r.status_code == 200 and r.text:
                return r.text
    except Exception as e:
        logger.warning(f"[STCN] httpx 详情失败：{url} - {e}")

    # 2) requests 降级
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
            verify=False,  # 降级：允许证书问题
            allow_redirects=True,
            proxies=proxies or None,
        )
        if r.status_code == 200 and r.text:
            return r.text
    except Exception as e:
        logger.warning(f"[STCN] requests 降级仍失败：{url} - {e}")

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

    # 时间（页面常见位置 / meta）
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

    # 正文
    content = ""
    article = soup.find("div", class_=re.compile(r"(article|txt|content|detail)", re.I))
    if not article:
        article = soup.find("article")
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
        "source": "stcn",
    }


# -----------------------
# 列表抓取（HTML）
# -----------------------

def _fetch_list_from_html(channel_url: str) -> List[str]:
    """
    从频道页提取“像文章”的链接列表。
    """
    with _make_client() as client:
        logger.info(f"[STCN] 抓取快讯列表页：{channel_url}")
        r = client.get(channel_url)
        r.raise_for_status()
        html = r.text

    urls = _collect_article_urls(html, base_url=channel_url)
    logger.info(f"[STCN] 本页共发现链接 {len(urls)} 个（经筛选）")
    return urls


# -----------------------
# 对外主函数
# -----------------------

def fetch_list(limit: int = 50,
               since_days: Optional[int] = None,
               keyword: Optional[str] = None) -> List[Dict]:
    """
    抓取 STCN（证券时报网）快讯频道新闻。
    - limit：最多返回多少条
    - since_days：只要最近 N 天；None 表示不过滤
    - keyword：只保留标题/正文里包含该关键词的记录（大小写不敏感）
    """
    all_urls: List[str] = []

    # 只用“实时快讯”频道，避免抓到 2022 年的旧站
    for ch in REALTIME_CHANNELS:
        try:
            urls = _fetch_list_from_html(ch)
            all_urls.extend(urls)
        except Exception as e:
            logger.warning(f"[STCN] 列表抓取失败：{ch} - {e}")

    # 去重
    uniq = []
    seen = set()
    for u in all_urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)

    # 为了尽量新 -> 旧，先不解析时间，直接前 N*2 条再去抓详情
    # 多抓一些后再根据 since_days/keyword/时间排序过滤
    candidate = uniq[: max(limit * 3, 150)]

    results: List[Dict] = []
    for url in candidate:
        d = _parse_detail(url)
        if not d:
            continue

        # since_days
        if since_days and d.get("pub_dt"):
            edge = datetime.now(timezone(timedelta(hours=8))) - timedelta(days=since_days)
            if d["pub_dt"] < edge:
                continue

        # keyword（标题或正文里）
        if keyword:
            kw = keyword.lower()
            if kw not in (d.get("title", "").lower() + " " + d.get("content", "").lower()):
                continue

        results.append(d)

        if len(results) >= limit:
            break

    # 按时间倒序
    results.sort(
        key=lambda x: x.get("pub_dt") or datetime(1970, 1, 1, tzinfo=timezone(timedelta(hours=8))),
        reverse=True,
    )
    return results
