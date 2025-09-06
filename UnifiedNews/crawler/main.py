# main.py
from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib
import os
import sys
from typing import Dict, List, Optional

from loguru import logger

# 日志格式（与示例输出相近）
logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {module}:{function}:{line} - {message}",
)

# 可用数据源 -> 模块路径
SOURCE_TO_MODULE: Dict[str, str] = {
    "cs": "spiders.cs",
    "stcn": "spiders.stcn",
    "eastmoney": "spiders.eastmoney",
    "ths": "spiders.ths",
}


def _call_fetch_list(module_name: str, limit: int, since_days: int, keyword: Optional[str]):
    mod = importlib.import_module(module_name)
    fn = getattr(mod, "fetch_list", None)
    if fn is None:
        raise RuntimeError(f"Module '{module_name}' missing fetch_list()")
    return fn(limit=limit, since_days=since_days, keyword=keyword)


def _union_fieldnames(rows: List[Dict]) -> List[str]:
    # 优先常用列，其它列按名称排序附在后面
    priority = ["site", "title", "url", "pub_time", "summary"]
    keys = set()
    for r in rows:
        keys.update(r.keys())
    ordered = [k for k in priority if k in keys]
    extra = sorted(k for k in keys if k not in ordered)
    return ordered + extra


def _write_csv(path: str, rows: List[Dict], fieldnames: List[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def dump_csv(rows: List[Dict], out_path: str) -> None:
    if not rows:
        logger.warning("No data grabbed.")
        return

    fieldnames = _union_fieldnames(rows)

    try:
        _write_csv(out_path, rows, fieldnames)
        logger.success(f"Saved {len(rows)} rows -> {out_path}")
        return
    except PermissionError:
        # 文件被占用或无权限时，自动改名重试
        base, ext = os.path.splitext(out_path)
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        alt_path = f"{base}_{ts}{ext or '.csv'}"
        logger.warning(f"Permission denied: '{out_path}'. Try '{alt_path}'.")
        _write_csv(alt_path, rows, fieldnames)
        logger.success(f"Saved {len(rows)} rows -> {alt_path}")
        return


def run_source(source: str, limit: int, out_path: str, since_days: int, keyword: Optional[str]) -> None:
    module_name = SOURCE_TO_MODULE.get(source)
    if not module_name:
        raise ValueError(f"Unknown source: {source}")

    rows = _call_fetch_list(module_name, limit, since_days, keyword)
    dump_csv(rows, out_path)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Collect A-share news from multiple sources.")
    p.add_argument(
        "--source",
        required=True,
        choices=list(SOURCE_TO_MODULE.keys()),
        help="news source",
    )
    p.add_argument("--limit", type=int, default=60, help="max number of articles")
    p.add_argument("--since-days", type=int, default=2, help="only keep articles within N days")
    p.add_argument("--keyword", type=str, default=None, help="filter by keyword (optional)")
    p.add_argument("--out", type=str, default="news.csv", help="output CSV path")
    return p


def main(argv: List[str]) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logger.info(
        f"Run {args.source}: limit={args.limit}, since_days={args.since_days}, "
        f"keyword={args.keyword}, out={args.out}"
    )

    try:
        run_source(args.source, args.limit, args.out, args.since_days, args.keyword)
        return 0
    except Exception as e:
        # 打印错误并抛出，让调用端看到完整回溯（与示例一致）
        logger.error(str(e))
        raise


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
