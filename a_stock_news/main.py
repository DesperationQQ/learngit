# main.py
import argparse
import csv
import datetime as dt
import importlib
import inspect
import os
import sys
from typing import Any, Dict, Iterable, List

from loguru import logger
from dateutil.tz import tzlocal

SOURCES = {
    "sina": "spiders.sina",
    "stcn": "spiders.stcn",
    "cs": "spiders.cs",
    "eastmoney": "spiders.eastmoney",  # 新增
}

PREFERRED_KEYS = [
    "title", "url", "pub_time", "source", "channel",
    "category", "author", "summary", "stock", "id",
]

def _iso_dt(v: Any) -> Any:
    if isinstance(v, dt.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=tzlocal())
        return v.isoformat()
    return v

def _guess_headers(rows: List[Dict[str, Any]]) -> List[str]:
    all_keys = set()
    for r in rows:
        all_keys.update(r.keys())
    ordered = [k for k in PREFERRED_KEYS if k in all_keys]
    extra = sorted(k for k in all_keys if k not in ordered)
    return ordered + extra

def dump_csv(rows: List[Dict[str, Any]], out_path: str) -> None:
    if not rows:
        logger.warning("No data grabbed.")
        return

    headers = _guess_headers(rows)
    normalized_rows = [{k: _iso_dt(r.get(k, "")) for k in headers} for r in rows]

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    def _write(path: str):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            w.writeheader()
            w.writerows(normalized_rows)

    try:
        _write(out_path)
        logger.success(f"Saved {len(rows)} rows -> {out_path}")
    except PermissionError:
        base, ext = os.path.splitext(out_path)
        alt = f"{base}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}{ext or '.csv'}"
        logger.warning(f"Permission denied: '{out_path}'. Try '{alt}'.")
        _write(alt)
        logger.success(f"Saved {len(rows)} rows -> {alt}")

def _call_fetch_list(module_name: str, limit: int, since_days: int, keyword: str | None):
    mod = importlib.import_module(module_name)
    if not hasattr(mod, "fetch_list"):
        raise AttributeError(f"{module_name} missing fetch_list")
    fn = getattr(mod, "fetch_list")
    sig = inspect.signature(fn)
    kwargs = {}
    if "limit" in sig.parameters:
        kwargs["limit"] = limit
    if "since_days" in sig.parameters:
        kwargs["since_days"] = since_days
    if "keyword" in sig.parameters:
        kwargs["keyword"] = keyword
    return fn(**kwargs)

def run_source(source: str, limit: int, out: str, since_days: int, keyword: str | None):
    module_name = SOURCES[source]
    rows = _call_fetch_list(module_name, limit, since_days, keyword)
    if isinstance(rows, list) and limit and len(rows) > limit:
        rows = rows[:limit]
    dump_csv(rows, out)

def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True, choices=sorted(SOURCES.keys()))
    p.add_argument("--limit", type=int, default=60)
    p.add_argument("--since-days", type=int, default=2)
    p.add_argument("--keyword", type=str, default=None)
    p.add_argument("--out", type=str, default=None)
    return p.parse_args(list(argv))

def main(argv: Iterable[str]) -> int:
    args = parse_args(argv)
    out_path = args.out or f"{args.source}_news.csv"
    logger.info(
        f"Run {args.source}: limit={args.limit}, since_days={args.since_days}, "
        f"keyword={args.keyword!r}, out={out_path}"
    )
    try:
        run_source(args.source, args.limit, out_path, args.since_days, args.keyword)
        return 0
    except Exception as e:
        logger.exception(e)
        return 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
