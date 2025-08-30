# main.py
import argparse
import csv
import datetime as dt
from typing import List, Dict, Optional

from loguru import logger

from spiders import sina
from spiders import stcn


# === 时间工具：统一为东八区 aware ===
CST = dt.timezone(dt.timedelta(hours=8))

def as_cst_aware(x: Optional[dt.datetime]) -> Optional[dt.datetime]:
    """把 None/naive/其他时区的 datetime 统一成东八区 aware；其余返回 None。"""
    if x is None:
        return None
    if not isinstance(x, dt.datetime):
        return None
    if x.tzinfo is None:
        return x.replace(tzinfo=CST)
    return x.astimezone(CST)


# === 结果写盘 ===
def dump_csv(rows: List[Dict], out_path: str):
    if not rows:
        logger.warning("No data grabbed.")
        return
    # 统一 pub_dt -> ISO 字符串
    for r in rows:
        if "pub_dt" in r:
            r["pub_dt"] = as_cst_aware(r.get("pub_dt"))
            r["pub_dt"] = r["pub_dt"].isoformat(timespec="seconds") if r["pub_dt"] else ""
    keys = rows[0].keys()
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    logger.success(f"Saved {len(rows)} rows -> {out_path}")


# === 过滤：最近 N 天 ===
def filter_by_since_days(rows: List[Dict], since_days: Optional[int]) -> List[Dict]:
    if not since_days:
        return rows
    cutoff = dt.datetime.now(CST) - dt.timedelta(days=since_days)
    out = []
    for r in rows:
        d = as_cst_aware(r.get("pub_dt"))
        if d and d >= cutoff:
            out.append(r)
    return out


# === 过滤：关键词（标题或正文）===
def filter_by_keyword(rows: List[Dict], keyword: Optional[str]) -> List[Dict]:
    if not keyword:
        return rows
    kw = keyword.lower()
    out = []
    for r in rows:
        title = (r.get("title") or "").lower()
        content = (r.get("content") or "").lower()
        if kw in title or kw in content:
            out.append(r)
    return out


# === 各源运行 ===
def run_sina(limit: int, out: str, since_days: Optional[int], keyword: Optional[str]):
    items = sina.fetch_list_sync(limit=limit)
    items = filter_by_since_days(items, since_days)
    items = filter_by_keyword(items, keyword)
    # 按时间倒序
    items.sort(key=lambda x: as_cst_aware(x.get("pub_dt")) or dt.datetime(1970,1,1,tzinfo=CST), reverse=True)
    dump_csv(items, out)


def run_stcn(limit: int, out: str, since_days: Optional[int], keyword: Optional[str]):
    # stcn.fetch_list 内部已尽量抓“实时快讯”，但这里再做统一过滤/排序
    lst = stcn.fetch_list(limit=limit * 2, keyword=keyword)  # 多抓一些，后面再截
    lst = filter_by_since_days(lst, since_days)
    lst = filter_by_keyword(lst, keyword)
    lst.sort(key=lambda x: as_cst_aware(x.get("pub_dt")) or dt.datetime(1970,1,1,tzinfo=CST), reverse=True)
    lst = lst[:limit]
    dump_csv(lst, out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, choices=["sina", "stcn"], help="news source")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--since-days", type=int, default=None, help="only keep items within N days")
    ap.add_argument("--keyword", type=str, default=None, help="filter by keyword in title/content")
    ap.add_argument("--out", type=str, default="news.csv")
    args = ap.parse_args()

    if args.source == "sina":
        run_sina(args.limit, args.out, args.since_days, args.keyword)
    elif args.source == "stcn":
        run_stcn(args.limit, args.out, args.since_days, args.keyword)
