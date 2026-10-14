"""Собирает static/css/tokens.css из design/tokens.json (единый источник значений).

Запуск: python design/build_tokens.py
Тот же формат использует дизайн-система EquiStart в Claude, поэтому файл совместим с ней.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "design" / "tokens.json"
OUT = ROOT / "static" / "css" / "tokens.css"


def build(tokens: dict, font_url_prefix: str = "../fonts/") -> str:
    lines = ["/* EquiStart — generated from tokens.json */", ""]
    for font in tokens["type"].get("fonts", []):
        name = font["file"].split("/")[-1]
        fmt = name.rsplit(".", 1)[-1]
        lines += [
            "@font-face {",
            f'  font-family: "{font["family"]}";',
            f'  src: url("{font_url_prefix}{name}") format("{fmt}");',
            f'  font-weight: {font.get("weight", "400")};',
            f'  font-style: {font.get("style", "normal")};',
            "  font-display: swap;",
            "}",
            "",
        ]
    first = tokens["color"]["themes"][0]["id"]
    lines.append(f':root, [data-theme="{first}"] {{')
    for t in tokens["color"]["tokens"]:
        v = t["value"][first] if isinstance(t["value"], dict) else t["value"]
        lines.append(f"  --{t['name']}: {v};")
    for t in tokens.get("shadow", {}).get("tokens", []):
        lines.append(f"  --{t['name']}: {t['value']};")
    lines += ["}", "", ":root {"]
    for family in ("spacing", "radius", "layout"):
        for t in tokens.get(family, {}).get("tokens", []):
            lines.append(f"  --{t['name']}: {t['value']};")
    for key, stack in tokens["type"]["families"].items():
        lines.append(f"  --font-{key}: {stack};")
    lines += ["}", ""]
    for group in tokens["type"]["groups"]:
        for s in group["styles"]:
            fam = s.get("family", group["family"])
            lines.append(
                f".{s['name']} {{ font-family: var(--font-{fam}); font-size: {s['fontSize']}; "
                f"line-height: {s['lineHeight']}; font-weight: {s['fontWeight']};"
                + (f" letter-spacing: {s['letterSpacing']};" if "letterSpacing" in s else "")
                + " }"
            )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    OUT.write_text(build(json.loads(SRC.read_text(encoding="utf-8"))), encoding="utf-8")
    print(f"written {OUT.relative_to(ROOT)}")
