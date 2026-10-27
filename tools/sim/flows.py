"""Сценарные шаги: регистрация, вход, профили, турнир, модерация, заявки, судейство."""
import io, re, random, subprocess
from browser import Browser, Page, friction
from people import R, phone_variants, horse_name, BREEDS, COLORS, SEXES, RANKS

PDF_BYTES = None


def pdf_bytes():
    global PDF_BYTES
    if PDF_BYTES is None:
        from reportlab.pdfgen import canvas
        buf = io.BytesIO(); c = canvas.Canvas(buf); c.drawString(100, 750, "Polozhenie"); c.save(); PDF_BYTES = buf.getvalue()
    return PDF_BYTES


# ---------------- аккаунт ----------------

def register(sc, person, categories, extra_label=""):
    b = Browser(sc, f"{person.fio}{extra_label}", person.level)
    p = b.get("/")
    # находим регистрацию по ссылке, как человек
    if not b.click("Регистрац", "Зарегистр", "Создать аккаунт"):
        b.click("Войти")
        if not b.click("Регистрац", "Зарегистр", "Создать аккаунт"):
            b.friction("Главная", "Не найдена ссылка на регистрацию с главной/входа", "high")
            b.get("/register/")
    phone_in = person.phone if person.level == "confident" else R.choice(phone_variants(person.phone))
    base = {"phone": phone_in, "last_name": person.last, "first_name": person.first, "middle_name": person.middle,
            "categories": categories, "pd_consent": "on", "password1": person.password, "password2": person.password,
            "publication_consent": "on" if "athlete" in categories else None}
    attempts = []
    if person.level == "novice":
        attempts.append(dict(base, password1="12345678", password2="12345678"))
        attempts.append(dict(base, pd_consent=None))
        if "athlete" in categories:
            attempts.append(dict(base, publication_consent=None))
    elif person.level == "medium":
        attempts.append(dict(base, password2=person.password + "1"))
    attempts.append(base)
    for i, data in enumerate(attempts):
        f = b.page.form(has_field="password1")
        if f is None:
            break
        prev_filled = {k: (f.find(attrs={"name": k}) or {}).get("value") if f.find(attrs={"name": k}) else None for k in ("last_name", "phone")}
        if i > 0 and not prev_filled.get("last_name"):
            b.friction("Регистрация", "После ошибки фамилия не сохранилась — вводить заново", "medium")
        p = b.submit(f, data, drop=("categories", "pd_consent", "publication_consent"))
        if "/register/" in p.url and i < len(attempts) - 1:
            errs = p.errors()
            if not errs:
                b.friction("Регистрация", "Форма не прошла, но текст ошибки не найден рядом с полем", "high")
            b.errors_seen = errs
            continue
    if "/register/" in b.page.url:
        b.friction("Регистрация", f"Не удалось зарегистрироваться: {b.page.errors()}", "critical", phone=phone_in)
        return None
    return b


def login(sc, person, label=""):
    b = Browser(sc, f"{person.fio}{label}", person.level)
    b.get("/login/")
    f = b.page.form(has_field="password")
    b.submit(f, {"phone": person.phone, "password": person.password})
    if "/login/" in b.page.url:
        b.friction("Вход", f"Не вошёл: {b.page.errors()}", "critical")
    return b


# ---------------- профили ----------------

_RANKS = ["", "none", "3y", "2y", "1y", "3", "2", "1", "kms", "ms", "msmk"]


def _rank(rank_j="", rank_d=""):
    """Разряд один на обе дисциплины (решение 27.10.2026): берём старший из переданных."""
    return max(rank_j or "", rank_d or "", key=_RANKS.index)


def add_athlete(b, person, rank_j="", rank_d="", coach=""):
    b.get("/athletes/new/")
    f = b.page.form(has_field="last_name")
    data = {"last_name": person.last, "first_name": person.first, "middle_name": person.middle,
            "birth_date": f"{person.birth_year}-{R.randint(1,12):02d}-{R.randint(1,28):02d}",
            "rank": _rank(rank_j, rank_d), "club": person.club, "region": person.region,
            "coach_name": coach, "consent": "on"}
    if b.level == "novice" and R.random() < 0.6:
        # новичок не отмечает согласие с первого раза
        b.submit(f, dict(data, consent=None), drop=("consent",))
        if "/athletes/new/" in b.page.url or b.page.form(has_field="last_name"):
            errs = b.page.errors()
            if not any("соглас" in e.lower() for e in errs):
                b.friction("Новый спортсмен", "Без согласия форма не сохраняется, но причина не видна у поля", "high", errs=errs)
            f = b.page.form(has_field="last_name")
    b.submit(f, data, drop=("consent",))
    m = re.search(r"/athletes/(\d+)/", b.page.url)
    if not m:
        b.friction("Новый спортсмен", f"Спортсмен не создан: {b.page.errors()[:3]}", "critical")
        return None
    return int(m.group(1))


def fill_own_profile(b, person, rank_j="", rank_d=""):
    """Спортсмен после регистрации: профиль уже создан, нужно дописать дату рождения и разряд — по подсказке в кабинете."""
    b.get("/cabinet/")
    if not b.click("укажите дату рождения", "Профиль спортсмена"):
        b.friction("Кабинет спортсмена", "Нет подсказки/ссылки на профиль спортсмена после регистрации", "high")
        return None
    if "/edit/" not in b.page.url:
        if not b.click("Изменить", "Редактировать"):
            b.friction("Профиль спортсмена", "Нет кнопки «Изменить» в профиле", "high")
            return None
    f = b.page.form(has_field="birth_date")
    data = {"birth_date": f"{person.birth_year}-{R.randint(1,12):02d}-{R.randint(1,28):02d}", "rank": _rank(rank_j, rank_d),
            "club": person.club, "region": person.region}
    b.submit(f, data)
    m = re.search(r"/athletes/(\d+)/", b.page.url)
    if not m or b.page.errors():
        b.friction("Профиль спортсмена", f"Не сохранилось: {b.page.errors()[:3]}", "critical")
        return None
    return int(m.group(1))


def add_horse(b, age=None, pony=False):
    b.get("/horses/new/")
    f = b.page.form(has_field="name")
    name = horse_name()
    year = 2026 - (age or R.randint(5, 17))
    data = {"name": name, "birth_year": str(year), "color": R.choice(COLORS), "sex": R.choice(SEXES), "breed": R.choice(BREEDS)}
    if pony:
        data.update(is_pony="on", height_cm=str(R.randint(120, 148)))
    b.submit(f, data, drop=("is_pony",))
    f2 = b.page.form(has_field="confirm_similar")
    if f2 is not None:
        b.similar_hits = getattr(b, "similar_hits", 0) + 1
        if b.level == "novice" and R.random() < 0.4:
            b.friction("Новая лошадь", "Предупреждение о лошади с той же кличкой и годом рождения — новичок решил, что это ошибка, и изменил кличку", "medium")
            data["name"] = name + " " + R.choice(["2", "II", "(моя)"])
            b.submit(f2, data, drop=("is_pony",))
        else:
            b.submit(f2, data, drop=("is_pony",))
        friction(b.scenario, b.persona, "Новая лошадь", "Сработало предупреждение о похожей кличке (лишний шаг)", "low", level=b.level)
    m = re.search(r"/horses/(\d+)/", b.page.url)
    if not m:
        b.friction("Новая лошадь", f"Лошадь не создана: {b.page.errors()[:3]} {b.page.messages()[:1]} {b.page.url}", "critical")
        return None, name
    return int(m.group(1)), name


# ---------------- турнир ----------------

def create_tournament(b, spec):
    """spec: title, start, end, venue(name, region, settlement), programs=[{day, discipline, type, heights, table, variant, test, templates:[ids], custom:[dict]}]"""
    b.get("/cabinet/")
    if not b.click("Создать соревнование", "Создать турнир"):
        b.friction("Кабинет судьи", "Нет кнопки создания турнира в кабинете", "high")
        b.get("/organizer/new/")
    f = b.page.form(has_field="title")
    data = {"title": spec["title"], "starts_on": spec["start"], "venue_name": spec["venue"][0], "venue_region": spec["venue"][1],
            "venue_settlement": spec["venue"][2], "venue_address": spec["venue"][3] if len(spec["venue"]) > 3 else "",
            "contact_name": spec.get("contact_name", ""), "contact": spec.get("contact", ""), "level": spec.get("level", "club")}
    if spec.get("venue_pick"):
        sel = f.find("select", attrs={"name": "venue"})
        vid = next((o.get("value") for o in sel.find_all("option") if spec["venue_pick"] in o.get_text()), None) if sel else None
        if vid:
            data.update(venue=vid, venue_name="", venue_region="", venue_settlement="", venue_address="")
        else:
            b.friction("Мастер, шаг 1", f"Клуб «{spec['venue_pick']}» не найден в списке мест", "high")
    if spec["end"] != spec["start"]:
        data.update(multiday="on", ends_on=spec["end"])
    if spec.get("classification"):
        data["classification"] = "on"
    b.submit(f, data, drop=("multiday", "classification"))
    m = re.search(r"/organizer/t/(\d+)/", b.page.url)
    if not m:
        b.friction("Мастер, шаг 1", f"Черновик не создан: {b.page.errors()}", "critical")
        return None
    pk = int(m.group(1))
    if spec.get("before_programs"):
        spec["before_programs"](b, pk)
    prog_ids = []
    for pr in spec["programs"]:
        b.get(f"/organizer/t/{pk}/step/2/")
        f = b.page.form(action_contains="programs/add")
        d = {"discipline": pr["discipline"], "day": pr["day"], "start_time": pr.get("time", ""), "name": pr.get("name", "")}
        if pr["discipline"] == "jumping":
            d.update(program_type=pr["type"], heights_text=pr.get("heights", ""), table=pr.get("table", ""), variant=pr.get("variant", ""))
        else:
            tid = pr["test"]() if callable(pr["test"]) else pr["test"]
            d.update(dressage_test=str(tid))
        b.submit(f, d, drop=("discipline",), note="program_add")
        m = re.search(r"#program-(\d+)", b.page.url)
        if not m:
            b.friction("Мастер, шаг 2", f"Маршрут/езда не добавлены: {b.page.errors()}", "critical", spec=pr.get("name"))
            continue
        pid = int(m.group(1)); prog_ids.append(pid)
        for tpl in pr.get("templates", []):
            f = b.page.form(action_contains=f"programs/{pid}/classes/from-template")
            extra = [("height", str(pr["tpl_height"]))] if pr.get("tpl_height") else None
            b.submit(f, button_name="template", button_value=str(tpl), extra=extra, note="class_tpl")
        for cc in pr.get("custom", []):
            f = b.page.form(action_contains=f"programs/{pid}/classes/add")
            if f is None:
                # форма своего зачёта открывается по кнопке «Свой зачёт»
                href = next((h for t, h in b.page.links() if f"custom={pid}" in h), None)
                if not href:
                    b.friction("Мастер, шаг 2", "Не найдена кнопка «Свой зачёт»", "high")
                    continue
                b.get(href)
                f = b.page.form(action_contains=f"programs/{pid}/classes/add")
            b.submit(f, cc, note="class_custom")
            if b.page.status == 422:
                b.friction("Мастер, шаг 2 — свой зачёт", f"Ошибка зачёта: {b.page.errors()}", "medium")
    # шаг 3
    b.get(f"/organizer/t/{pk}/step/3/")
    f = b.page.form(has_field="entry_deadline")
    b.submit(f, {"entry_deadline": spec["deadline"], "cancel_terms": spec.get("cancel_terms", "")}, button_name="next",
             files={"regulation": ("polozhenie.pdf", pdf_bytes(), "application/pdf")})
    b.get(f"/organizer/t/{pk}/step/4/")
    f = b.page.form(button="Отправить на проверку")
    if not f:
        b.friction("Мастер, шаг 4", "Нет кнопки отправки на проверку", "critical", problems=b.page.text()[:400])
        return pk
    b.submit(f)
    return pk, prog_ids


def moderate(mod_b, pk, approve=True, comment=""):
    mod_b.get("/moderation/")
    mod_b.get(f"/moderation/t/{pk}/")
    f = mod_b.page.form(action_contains="decide")
    data = {"decision": "approve" if approve else "return", "comment": comment, "confirm_venue": "1"}
    mod_b.submit(f, data, drop=("decision", "confirm_venue"))
    return mod_b.page.messages()


def invite(b, pk, phone, role):
    b.get(f"/organizer/t/{pk}/team/")
    f = b.page.form(has_field="role")
    b.submit(f, {"phone": phone, "role": role})
    return b.page.messages() + b.page.errors()


def accept_invites(b):
    b.get("/cabinet/")
    if not b.click("Приглашения"):
        b.get("/cabinet/invitations/")
    n = 0
    while True:
        f = b.page.form(action_contains="/cabinet/invitations/")
        if not f:
            break
        b.submit(f, {"decision": "accept"}, drop=("decision",))
        n += 1
        if n > 20:
            break
    return n


# ---------------- участники и заявки ----------------

def find_tournament(b, title):
    """Найти турнир как человек: через календарь, при необходимости — поиском."""
    b.get("/")
    href = b.page.find_link(title)
    if not href:
        f = b.page.form(has_field="q")
        if f:
            b.submit(f, {"q": title.split("«")[-1].split("»")[0] if "«" in title else title[:20]})
            href = b.page.find_link(title)
    if not href:
        b.friction("Календарь", f"Не нашёл турнир «{title}» ни в списке, ни поиском", "high")
        return None
    b.get(href)
    return int(re.search(r"/t/(\d+)/", b.page.url).group(1))


def apply_pairs(b, title, pairs, lines_for_pair):
    """pairs: [(athlete_id, horse_id)]; lines_for_pair(i) -> [(class_id, height or None)]. Новый экран: у пары выбор зачётов во всех маршрутах и одна кнопка."""
    pk = find_tournament(b, title)
    if not pk:
        return None
    if not b.click("Подать заявку", "Войти и подать заявку"):
        b.friction("Страница турнира", "Не найдена кнопка «Подать заявку»", "critical")
        return None
    added = 0
    for i, (a, h) in enumerate(pairs):
        f = b.page.form(has_field="athlete")
        b.submit(f, {"athlete": str(a), "horse": str(h)}, button_name="action", button_value="pair_add", note="pair_add")
        f = b.page.form(button="Добавить выбранные")
        if f is None or not f.find(attrs={"name": "a", "value": str(a)}):
            b.friction("Заявка", "После добавления пары не раскрылся выбор маршрутов для неё", "high")
            continue
        if b.level == "novice" and i == 0:
            # новичок сразу жмёт «Добавить выбранные», ничего не выбрав
            b.submit(f, button_name="action", button_value="add_many", note="lines_add_empty")
            alert = b.page.soup.select_one(".alert--error") if b.page.soup else None
            if not alert or "Выберите зачёт" not in alert.get_text():
                b.friction("Заявка", "Нажал «Добавить выбранные» без выбора — нет понятной подсказки", "medium")
            f = b.page.form(button="Добавить выбранные") or f
        data, confirm = {}, {}
        for cls, ht in lines_for_pair(i):
            target = None
            for sel in f.find_all("select", attrs={"name": re.compile(r"^sel_")}):
                for o in sel.find_all("option"):
                    v = o.get("value") or ""
                    if v.split("|")[0] == str(cls) and (ht is None or v.endswith(f"|{ht}") or "|" not in v):
                        if o.has_attr("disabled"):
                            friction(b.scenario, b.persona, "Заявка", f"Зачёт недоступен, причина показана: {o.get_text()[:90]}", "info", level=b.level)
                            alt = [x for x in sel.find_all("option") if x.get("value") and not x.has_attr("disabled")]
                            if alt:
                                target = (sel.get("name"), alt[0].get("value"))
                        else:
                            target = (sel.get("name"), v)
                        break
                if target:
                    break
            if not target:
                b.friction("Заявка", f"Зачёт {cls} не найден в списке маршрутов пары", "medium")
                continue
            data[target[0]] = target[1]
            pid = target[0][4:]
            if f.find(attrs={"name": f"confirm_{pid}"}):
                confirm[f"confirm_{pid}"] = "1"
        if not data:
            continue
        b.submit(f, {**data, **confirm}, button_name="action", button_value="add_many", note="lines_add")
        errs = b.page.errors() + [m for m in b.page.messages() if m.startswith("Проверьте")]
        alert = b.page.soup.select_one(".alert--error") if b.page.soup else None
        if alert:
            errs.append(" ".join(alert.get_text(" ").split())[:200])
        if errs:
            b.friction("Заявка", f"Не всё добавилось: {errs[:2]}", "medium")
        for m in b.page.messages():
            mm = re.search(r"добавлено (\d+)", m)
            if mm:
                added += int(mm.group(1))
    f = b.page.form(button="Отправить заявку")
    b.submit(f, button_name="action", button_value="submit", note="submit")
    m = re.search(r"/applications/(\d+)/", b.page.url)
    if not m:
        b.friction("Заявка", f"Заявка не отправлена: {b.page.errors()[:3]} {b.page.messages()[:2]}", "critical")
        return None
    return int(m.group(1)), added


def decide_all(jb, pk, reject_share=0.03, reason="Нет ветеринарных документов на месте"):
    """Судья: несколько отклонить точечно, остальные — «Все по фильтру (на рассмотрении)»."""
    jb.get(f"/organizer/t/{pk}/entries/?decision=pending")
    f = jb.page.form(action_contains="decide")
    if not f:
        return 0, 0
    ids = [i.get("value") for i in f.find_all("input", attrs={"name": "entry"})]
    rej = [x for x in ids if R.random() < reject_share]
    if rej:
        jb.submit(f, {"entry": rej, "action": "reject"}, note="decide_pre")
        f2 = jb.page.form(action_contains="decide")
        jb.submit(f2, {"reason": reason}, note="decide_confirm")
        jb.get(f"/organizer/t/{pk}/entries/?decision=pending")
        f = jb.page.form(action_contains="decide")
    total = 0
    if f:
        jb.submit(f, {"action": "accept", "scope": "filter"}, note="decide_pre_filter")
        if jb.page.status >= 400:
            jb.friction("Рассмотрение заявок", f"«Все по фильтру» → HTTP {jb.page.status}", "critical")
            return 0, len(rej)
        f2 = jb.page.form(action_contains="decide")
        total = int(re.search(r"(\d+)", f2.find("button", class_="btn--primary").get_text()).group(1)) if f2 else 0
        jb.submit(f2, note="decide_confirm")
        if jb.page.status >= 400:
            jb.friction("Рассмотрение заявок", f"Подтверждение решения → HTTP {jb.page.status}", "critical")
    return total, len(rej)


def jump_result():
    r = R.random()
    if r < 0.03:
        return {"status": "eliminated", "reason": "второе неповиновение"}
    if r < 0.05:
        return {"status": "retired"}
    kd = R.choices([0, 1, 2, 3, 4], [48, 26, 15, 7, 4])[0]
    rf = R.choices([0, 1], [90, 10])[0]
    return {"knockdowns": str(kd), "refusals": str(rf)}


def conduct_jumping(jb, pk, pid, length=None, speed=350):
    base = f"/organizer/t/{pk}/conduct/p/{pid}/"
    jb.get(base)
    f = jb.page.form(action_contains="/course/")
    length = length or R.choice(range(300, 520, 10))
    jb.submit(f, {"main_length": str(length), "main_speed": str(speed)}, note="course")
    f = jb.page.form(action_contains="/draw/")
    jb.submit(f, note="draw")
    f = jb.page.form(action_contains="/publish/")
    jb.submit(f, note="publish_start")
    ta = length / speed * 60
    saves = 0
    stage_q = ""
    seen_stages = set()
    for _ in range(3):
        guard = 0
        while guard < 400:
            guard += 1
            follow = "entry=" in jb.page.url and "field" in jb.page.url and jb.page.form(action_contains="field/save") is not None \
                and "stage=" in jb.page.url and ("stage=" + (stage_q.replace("?stage=", "") or "x")) not in jb.page.url
            if not follow:
                jb.get(base + "field/" + stage_q, note="field")
            f = jb.page.form(action_contains="field/save")
            if not f:
                break
            stage = f.find(attrs={"name": "stage"}).get("value")
            entry = f.find(attrs={"name": "entry"}).get("value")
            if not entry:
                break
            res = jump_result()
            res.setdefault("time", f"{ta * R.uniform(0.82, 1.12):.2f}".replace(".", ","))
            if res.get("status") in ("eliminated", "retired"):
                res.pop("time", None)
            st = res.pop("status", "ok")
            before = jb.page.url
            jb.submit(f, dict(res, status=st), note="field_save")
            saves += 1
            if "saved=" not in jb.page.url and "field" in jb.page.url and jb.page.errors():
                jb.friction("Экран судьи конкура", f"Не сохранилось: {jb.page.errors()[:2]}", "high")
                break
        # переход к перепрыжке / следующей фазе
        nxt = [t for t, h in jb.page.links() if "stage=" in h]
        stages = sorted({re.search(r"stage=([\w]+)", h).group(1) for t, h in jb.page.links() if "stage=" in h})
        todo = [s for s in stages if s not in seen_stages and ("?stage=" + s) != stage_q]
        seen_stages.add(stage_q.replace("?stage=", "") or "main")
        if not todo:
            break
        stage_q = "?stage=" + todo[0]
        if todo[0] in seen_stages:
            break
        # норма времени перепрыжки
        jb.get(base)
        f = jb.page.form(action_contains="/course/")
        jo = {k.get("name"): str(int(ta * 0.75)) for k in f.find_all("input") if "time_allowed" in (k.get("name") or "") and not k.get("name").startswith("main")}
        jo.update({k.get("name"): str(int(length * 0.6)) for k in f.find_all("input") if (k.get("name") or "").endswith("_length") and not k.get("name").startswith("main")})
        if jo:
            jb.submit(f, jo, note="course_jo")
    # публикация результатов по всем зачётам
    jb.get(base)
    pubs = 0
    for f in [x for x in jb.page.forms() if "publish_results" in (x.get("action") or "")]:
        cid = f.find(attrs={"name": "tclass"}).get("value")
        jb.post(base + "publish_results/", {"tclass": cid}, note="publish_results")
        pubs += 1
    return saves, pubs


# ---------------- выездка ----------------

def create_test(b, t_pk, name, short, level, kind, arena, minutes, movements, collective, second="", scheme="fei_junior", tie="collective"):
    b.get(f"/organizer/t/{t_pk}/step/2/")
    if not b.click("Создайте свою"):
        b.friction("Мастер, шаг 2", "Не видна ссылка «Создайте свою» езду", "high")
        b.get("/dressage-tests/new/")
    f = b.page.form(has_field="technique_movements")
    data = {"name": name, "short": short, "level": level, "kind": kind, "arena": arena, "duration_min": str(minutes),
            "penalty_scheme": scheme, "tie_rule": tie, "step": "0.5",
            "technique_movements": movements, "technique_collective": collective, "second_collective": second}
    if b.level == "novice":
        # новичок вставляет текст из бланка без разделителей «|»
        bad = "\n".join(l.split("|")[2].strip() + " " + l.split("|")[1].strip() + " коэф " + l.split("|")[3].strip() for l in movements.splitlines())
        b.submit(f, dict(data, technique_movements=bad))
        ok = re.search(r"/dressage-tests/(\d+)/", b.page.url) or "/step/2/" in b.page.url
        if not ok and "без разделителей" in b.page.text():
            friction(b.scenario, b.persona, "Конструктор езды", "Текст без «|» — предпросмотр показал предупреждение, езда не сохранена до исправления", "info", level=b.level)
            f = b.page.form(has_field="technique_movements")
        if ok:
            b.friction("Конструктор езды", "Текст без «|» принят молча: номера, буквы и коэффициенты слились в описание, коэффициенты = 1 — ошибку видно только при судействе", "high")
            b.get(f"/dressage-tests/new/?t={t_pk}")
            f = b.page.form(has_field="technique_movements")
        else:
            f = b.page.form(has_field="technique_movements")
    b.submit(f, data)
    if "Проверьте лист перед сохранением" in b.page.text():
        if b.level == "novice" and "без разделителей" in b.page.text():
            b.friction("Конструктор езды", "Предпросмотр предупредил о тексте без «|» — новичок исправил до сохранения", "info")
        f = b.page.form(has_field="technique_movements")
        b.submit(f, data, button_name="confirm", button_value="1")
    m = re.search(r"/dressage-tests/(\d+)/", b.page.url)
    if m:
        return int(m.group(1))
    sel = b.page.soup.find("select", attrs={"name": "dressage_test"}) if b.page.soup else None
    if sel is not None:
        ids = [int(o.get("value")) for o in sel.find_all("option") if o.get("value") and o.get_text().strip().startswith(name)]
        if ids:
            return max(ids)
    b.friction("Конструктор езды", f"Езда не сохранена: {b.page.errors()[:3]}", "critical")
    return None


def set_panel(b, pk, pid, rows):
    """rows: [(letter, user_phone_or_name_match, judge_name, category, region, role)]"""
    b.get(f"/organizer/t/{pk}/conduct/p/{pid}/panel/")
    f = b.page.form(has_field="letter_0")
    if not f:
        b.friction("Бригада выездки", "Нет формы бригады", "critical")
        return False
    data = {}
    from orm import q as _q
    for i, (letter, user_label, name, cat, reg, role) in enumerate(rows):
        if user_label and user_label.startswith("+7"):
            nm = _q(f"from apps.accounts.models import User\nu=User.objects.get(phone='{user_label}')\nout=[u.last_name+' '+u.first_name, u.last_name+' '+u.first_name+' '+u.middle_name]")
            user_label, name = nm[0], nm[1]
        sel = f.find("select", attrs={"name": f"user_{i}"})
        uid = ""
        for o in sel.find_all("option"):
            if user_label and user_label in o.get_text():
                uid = o.get("value"); break
        data.update({f"letter_{i}": letter, f"user_{i}": uid, f"judge_name_{i}": name, f"judge_category_{i}": cat,
                     f"judge_region_{i}": reg, f"role_{i}": role})
    b.submit(f, data)
    if b.page.errors() or any("Провер" in m for m in b.page.messages()):
        b.friction("Бригада выездки", f"Ошибка: {b.page.errors()[:2]} {b.page.messages()[:2]}", "medium")
    return True


def mark_value(base):
    v = max(3.0, min(10.0, R.gauss(base, 0.9)))
    return f"{round(v * 2) / 2:.1f}".replace(".0", "").replace(".", ",")


def judge_all(b, pk, pid, letter, skill=6.5, per_mark_share=0.0, c_errors=0.04):
    """Судья на букве проходит всех по очереди и подписывает листы."""
    base = f"/organizer/t/{pk}/conduct/p/{pid}/judge/{letter}/"
    signed = 0
    for _ in range(400):
        b.get(base, note="sheet")
        f = b.page.form(action_contains="/sign/")
        if not f:
            break
        entry = f.find(attrs={"name": "entry"}).get("value")
        if f.find("button") and f.find("button").has_attr("disabled") and "Подписан" in b.page.text()[:3000]:
            break
        pair_base = skill + R.gauss(0, 0.6)
        marks = {}
        for el in f.find_all(attrs={"name": re.compile(r"^m_")}):
            marks[el.get("name")] = mark_value(pair_base)
        if not marks:
            # оценки кнопками: имена полей могут быть радио — собираем уникальные
            names = {el.get("name") for el in f.find_all("input") if (el.get("name") or "").startswith("m_")}
            marks = {n: mark_value(pair_base) for n in names}
        if letter == "C" and R.random() < c_errors:
            ff = b.page.form(action_contains="/flags/")
            if ff:
                b.submit(ff, {"errors": "1", "technical": "0"}, note="flags")
                b.get(base + f"?entry={entry}", note="sheet")
                f = b.page.form(action_contains="/sign/")
        if R.random() < per_mark_share:
            for k, v in marks.items():
                _, part, i = k.split("_", 2)
                b.post(base + "mark/", {"entry": entry, "part": part, "i": i, "value": v}, headers={"X-Requested-With": "fetch"}, note="mark_fetch")
            b.get(base + f"?entry={entry}", note="sheet")
            f = b.page.form(action_contains="/sign/")
        before = b.page.url
        b.submit(f, marks, note="sign")
        if any("подписан" in m.lower() for m in b.page.messages()):
            signed += 1
        else:
            b.friction("Лист судьи", f"Не подписан: {b.page.messages()[:2]} {b.page.errors()[:2]}", "high", letter=letter)
            break
    return signed


def scorer_publish(b, pk, pid):
    base = f"/organizer/t/{pk}/conduct/p/{pid}/scorer/"
    b.get(base, note="scorer")
    b.post(base + "approve_all/", {}, note="approve_all")
    msgs = b.page.messages()
    pb = f"/organizer/t/{pk}/conduct/p/{pid}/"
    b.get(pb)
    n = 0
    for f in [x for x in b.page.forms() if "publish_results" in (x.get("action") or "")]:
        cid = f.find(attrs={"name": "tclass"}).get("value")
        b.post(pb + "publish_results/", {"tclass": cid}, note="publish_results")
        if any("опубликованы" in m for m in b.page.messages()):
            n += 1
        else:
            b.friction("Публикация результатов выездки", f"Не опубликовано: {b.page.messages()[:2]}", "high")
    return msgs, n


def reg_or_login(sc, person, cats, label=""):
    import json as _j
    b = register(sc, person, cats, label)
    return b or login(sc, person, label)
