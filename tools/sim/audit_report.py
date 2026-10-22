import json, re, sys
from collections import defaultdict
R = json.load(open("/home/claude/sim/audit/results.json"))
pat = lambda u: re.sub(r"\d+", "{n}", u)
by = defaultdict(lambda: defaultdict(set))
for r in R:
    k = pat(r["url"])
    w = r["width"]
    if r.get("status", 200) and r.get("status", 200) >= 400:
        by["status"][k].add(f'{r["role"]}: {r["status"]}')
    if r.get("error"):
        by["error"][k].add(r["error"][:100])
    if r.get("docW", 0) > r.get("W", 0) + 1:
        by["page-scroll"][k].add(f'{w}: {r["docW"]} {r["overflow"][:3]}')
    elif r.get("overflow"):
        by["overflow"][k].add(f'{w}: {r["overflow"][:3]}')
    for key in ("smallTap", "zoomInputs", "tinyText", "clippedText", "brokenImgs", "console"):
        for x in r.get(key, []) or []:
            by[key][k].add(f"{w}: {x}")
    if r.get("scrollers") and w < 500:
        by["inner-scroll"][k].add(f'{w}: {r["scrollers"][:3]}')
only = sys.argv[1:]
for cat, d in by.items():
    if only and cat not in only:
        continue
    print(f"\n=== {cat} ({len(d)} экранов)")
    for k, v in sorted(d.items()):
        v = sorted(v)
        print(" ", k, "|", "; ".join(v[:6])[:600])
