# main.py
import argparse
import csv
from datetime import datetime, timedelta, timezone
from loguru import logger

# 源：按需导入，缺哪个就跳过
try:
    from spiders import stcn
except Exception as e:
    stcn = None
    logger.warning(f"[LOAD] STCN not available: {e}")

try:
    from spiders import cs
except Exception as e:
    cs = None
    logger.warning(f"[LOAD] CS not available: {e}")

try:
    from spiders import sina
except Exception:
    sina = None  # 你的工程里可能没有；忽略即可

CN_TZ = timezone(timedelta(hours=8))


def _to_dt(s: str | None):
    if not s:
        return None
    # 兼容 "YYYY-MM-DD HH:MM:SS"
    try:
        return datetime.fromisoformat(s.replace(" ", "T")).astimezone(CN_TZ)
    except Exception:
        return None


def _recent_enough(row: dict, since_days: int) -> bool:
    """优先用 row['published_at']；没有就尝试从 URL 提取（stcn 提供了工具）。"""
    cutoff = datetime.now(CN_TZ) - timedelta(days=since_days)
    dt = _to_dt(row.get("published_at"))
    if dt:
        return dt >= cutoff

    # URL 提取（仅 stcn/cs 能较稳提到日期）
    url = row.get("url", "")
    try:
        if "stcn.com" in url and stcn:
            dt2 = stcn.date_from_url(url)
            if dt2:
                return dt2 >= cutoff
    except Exception:
        pass
    return False  # 没时间就丢弃，确保“近 N 天”


def _keyword_ok(row: dict, keyword: str | None) -> bool:
    if not keyword:
        return True
    title = (row.get("title") or "").lower()
    return keyword.lower() in title


def dump_csv(rows, out_path: str):
    if not rows:
        logger.warning("No data grabbed.")
        return
    keys = rows[0].keys()
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    logger.success(f"Saved {len(rows)} rows -> {out_path}")

    # 附带输出 URL 列（去重）
    try:
        urls = []
        seen = set()
        for r in rows:
            u = r.get("url")
            if u and u not in seen:
                seen.add(u); urls.append(u)
        url_path = out_path.rsplit(".", 1)[0] + "_urls.txt"
        with open(url_path, "w", encoding="utf-8") as f:
            f.write("\n".join(urls))
        logger.success(f"Also saved URLs -> {url_path}")
    except Exception as e:
        logger.warning(f"Save URLs failed: {e}")


def run_stcn(limit: int, out: str, since_days: int, keyword: str | None):
    if not stcn:
        logger.error("[STCN] 模块缺失")
        return
    lst = stcn.fetch_list(limit=limit)

    # 拉详情（发布时间、正文）
    rows = []
    for it in lst:
        try:
            r = stcn.fetch_detail(it["url"])
            # 补上列表拿到的标题作为兜底
            if not r.get("title"):
                r["title"] = it.get("title", "")
            if _recent_enough(r, since_days) and _keyword_ok(r, keyword):
                rows.append(r)
        except Exception as e:
            logger.warning(f"[STCN] 详情失败：{it['url']} - {e}")
    if not rows:
        logger.warning("[STCN] No data after filtering.")
        return
    dump_csv(rows, out)


def run_cs(limit: int, out: str, since_days: int, keyword: str | None):
    if not cs:
        logger.error("[CS] 模块缺失")
        return
    lst = cs.fetch_list(limit=limit)

    from spiders.cs import fetch_detail as cs_detail  # 确保可用
    rows = []
    for it in lst:
        try:
            r = cs_detail(it["url"])
            if not r.get("title"):
                r["title"] = it.get("title", "")
            if _recent_enough(r, since_days) and _keyword_ok(r, keyword):
                rows.append(r)
        except Exception as e:
            logger.warning(f"[CS] 详情失败：{it['url']} - {e}")
    if not rows:
        logger.warning("[CS] No data after filtering.")
        return
    dump_csv(rows, out)


def run_sina(limit: int, out: str, since_days: int, keyword: str | None):
    if not sina:
        logger.error("[SINA] 模块缺失")
        return
    # 你的工程里如果有 fetch_list_sync 就用之；否则按你已有实现改
    lst = sina.fetch_list_sync(limit=limit)  # 已包含 title/url/published_at?
    rows = []
    for it in lst:
        if _recent_enough(it, since_days) and _keyword_ok(it, keyword):
            rows.append(it)
    if not rows:
        logger.warning("[SINA] No data after filtering.")
        return
    dump_csv(rows, out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, choices=["stcn", "cs", "sina"], help="news source")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--out", type=str, default="news.csv")
    ap.add_argument("--since-days", type=int, default=7, help="only keep news within N days")
    ap.add_argument("--keyword", type=str, default=None, help="filter by keyword in title")
    args = ap.parse_args()

    if args.source == "stcn":
        run_stcn(args.limit, args.out, args.since_days, args.keyword)
    elif args.source == "cs":
        run_cs(args.limit, args.out, args.since_days, args.keyword)
    elif args.source == "sina":
        run_sina(args.limit, args.out, args.since_days, args.keyword)
