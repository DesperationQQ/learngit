import httpx

def client():
    # 统一的 HTTP 客户端
    return httpx.Client(
        timeout=10,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AStockCrawler/0.1"
        },
        follow_redirects=True,
        trust_env=False,   # 先禁用系统代理，抓国内站点更稳；以后若要走代理再开
    )
