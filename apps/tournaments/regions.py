"""Субъекты Российской Федерации и их часовые пояса.

Часовой пояс турнира определяется автоматически по региону места проведения.
Единственный субъект с несколькими поясами — Республика Саха (Якутия):
для него пояс выбирается по району.
Ключи — внутренние идентификаторы сервиса, не официальные коды.
"""

REGIONS = [
    # (ключ, название, часовой пояс)
    ("ad", "Республика Адыгея", "Europe/Moscow"),
    ("al", "Республика Алтай", "Asia/Barnaul"),
    ("ba", "Республика Башкортостан", "Asia/Yekaterinburg"),
    ("bu", "Республика Бурятия", "Asia/Irkutsk"),
    ("da", "Республика Дагестан", "Europe/Moscow"),
    ("dnr", "Донецкая Народная Республика", "Europe/Moscow"),
    ("in", "Республика Ингушетия", "Europe/Moscow"),
    ("kb", "Кабардино-Балкарская Республика", "Europe/Moscow"),
    ("kl", "Республика Калмыкия", "Europe/Moscow"),
    ("kc", "Карачаево-Черкесская Республика", "Europe/Moscow"),
    ("kr", "Республика Карелия", "Europe/Moscow"),
    ("ko", "Республика Коми", "Europe/Moscow"),
    ("crimea", "Республика Крым", "Europe/Simferopol"),
    ("lnr", "Луганская Народная Республика", "Europe/Moscow"),
    ("me", "Республика Марий Эл", "Europe/Moscow"),
    ("mo", "Республика Мордовия", "Europe/Moscow"),
    ("sa", "Республика Саха (Якутия)", "Asia/Yakutsk"),
    ("se", "Республика Северная Осетия — Алания", "Europe/Moscow"),
    ("ta", "Республика Татарстан", "Europe/Moscow"),
    ("ty", "Республика Тыва", "Asia/Krasnoyarsk"),
    ("ud", "Удмуртская Республика", "Europe/Samara"),
    ("kk", "Республика Хакасия", "Asia/Krasnoyarsk"),
    ("ce", "Чеченская Республика", "Europe/Moscow"),
    ("cu", "Чувашская Республика", "Europe/Moscow"),
    ("alt", "Алтайский край", "Asia/Barnaul"),
    ("zab", "Забайкальский край", "Asia/Chita"),
    ("kam", "Камчатский край", "Asia/Kamchatka"),
    ("kda", "Краснодарский край", "Europe/Moscow"),
    ("kya", "Красноярский край", "Asia/Krasnoyarsk"),
    ("per", "Пермский край", "Asia/Yekaterinburg"),
    ("pri", "Приморский край", "Asia/Vladivostok"),
    ("sta", "Ставропольский край", "Europe/Moscow"),
    ("kha", "Хабаровский край", "Asia/Vladivostok"),
    ("amu", "Амурская область", "Asia/Yakutsk"),
    ("ark", "Архангельская область", "Europe/Moscow"),
    ("ast", "Астраханская область", "Europe/Astrakhan"),
    ("bel", "Белгородская область", "Europe/Moscow"),
    ("bry", "Брянская область", "Europe/Moscow"),
    ("vla", "Владимирская область", "Europe/Moscow"),
    ("vgg", "Волгоградская область", "Europe/Volgograd"),
    ("vlg", "Вологодская область", "Europe/Moscow"),
    ("vor", "Воронежская область", "Europe/Moscow"),
    ("zap", "Запорожская область", "Europe/Moscow"),
    ("iva", "Ивановская область", "Europe/Moscow"),
    ("irk", "Иркутская область", "Asia/Irkutsk"),
    ("kgd", "Калининградская область", "Europe/Kaliningrad"),
    ("klu", "Калужская область", "Europe/Moscow"),
    ("kem", "Кемеровская область — Кузбасс", "Asia/Novokuznetsk"),
    ("kir", "Кировская область", "Europe/Kirov"),
    ("kos", "Костромская область", "Europe/Moscow"),
    ("kgn", "Курганская область", "Asia/Yekaterinburg"),
    ("krs", "Курская область", "Europe/Moscow"),
    ("len", "Ленинградская область", "Europe/Moscow"),
    ("lip", "Липецкая область", "Europe/Moscow"),
    ("mag", "Магаданская область", "Asia/Magadan"),
    ("mos", "Московская область", "Europe/Moscow"),
    ("mur", "Мурманская область", "Europe/Moscow"),
    ("niz", "Нижегородская область", "Europe/Moscow"),
    ("ngr", "Новгородская область", "Europe/Moscow"),
    ("nvs", "Новосибирская область", "Asia/Novosibirsk"),
    ("oms", "Омская область", "Asia/Omsk"),
    ("ore", "Оренбургская область", "Asia/Yekaterinburg"),
    ("orl", "Орловская область", "Europe/Moscow"),
    ("pnz", "Пензенская область", "Europe/Moscow"),
    ("psk", "Псковская область", "Europe/Moscow"),
    ("ros", "Ростовская область", "Europe/Moscow"),
    ("rya", "Рязанская область", "Europe/Moscow"),
    ("sam", "Самарская область", "Europe/Samara"),
    ("sar", "Саратовская область", "Europe/Saratov"),
    ("sak", "Сахалинская область", "Asia/Sakhalin"),
    ("sve", "Свердловская область", "Asia/Yekaterinburg"),
    ("smo", "Смоленская область", "Europe/Moscow"),
    ("tam", "Тамбовская область", "Europe/Moscow"),
    ("tve", "Тверская область", "Europe/Moscow"),
    ("tom", "Томская область", "Asia/Tomsk"),
    ("tul", "Тульская область", "Europe/Moscow"),
    ("tyu", "Тюменская область", "Asia/Yekaterinburg"),
    ("uly", "Ульяновская область", "Europe/Ulyanovsk"),
    ("khe", "Херсонская область", "Europe/Moscow"),
    ("che", "Челябинская область", "Asia/Yekaterinburg"),
    ("yar", "Ярославская область", "Europe/Moscow"),
    ("mow", "Москва", "Europe/Moscow"),
    ("spe", "Санкт-Петербург", "Europe/Moscow"),
    ("sev", "Севастополь", "Europe/Simferopol"),
    ("yev", "Еврейская автономная область", "Asia/Vladivostok"),
    ("nen", "Ненецкий автономный округ", "Europe/Moscow"),
    ("khm", "Ханты-Мансийский автономный округ — Югра", "Asia/Yekaterinburg"),
    ("chu", "Чукотский автономный округ", "Asia/Anadyr"),
    ("yan", "Ямало-Ненецкий автономный округ", "Asia/Yekaterinburg"),
]

REGION_NAMES = {key: name for key, name, _ in REGIONS}
REGION_TZ = {key: tz for key, _, tz in REGIONS}
REGION_CHOICES = sorted(((key, name) for key, name, _ in REGIONS), key=lambda x: x[1])

# Регион с несколькими часовыми поясами: пояс выбирается по району.
MULTI_ZONE = {
    "sa": [
        ("Asia/Yakutsk", "UTC+9 — Якутск и большая часть республики"),
        ("Asia/Ust-Nera", "UTC+10 — Верхоянский, Оймяконский, Усть-Янский районы"),
        ("Asia/Srednekolymsk", "UTC+11 — Абыйский, Аллаиховский, Верхнеколымский, Момский, Нижнеколымский, Среднеколымский районы"),
    ],
}

TZ_LABELS = {
    "Europe/Kaliningrad": "калининградское время (UTC+2)",
    "Europe/Moscow": "московское время",
    "Europe/Simferopol": "московское время",
    "Europe/Volgograd": "московское время",
    "Europe/Kirov": "московское время",
}


def region_timezone(region: str, chosen: str | None = None) -> str:
    """Часовой пояс места: по региону, а для многозонного региона — выбранный из допустимых."""
    if region in MULTI_ZONE:
        allowed = {tz for tz, _ in MULTI_ZONE[region]}
        if chosen in allowed:
            return chosen
        raise ValueError("Для этого региона нужно выбрать часовой пояс")
    return REGION_TZ[region]


def tz_label(tz: str) -> str:
    """Подпись пояса для людей: «московское время» или «время UTC+5»."""
    if not tz:
        return ""
    if tz in TZ_LABELS:
        return TZ_LABELS[tz]
    import datetime
    import zoneinfo

    offset = datetime.datetime.now(zoneinfo.ZoneInfo(tz)).utcoffset()
    hours = int(offset.total_seconds() // 3600)
    return f"местное время (UTC{hours:+d})"
