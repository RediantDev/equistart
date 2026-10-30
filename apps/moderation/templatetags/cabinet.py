from django import template
from django.urls import reverse

register = template.Library()

# (раздел, подпись, имя url, только администратору, имена url, при которых раздел активен)
SECTIONS = [
    ("panel", "Пульт", "moderation:panel", False, {"panel"}),
    ("queue", "Турниры на проверке", "moderation:queue", False, {"queue", "detail"}),
    ("clubs", "Клубы", "moderation:clubs", False, {"clubs", "club"}),
    ("support", "Обращения", "moderation:support", False, {"support", "support_detail"}),
    ("users", "Пользователи", "moderation:users", False, {"users", "user"}),
    ("duplicates", "Дубли", "moderation:duplicates", False, {"duplicates"}),
    ("tournaments", "Все турниры", "moderation:tournaments", False, {"tournaments"}),
    ("journal", "Журнал", "moderation:journal", True, {"journal"}),
    ("settings", "Настройки", "moderation:settings", True, {"settings"}),
    ("system", "Система", "moderation:system", True, {"system"}),
    ("moderators", "Модераторы", "moderation:moderators", True, {"moderators"}),
]


def _counts():
    from apps.clubs.models import Club
    from apps.support.models import Ticket
    from apps.tournaments.models import Tournament

    open_tickets = Ticket.objects.exclude(status__in=[Ticket.Status.RESOLVED, Ticket.Status.WAITING])
    return {
        "queue": (Tournament.objects.filter(status=Tournament.Status.REVIEW).count(), False),
        "clubs": (Club.objects.filter(status=Club.Status.REVIEW, merged_into__isnull=True).count(), False),
        "support": (open_tickets.count(), open_tickets.filter(urgent=True).exists()),
    }


@register.inclusion_tag("moderation/_nav.html", takes_context=True)
def cabinet_nav(context):
    request = context["request"]
    user = request.user
    current = getattr(request.resolver_match, "url_name", "")
    counts = _counts()
    items = []
    for key, label, url, admin_only, active in SECTIONS:
        if admin_only and not user.is_admin:
            continue
        n, urgent = counts.get(key, (0, False))
        items.append({"key": key, "label": label, "url": reverse(url), "active": current in active, "count": n, "urgent": urgent})
    return {"items": items, "user": user, "admin_url": reverse("admin:index") if user.is_admin else ""}


TARGET_URLS = {"tournament": "moderation:detail", "user": "moderation:user", "club": "moderation:club", "ticket": "moderation:support_detail"}
TARGET_LABELS = {"tournament": "Турнир", "user": "Пользователь", "club": "Клуб", "ticket": "Обращение", "athlete": "Спортсмен",
                 "horse": "Лошадь", "application": "Заявка", "entry": "Выступление", "service": "Сервис", "sitesettings": "Настройки"}


@register.filter
def audit_target(row):
    label = TARGET_LABELS.get(row.target_type, row.target_type)
    if row.target_type in ("service", "sitesettings"):
        return {"label": label, "url": ""}
    url = ""
    if row.target_type in TARGET_URLS:
        try:
            url = reverse(TARGET_URLS[row.target_type], args=[row.target_id])
        except Exception:
            url = ""
    return {"label": f"{label} №{row.target_id}", "url": url}


DATA_LABELS = {"reason": "причина", "reason_was": "прежняя причина", "changed": "изменено", "moved": "перенесено", "dup": "дубль №",
               "into": "объединён с №", "status_from": "статус был", "status_to": "статус стал", "topic": "тема", "urgent": "срочное",
               "rows": "строк", "tournament": "турнир №", "recipients": "получателей", "comment": "замечание"}


@register.filter
def audit_data(data):
    """Сведения записи журнала в одну строку, без служебных полей."""
    if not isinstance(data, dict):
        return ""
    parts = []
    for k, v in data.items():
        if k == "as_admin":
            continue
        if isinstance(v, (list, tuple)) and len(v) == 2 and k not in ("dup",):
            v = f"{v[0]} → {v[1]}"
        elif isinstance(v, dict):
            v = ", ".join(f"{kk}: {vv}" for kk, vv in v.items())
        if v is True:
            v = "да"
        elif v is False:
            v = "нет"
        parts.append(f"{DATA_LABELS.get(k, k)}: {v}")
    return "; ".join(parts)[:400]
