# -*- coding: utf-8 -*-
"""
把 CSV 转成可读网页并自动在浏览器打开（避免花括号冲突，使用 string.Template）
用法：
  python csv_to_readable_html.py stcn_news.csv output.html
可选：
  --browser edge|chrome|firefox   # 强制用某个浏览器打开
  --no-open                       # 仅生成文件，不自动打开
  --url_col/--title_col/--date_col/--content_col/--source_col  # 自定义列名
"""
import csv, sys, argparse, html, re, webbrowser, os, platform, subprocess
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Optional

# ---------- 浏览器打开（优先 Edge/Chrome/Firefox；可 --browser 指定） ----------
def _candidate_paths():
    pf   = Path(os.environ.get("PROGRAMFILES"      , r"C:\Program Files"))
    pfx  = Path(os.environ.get("PROGRAMFILES(X86)" , r"C:\Program Files (x86)"))
    lapp = Path(os.environ.get("LOCALAPPDATA"      , Path.home()/"AppData/Local"))
    return {
        "edge":   [pf/"Microsoft/Edge/Application/msedge.exe", pfx/"Microsoft/Edge/Application/msedge.exe"],
        "chrome": [pf/"Google/Chrome/Application/chrome.exe", pfx/"Google/Chrome/Application/chrome.exe", lapp/"Google/Chrome/Application/chrome.exe"],
        "firefox":[pf/"Mozilla Firefox/firefox.exe", pfx/"Mozilla Firefox/firefox.exe"],
    }

def _find_browser(exe_key: str) -> Optional[str]:
    for p in _candidate_paths().get(exe_key, []):
        if p.exists():
            return str(p)
    return None

def open_in_browser(html_path: Path, prefer: Optional[str] = None):
    html_path = html_path.resolve()
    uri = html_path.as_uri()
    # 1) 指定浏览器
    if prefer:
        exe = _find_browser(prefer)
        if exe:
            try:
                subprocess.Popen([exe, str(html_path)])
                return
            except Exception:
                pass
    # 2) 自动优先 Edge -> Chrome -> Firefox
    for key in ("edge", "chrome", "firefox"):
        exe = _find_browser(key)
        if exe:
            try:
                subprocess.Popen([exe, str(html_path)])
                return
            except Exception:
                continue
    # 3) webbrowser（可能受默认程序影响）
    try:
        if platform.system() == "Windows":
            webbrowser.get("windows-default").open(uri, new=2)
            return
    except Exception:
        pass
    try:
        if webbrowser.open_new_tab(uri):
            return
    except Exception:
        pass
    # 4) 兜底
    try:
        if platform.system() == "Windows":
            os.startfile(str(html_path))  # type: ignore[attr-defined]
        else:
            webbrowser.open(uri)
    except Exception:
        print("⚠️ 未能自动打开浏览器，请手动打开：", str(html_path))

# ---------- 文本处理 ----------
def try_parse_date(s: str) -> str:
    if not s: return ""
    s = s.strip()
    fmts = ["%Y-%m-%d","%Y-%m-%d %H:%M","%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d","%Y/%m/%d %H:%M","%Y/%m/%d %H:%M:%S",
            "%Y.%m.%d","%Y.%m.%d %H:%M","%Y.%m.%d %H:%M:%S"]
    for f in fmts:
        try:
            dt = datetime.strptime(s, f)
            return dt.strftime("%Y-%m-%d %H:%M") if "H" in f else dt.strftime("%Y-%m-%d")
        except: pass
    return s

def mk_excerpt(text: str, limit: int = 280) -> str:
    if not text: return ""
    if len(text) <= limit: return text
    cut = text[:limit]
    m = re.search(r"[，。；、,.!?\s]\S*$", cut)
    if m: cut = cut[:m.start()]
    return cut.rstrip() + "…"

def format_content_to_html(raw: str) -> str:
    if not raw: return ""
    safe = html.escape(raw, quote=False)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", safe) if p.strip()] or [safe.strip()]
    return "".join(f"<p>{p.replace('\n','<br>')}</p>" for p in paragraphs)

def build_article_card(idx, title, url, date_str, source, content_html, excerpt_html, search_blob):
    aid = f"a{idx}"
    title_display = title or "(无标题)"
    date_badge = f'<span class="badge">{html.escape(date_str)}</span>' if date_str else ""
    source_badge = f'<span class="badge badge-muted">{html.escape(source)}</span>' if source else ""
    link_btn = f'<a class="btn btn-link" href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">阅读原文</a>' if url else ""
    return f"""
    <article id="{aid}" class="article" data-search="{html.escape(search_blob.lower())}">
      <header class="article-header">
        <h2 class="article-title">{html.escape(title_display)}</h2>
        <div class="meta">{date_badge}{source_badge}{link_btn}</div>
      </header>
      <div class="content">
        <div class="excerpt">{excerpt_html}</div>
        <div class="full hidden">{content_html}</div>
        <button class="btn btn-toggle" aria-expanded="false" onclick="toggleFull(this)">展开全文</button>
      </div>
    </article>
    """

# ---------- HTML 模板（用 string.Template 的 $占位符） ----------
HTML_TMPL = Template(r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>$page_title</title>
<style>
  :root {
    --bg: #0b0c0f;
    --fg: #e7e9ee;
    --muted: #a1a8b3;
    --card: #151821;
    --card-border: #232735;
    --accent: #3b82f6;
    --badge: #1f2937;
    --shadow: rgba(0,0,0,.25);
  }
  @media (prefers-color-scheme: light) {
    :root {
      --bg: #f8fafc;
      --fg: #0b1220;
      --muted: #516175;
      --card: #ffffff;
      --card-border: #e5e7eb;
      --badge: #eef2f7;
      --shadow: rgba(0,0,0,.08);
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC","Hiragino Sans GB", "Microsoft YaHei","Helvetica Neue", Arial;
  }
  header.top {
    position: sticky; top: 0; z-index: 10; backdrop-filter: saturate(1.2) blur(6px);
    background: color-mix(in oklab, var(--bg) 88%, transparent);
    border-bottom: 1px solid var(--card-border);
  }
  .container { max-width: 960px; margin: 0 auto; padding: 16px; }
  .title-row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  h1 { font-size: 22px; margin: 0; }
  .search { flex: 1 1 280px; position: relative; }
  .search input {
    width: 100%; padding: 10px 38px 10px 12px; border-radius: 12px; border: 1px solid var(--card-border);
    background: var(--card); color: var(--fg); outline: none;
  }
  .search .hint { position: absolute; right: 10px; top: 50%; transform: translateY(-50%); color: var(--muted); font-size: 12px; }
  .stat { color: var(--muted); font-size: 14px; }
  .article {
    border: 1px solid var(--card-border); background: var(--card); border-radius: 16px;
    padding: 16px; margin: 16px 0; box-shadow: 0 10px 24px var(--shadow);
  }
  .article-title { margin: 0 0 8px 0; font-size: 20px; }
  .meta { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
  .badge {
    display: inline-block; padding: 4px 8px; border-radius: 999px; background: var(--badge); color: var(--muted); font-size: 12px;
  }
  .badge-muted { opacity: 0.9; }
  .btn {
    display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--card-border);
    background: transparent; color: var(--fg); padding: 8px 12px; border-radius: 10px; cursor: pointer;
  }
  .btn:hover { border-color: var(--accent); }
  .btn-link {
    text-decoration: none; border-color: transparent; color: var(--accent); font-weight: 600; padding: 0;
  }
  .content p { margin: 0 0 10px 0; }
  .excerpt { color: var(--fg); }
  .full.hidden { display: none; }
  footer.page-end { color: var(--muted); text-align: center; padding: 32px 0 48px; }
</style>
</head>
<body>
<header class="top">
  <div class="container">
    <div class="title-row">
      <h1>$page_title</h1>
      <div class="search">
        <input id="q" type="search" placeholder="搜索标题 / 正文 / 来源…" aria-label="搜索" />
        <span class="hint">Ctrl/⌘ + F 也可</span>
      </div>
      <div class="stat"><span id="shownCount">$shown</span>/<span id="totalCount">$total</span> 篇</div>
    </div>
  </div>
</header>

<main class="container" id="list">
$articles
</main>

<footer class="page-end">
  由 CSV 自动生成 • 本页可离线打开
</footer>

<script>
  const list = document.getElementById('list');
  const q = document.getElementById('q');
  const shownCount = document.getElementById('shownCount');

  function toggleFull(btn) {
    const wrap = btn.closest('.content');
    const full = wrap.querySelector('.full');
    const excerpt = wrap.querySelector('.excerpt');
    const expanded = btn.getAttribute('aria-expanded') === 'true';
    if (expanded) {
      full.classList.add('hidden');
      excerpt.classList.remove('hidden');
      btn.textContent = '展开全文';
      btn.setAttribute('aria-expanded', 'false');
    } else {
      full.classList.remove('hidden');
      excerpt.classList.add('hidden');
      btn.textContent = '收起';
      btn.setAttribute('aria-expanded', 'true');
    }
  }

  function updateFilter() {
    const kw = q.value.trim().toLowerCase();
    let shown = 0;
    for (const card of list.querySelectorAll('.article')) {
      const hay = card.dataset.search || '';
      const hit = !kw || hay.includes(kw);
      card.style.display = hit ? '' : 'none';
      if (hit) shown++;
    }
    shownCount.textContent = shown;
  }

  q.addEventListener('input', updateFilter);
  updateFilter();
</script>
</body>
</html>
""")

# ---------- 生成页面 ----------
def build_articles(rows):
    cards=[]
    for i,r in enumerate(rows,1):
        title=r["title"]; url=r["url"]; date_str=try_parse_date(r["date"]); src=r["source"]; txt=r["content"]
        content_html = format_content_to_html(txt)
        excerpt_html = format_content_to_html(mk_excerpt(txt,280))
        search_blob = " ".join(x for x in [title,txt,src,date_str] if x)
        cards.append(build_article_card(i,title,url,date_str,src,content_html,excerpt_html,search_blob))
    return "\n".join(cards)

def run(input_csv: Path, output_html: Path, colmap, open_browser=True, prefer=None):
    rows=[]
    with input_csv.open("r", encoding="utf-8-sig", newline="") as f:
        reader=csv.DictReader(f)
        norm={(fn or "").strip().lower():fn for fn in (reader.fieldnames or [])}
        def pick(row,want):
            als={want, want.replace("_"," "), want.replace(" ","_")}
            for low,real in norm.items():
                if low in als: return (row.get(real) or "").strip()
            return (row.get(want) or "").strip()
        for raw in reader:
            rows.append({
                "url":     (raw.get(colmap["url"])     or pick(raw,"url")).strip(),
                "title":   (raw.get(colmap["title"])   or pick(raw,"title")).strip(),
                "date":    (raw.get(colmap["date"])    or pick(raw,"pub_dt")).strip(),
                "content": (raw.get(colmap["content"]) or pick(raw,"content")).strip(),
                "source":  (raw.get(colmap["source"])  or pick(raw,"source")).strip(),
            })
    html_text = HTML_TMPL.substitute(
        page_title=f"文章合集（{len(rows)}）",
        articles=build_articles(rows),
        total=len(rows),
        shown=len(rows),
    )
    output_html.write_text(html_text, encoding="utf-8")
    print("✅ 已生成：", output_html.resolve())
    if open_browser:
        open_in_browser(output_html, prefer)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("input_csv", type=Path)
    ap.add_argument("output_html", type=Path)
    ap.add_argument("--url_col", default="url")
    ap.add_argument("--title_col", default="title")
    ap.add_argument("--date_col", default="pub_dt")
    ap.add_argument("--content_col", default="content")
    ap.add_argument("--source_col", default="source")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--browser", choices=["edge","chrome","firefox"], help="强制使用的浏览器")
    args=ap.parse_args()
    colmap={"url":args.url_col,"title":args.title_col,"date":args.date_col,"content":args.content_col,"source":args.source_col}
    run(args.input_csv, args.output_html, colmap, open_browser=not args.no_open, prefer=args.browser)

if __name__=="__main__":
    if len(sys.argv)==1: print(__doc__); sys.exit(0)
    main()
