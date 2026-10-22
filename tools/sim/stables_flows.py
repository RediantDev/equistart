"""Денники и услуги: конюшня настраивает категории и услуги, представители бронируют и заказывают, конюшня подтверждает и выдаёт."""
import re, threading, datetime, uuid
from browser import Browser, friction
from people import R


def find_manage(sb, pk):
    """Конюшня находит управление денниками как человек: кабинет → мои соревнования → турнир → «Денники и услуги»."""
    sb.get("/cabinet/")
    if not sb.click("Мои соревнования"):
        sb.friction("Кабинет конюшни", "Нет ссылки на турниры, где я в команде", "high")
    href = None
    for t, h in sb.page.links():
        if f"/t/{pk}/" in h or f"/organizer/t/{pk}/" in h:
            href = h; break
    if href:
        sb.get(href)
    if not sb.click("Денники и услуги", "Денники"):
        sb.friction("Конюшня", "Не нашёл вход в управление денниками за 3 клика", "high")
        sb.get(f"/organizer/t/{pk}/stables/")
    return sb.page


def setup(sb, pk, first_day, last_day):
    find_manage(sb, pk)
    d0 = (datetime.date.fromisoformat(first_day) - datetime.timedelta(days=1)).isoformat()
    d1 = (datetime.date.fromisoformat(last_day) + datetime.timedelta(days=1)).isoformat()
    cats = [("Летник (временная конюшня)", 40, "Подстилка — опилки, поилка автоматическая."),
            ("Капитальная конюшня", 60, "Денник 3×3,5 м, включена первая подстилка."),
            ("Денник для жеребцов", 5, "Отдельный блок, только жеребцы.")]
    for name, cap, cond in cats:
        sb.get(f"/organizer/t/{pk}/stables/category/new/")
        f = sb.page.form(has_field="capacity")
        sb.submit(f, {"name": name, "available_from": d0, "available_to": d1, "capacity": str(cap), "checkin_time": "12:00",
                      "checkout_time": "18:00", "conditions": cond, "is_active": "on"}, drop=("is_active",))
        if sb.page.errors():
            sb.friction("Категория денников", f"Не сохранилась: {sb.page.errors()[:2]}", "high")
    svcs = [("Опилки", "тюк", "horse", "300"), ("Сено", "тюк", "horse", ""), ("Электричество для коневоза", "сутки", "trailer", "20")]
    for name, unit, rec, limit in svcs:
        sb.get(f"/organizer/t/{pk}/stables/service/new/")
        f = sb.page.form(has_field="unit")
        sb.submit(f, {"name": name, "unit": unit, "recipient": rec, "available_from": d0, "available_to": d1, "limit": limit, "is_active": "on"},
                  drop=("is_active",))
        if sb.page.errors():
            sb.friction("Услуга", f"Не сохранилась: {sb.page.errors()[:2]}", "high")
    return d0, d1


def book(b, pk, horse_id, cat_name, arrive, depart, required=False):
    b.get(f"/t/{pk}/")
    if not b.click("Денники и услуги", "Заказать денник", "Денник"):
        b.friction("Страница турнира", "Нет кнопки заказа денника", "high")
        b.get(f"/t/{pk}/stables/")
    f = b.page.form(has_field="arrive")
    if not f:
        b.friction("Денники", "Нет формы брони", "critical")
        return False
    sel = f.find("select", attrs={"name": "category"})
    cid = next((o.get("value") for o in sel.find_all("option") if cat_name in o.get_text()), None)
    data = {"category": cid, "horse": str(horse_id), "arrive": arrive, "depart": depart, "required": "on" if required else None}
    b.submit(f, data, drop=("required",), note="booking_preview")
    f2 = b.page.form(has_field="confirm") or b.page.form(button="Подтвердить") or b.page.form(button="Отправить")
    if not f2 or b.page.errors():
        alert = b.page.soup.select_one(".alert--error") if b.page.soup else None
        return " ".join(alert.get_text(" ").split()) if alert else (b.page.errors() or ["нет шага подтверждения"])[0]
    b.submit(f2, {**data, "confirm": "1"}, drop=("required", "confirm"), note="booking_confirm")
    return "/my/stables/" in b.page.url or any("Бронь отправлена" in m for m in b.page.messages())


def order(b, pk, svc_name, qty, horse_id=None, trailer=""):
    b.get(f"/t/{pk}/stables/")
    f = None
    for x in b.page.forms():
        if x.find(attrs={"name": "service"}) and svc_name in x.get_text(" "):
            f = x; break
    if f is None:
        b.friction("Услуги", f"Нет формы заказа «{svc_name}»", "high")
        return False
    data = {"quantity": str(qty)}
    if horse_id and f.find(attrs={"name": "horse"}):
        data["horse"] = str(horse_id)
    if f.find(attrs={"name": "trailer"}):
        data["trailer"] = trailer or f"А{R.randint(100,999)}ВС 77"
    b.submit(f, data, note="service_order")
    return not b.page.errors() and not any("Проверьте" in m for m in b.page.messages())


def staff_process(sb, pk, reject_share=0.05):
    """Конюшня: подтверждает брони (часть отклоняет), назначает номера денников, выдаёт услуги (часть — не полностью)."""
    stats = dict(confirmed=0, rejected=0, boxes=0, delivered=0, partial=0)
    for _ in range(300):
        sb.get(f"/organizer/t/{pk}/stables/?tab=bookings")
        f = sb.page.form(action_contains="/stables/do/confirm/")
        if not f:
            break
        bid = f.find(attrs={"name": "booking"}).get("value")
        if R.random() < reject_share:
            fr = next((x for x in sb.page.forms() if "/do/reject/" in (x.get("action") or "") and x.find(attrs={"name": "booking", "value": bid})), None)
            if fr:
                sb.submit(fr, {"reason": "Нет мест в выбранной категории на эти даты — возьмите летник"})
                stats["rejected"] += 1
                continue
        sb.submit(f)
        stats["confirmed"] += 1
    n = 1
    for _ in range(300):
        sb.get(f"/organizer/t/{pk}/stables/?tab=bookings")
        fs = [x for x in sb.page.forms() if "/do/box/" in (x.get("action") or "") and not x.find(attrs={"name": "box"}).get("value")]
        if not fs:
            break
        f = fs[0]
        sb.submit(f, {"box": f"{n}"})
        if sb.page.errors() or any("занят" in m for m in sb.page.messages()):
            friction(sb.scenario, sb.persona, "Номер денника", f"{sb.page.messages()[:1]}", "info")
        n += 1
        stats["boxes"] += 1
    for _ in range(300):
        sb.get(f"/organizer/t/{pk}/stables/?tab=orders")
        f = sb.page.form(action_contains="/stables/do/deliver/")
        if not f:
            break
        q = f.find(attrs={"name": "quantity"})
        mx = int(q.get("value") or 1)
        give = mx if (R.random() > 0.2 or mx == 1) else mx - 1
        sb.submit(f, {"quantity": str(give)})
        if any("Нельзя" in m for m in sb.page.messages()):
            sb.friction("Выдача услуги", f"Отказ: {sb.page.messages()[:1]}", "high")
            break
        stats["delivered"] += 1
        stats["partial"] += give < mx
        if give < mx:
            # остаток выдаём позже
            continue
    return stats


def race_last_box(people_browsers, pk, horse_ids, cat_name, arrive, depart):
    """Несколько представителей одновременно жмут «Подтвердить» на последний денник."""
    prepared = []
    for b, h in zip(people_browsers, horse_ids):
        b.get(f"/t/{pk}/stables/")
        f = b.page.form(has_field="arrive")
        sel = f.find("select", attrs={"name": "category"})
        cid = next((o.get("value") for o in sel.find_all("option") if cat_name in o.get_text()), None)
        prepared.append((b, {"category": cid, "horse": str(h), "arrive": arrive, "depart": depart, "confirm": "1",
                             "key": uuid.uuid4().hex, "csrfmiddlewaretoken": b.s.cookies.get("csrftoken")}))
    results = []
    barrier = threading.Barrier(len(prepared))

    def go(b, data):
        barrier.wait()
        r = b.s.post(f"http://127.0.0.1:8000/t/{pk}/stables/", data=data, headers={"Referer": f"http://127.0.0.1:8000/t/{pk}/stables/"})
        results.append("/my/stables/" in r.url)
    th = [threading.Thread(target=go, args=x) for x in prepared]
    [t.start() for t in th]
    [t.join() for t in th]
    return results
