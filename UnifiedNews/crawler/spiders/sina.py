# spiders/sina.py
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from typing import List, Dict, Any

import httpx
from loguru import logger


API = "https://feed.mix.sina.com.cn/api/roll/get"
# 股票/证券栏目（财经频道）
DEFAULT_PARAMS = {
    "pageid": "153",
    "lid": "2510",   # 栏目 id（股票/证券）
    "k": "",         # 关键词（留空）
    "num": "50",     # 每页数量（后面会按需要覆盖）
    "page": "1",     # 第几页，从 1 开始
}


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn/roll/",
    "Accept": "application/json, text/plain, */*",
}


def ts_to_str(ts: int) -> str:
    """新浪返回的 ctime 是秒级时间戳。转为 ISO 字符串（本地时区）"""
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S%z")
    except Exception:
        return ""


async def _fetch_page(
    client: httpx.AsyncClient,
    page: int,
    page_size: int,
) -> List[Dict[str, Any]]:
    params = DEFAULT_PARAMS.copy()
    params["page"] = str(page)
    params["num"]  = str(page_size)

    r = await client.get(API, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()
    # 接口格式：{"result": {"status": {"code": 0}, "data": [ ... ]}}
    items = data.get("result", {}).get("data", []) or []
    out: List[Dict[str, Any]] = []
    for it in items:
        out.append(
            {
                "source": "新浪财经",
                "title": it.get("title", "").strip(),
                "url": it.get("url", "").strip(),
                "pub_time": ts_to_str(it.get("ctime") or 0),
                "summary": it.get("intro", "").strip(),
                "media": it.get("media_name", ""),
            }
        )
    return out


async def fetch_list(limit: int = 50) -> List[Dict[str, Any]]:
    """
    抓取新浪财经（股票/证券栏目）的最新新闻列表。
    - 自动按页抓取直到凑够 limit 条或没有更多数据
    - 走系统代理（如果你设置了 HTTP(S)_PROXY 会自动生效）
    """
    page_size = min(max(limit, 1), 50)        # 接口单页最多 50
    pages = math.ceil(limit / page_size)

    results: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for p in range(1, pages + 1):
            logger.info(f"Fetching Sina roll JSON page={p}, size={page_size}")
            try:
                items = await _fetch_page(client, p, page_size)
            except Exception as e:
                logger.warning(f"Sina page {p} fetch error: {e}")
                break

            if not items:
                logger.info("No more items from Sina.")
                break

            results.extend(items)
            if len(results) >= limit:
                break

            # 给服务器一点喘息时间，别太猛
            await asyncio.sleep(0.6)

    return results[:limit]


def fetch_list_sync(limit: int = 50) -> List[Dict[str, Any]]:
    """给 main.py 的同步入口用"""
    return asyncio.run(fetch_list(limit=limit))
