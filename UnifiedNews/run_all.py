# run_all.py —— 一键：先跑爬虫 → 再把 CSV 转为网页（含 stcn；强制用当前 venv 的 Python）
import argparse
import subprocess
import sys
import time
from pathlib import Path

PY = sys.executable  # 使用当前解释器（.venv\Scripts\python.exe）

def run(argv: list[str], cwd: Path | None = None):
    """以列表参数方式运行子进程；shell=False 确保不跑到系统/Anaconda 的 python。"""
    print("▶", " ".join(argv))
    ret = subprocess.run(argv, cwd=(str(cwd) if cwd else None), shell=False)
    if ret.returncode != 0:
        raise SystemExit(f"命令失败（exit {ret.returncode}）：{' '.join(argv)}")

def list_new_csv(csv_dir: Path, since: float) -> list[Path]:
    """返回在 since 之后新建/更新的 CSV 文件"""
    return sorted(
        p for p in csv_dir.rglob("*.csv")
        if p.is_file() and p.stat().st_mtime >= since - 1  # 保险 -1 秒
    )

def main():
    parser = argparse.ArgumentParser(
        description="统一环境一键流程：先爬虫，再把 CSV 转网页并在浏览器打开"
    )
    # 业务参数
    parser.add_argument(
        "--sources",
        nargs="+",
        default=["eastmoney", "cs", "ths", "stcn"],  # ★ 已加入 stcn
        help="要抓取的源列表（你的爬虫 main.py 支持的 source 名称）",
    )
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--since-days", type=int, default=2)
    parser.add_argument("--keyword", default=None)
    parser.add_argument("--browser", choices=["edge", "chrome", "firefox"], default="edge")
    parser.add_argument(
        "--convert-all",
        action="store_true",
        help="将 data/ 下的所有 CSV 都转网页（默认仅转本次运行中新生成/更新的文件）",
    )

    # 路径参数（按 UnifiedNews 的结构）
    root = Path(__file__).parent
    parser.add_argument("--crawler-dir", default=str(root / "crawler"))
    parser.add_argument("--csv-dir",     default=str(root / "data"))
    parser.add_argument("--webgen",      default=str(root / "webgen" / "csv_to_readable_html.py"))
    parser.add_argument("--html-dir",    default=str(root / "reports"))

    args = parser.parse_args()

    crawler_dir = Path(args.crawler_dir).resolve()
    csv_dir     = Path(args.csv_dir).resolve()
    webgen_py   = Path(args.webgen).resolve()
    html_dir    = Path(args.html_dir).resolve()

    # 基础检查
    if not (crawler_dir / "main.py").exists():
        raise SystemExit(f"未找到爬虫入口：{crawler_dir / 'main.py'}")
    if not webgen_py.exists():
        raise SystemExit(f"未找到转网页脚本：{webgen_py}")

    csv_dir.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)

    print(f"当前解释器：{PY}")

    t0 = time.time()

    # 1) 逐源运行爬虫（不改爬虫代码，只传参）
    for src in args.sources:
        out_csv = csv_dir / f"{src}_news.csv"
        parts = [
            PY, "main.py",
            "--source", src,
            "--limit", str(args.limit),
            "--since-days", str(args.since_days),
            "--out", str(out_csv),
        ]
        if args.keyword:
            parts += ["--keyword", args.keyword]
        run(parts, cwd=crawler_dir)

    # 2) 选择要转换的 CSV
    if args.convert_all:
        targets = sorted(csv_dir.rglob("*.csv"))
        if not targets:
            raise SystemExit("data/ 目录下没有任何 CSV")
        print(f"✅ 转换全部 CSV：共 {len(targets)} 个")
    else:
        targets = list_new_csv(csv_dir, since=t0)
        if not targets:
            # 若本次没新增/更新，则退回全量提示（不强制）
            print("ℹ 未检测到新 CSV，本次将不执行转换。若需要全量转换，请添加 --convert-all")
            return
        print(f"✅ 转换本次新增/更新的 CSV：共 {len(targets)} 个")

    # 3) 调网页脚本逐一生成 HTML（并在浏览器打开）
    for p in targets:
        out_html = html_dir / (p.stem + ".html")
        cmd = [PY, str(webgen_py), str(p), str(out_html), "--browser", args.browser]
        run(cmd)

    print("🎉 全部完成")

if __name__ == "__main__":
    main()
