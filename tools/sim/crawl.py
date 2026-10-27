"""Находимость: кратчайший путь по видимым ссылкам от «Кабинета» до целевого экрана (только GET, без форм)."""
import re, sys, json
from collections import deque
from urllib.parse import urlparse
sys.path.insert(0, "/home/claude/sim")
from browser import Browser, BASE

SKIP = re.compile(r"(logout|\.xlsx|\.pdf|/manage/|/static/|/media/|#|privacy|consent|regulation|/health/)")


def crawl(b, start="/cabinet/", max_depth=4, max_pages=400):
    seen = {start: (0, [start], "")}
    dq = deque([start])
    while dq and len(seen) < max_pages:
        url = dq.popleft()
        d, path, _ = seen[url]
        if d >= max_depth:
            continue
        p = b.get(url, note="crawl")
        if p.status != 200 or not p.soup:
            continue
        for text, href in p.links():
            u = urlparse(href)
            if u.netloc and u.netloc not in BASE:
                continue
            key = u.path + (("?" + u.query) if u.query and "tab=" in u.query else "")
            if SKIP.search(href) or key in seen:
                continue
            seen[key] = (d + 1, path + [f"«{text[:40]}»"], text)
            dq.append(key)
    return seen


def find(seen, pattern):
    best = None
    for k, (d, path, text) in seen.items():
        if re.search(pattern, k) and (best is None or d < best[0]):
            best = (d, path, k)
    return best
