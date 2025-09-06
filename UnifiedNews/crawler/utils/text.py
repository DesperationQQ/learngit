from datetime import datetime

def normalize_time(dt):
    """dt 可能是 datetime 或字符串，这里做一个最简单兜底"""
    if isinstance(dt, datetime):
        return dt.isoformat(sep=" ", timespec="seconds")
    return str(dt)
