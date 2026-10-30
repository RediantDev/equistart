import datetime

from django import template

register = template.Library()


@register.filter
def rub(kopecks):
    """12345678 → «123 456,78 ₽»; 250000 → «2 500 ₽»; 0 → «Бесплатно»."""
    if kopecks is None or kopecks == "":
        return ""
    kopecks = int(kopecks)
    if kopecks == 0:
        return "Бесплатно"
    rub_part, kop = divmod(kopecks, 100)
    s = f"{rub_part:,}".replace(",", " ")
    if kop:
        s += f",{kop:02d}"
    return f"{s} ₽"


@register.filter
def get(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None


MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


@register.filter
def isodate(value, with_year=True):
    """«2026-11-21» → «21 ноября 2026»."""
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.date.fromisoformat(value)
        except ValueError:
            return value
    out = f"{value.day} {MONTHS_GEN[value.month - 1]}"
    return f"{out} {value.year}" if with_year else out


@register.simple_tag
def daterange(start, end):
    """«20–21 ноября 2026», «30 ноября — 1 декабря 2026», «20 ноября 2026»."""
    if isinstance(start, str):
        start = datetime.date.fromisoformat(start)
    if isinstance(end, str) and end:
        end = datetime.date.fromisoformat(end)
    if not end or end == start:
        return isodate(start)
    if start.year == end.year and start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS_GEN[end.month - 1]} {end.year}"
    if start.year == end.year:
        return f"{start.day} {MONTHS_GEN[start.month - 1]} — {end.day} {MONTHS_GEN[end.month - 1]} {end.year}"
    return f"{isodate(start)} — {isodate(end)}"


@register.filter
def phone(value):
    from apps.accounts.phone import format_phone, normalize_phone

    if not value:
        return ""
    try:
        return format_phone(normalize_phone(str(value)))
    except Exception:
        return value


@register.filter
def num(v):
    """Числа результатов: 4.0 → «4», 61.25 → «61,25». Пусто → «—»."""
    if v is None or v == "":
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if f == int(f):
        return str(int(f))
    return f"{f:.2f}".rstrip("0").replace(".", ",")


@register.filter
def money(kopecks):
    """Суммы учёта: 0 → «0 ₽», отрицательные со знаком «−»."""
    if kopecks is None or kopecks == "":
        return ""
    k = int(kopecks)
    r, kop = divmod(abs(k), 100)
    s = f"{r:,}".replace(",", " ")
    if kop:
        s += f",{kop:02d}"
    return ("−" if k < 0 else "") + s + " ₽"


@register.filter
def rub_input(kopecks):
    """Копейки → значение для поля ввода: 150050 → «1500,50»."""
    if kopecks is None or kopecks == "":
        return ""
    r, kop = divmod(abs(int(kopecks)), 100)
    return f"{r},{kop:02d}" if kop else str(r)


@register.filter
def split(value, sep=","):
    """«0,1,2» → ["0", "1", "2"] — для кнопок в шаблоне."""
    return str(value).split(sep)


@register.filter
def heights_extra(p):
    """Подпись высоты для второй строки — только если её ещё нет в названии (тест 4: не дублировать высоту)."""
    get_ = (lambda k: p.get(k)) if isinstance(p, dict) else (lambda k: getattr(p, k, None))
    label, name = get_("heights_label") or "", get_("name") or ""
    if not label:
        return ""
    digits = label.replace(" см", "").replace(", ", "-").replace(",", "-")
    return "" if digits in name or label in name else label


@register.filter
def ph(bound):
    """Поле формы с подсказкой внутри: «Обязательно» / «Необязательно», если своей подсказки нет (тест 4.2, п. 19)."""
    w = bound.field.widget
    attrs = {}
    input_type = getattr(w, "input_type", None)
    if input_type not in ("checkbox", "radio", "file", "hidden") and not getattr(w, "allow_multiple_selected", False) \
            and not w.attrs.get("placeholder") and w.__class__.__name__ not in ("Select", "RadioSelect", "NullBooleanSelect"):
        required = bound.field.required or getattr(bound.field, "show_required", False)
        attrs["placeholder"] = "Обязательно" if required else "Необязательно"
    return bound.as_widget(attrs=attrs) if attrs else bound


@register.filter
def card(t):
    """Данные карточки турнира (дата-плитка, статус приёма, дисциплины) — как в календаре."""
    from apps.tournaments.views import _card

    return _card(t)


@register.filter
def tpl_years(tpl, year):
    """Тест 6, п. 16: подпись под чипом готового зачёта — годы рождения для года турнира."""
    try:
        y = int(year)
    except (TypeError, ValueError):
        return ""
    d = tpl.for_year(y)
    a, b = d.get("birth_year_from"), d.get("birth_year_to")
    if a and b:
        return f"{a}–{b} г.р."
    if a:
        return f"{a} г.р. и старше"
    if b:
        return f"{b} г.р. и моложе"
    return "без ограничений по возрасту"


@register.filter
def plural(n, forms):
    """Русское окончание: {{ n|plural:"результат,результата,результатов" }}."""
    one, few, many = (forms.split(",") + ["", "", ""])[:3]
    try:
        n = abs(int(n))
    except (TypeError, ValueError):
        return many
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


@register.filter
def at(seq, i):
    """Элемент списка по номеру: {{ columns|at:forloop.counter0 }}."""
    try:
        return seq[int(i)]
    except (TypeError, ValueError, IndexError, KeyError):
        return ""
