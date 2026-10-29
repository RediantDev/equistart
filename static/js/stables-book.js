// Экран «Денники и услуги» (макет 10B): лента ночей, счётчики услуг, итог и «Всем как у первой».
// Без JS форма работает: ночи — флажки, услуги — числовые поля. Сервер проверяет всё заново.
(function () {
  var form = document.querySelector("[data-cart]");
  if (!form) return;
  var MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  var UNITS = { "мешок": ["мешок", "мешка", "мешков"], "тюк": ["тюк", "тюка", "тюков"], "сутки": ["сутки", "суток", "суток"],
    "брикет": ["брикет", "брикета", "брикетов"], "штука": ["штука", "штуки", "штук"], "кипа": ["кипа", "кипы", "кип"],
    "рулон": ["рулон", "рулона", "рулонов"], "денник": ["денник", "денника", "денников"], "ночь": ["ночь", "ночи", "ночей"] };
  function plural(n, f) {
    var a = n % 10, b = n % 100;
    return a === 1 && b !== 11 ? f[0] : a >= 2 && a <= 4 && (b < 12 || b > 14) ? f[1] : f[2];
  }
  function unit(n, u) { var f = UNITS[(u || "").toLowerCase()]; return f ? plural(n, f) : u; }
  function parse(iso) { var p = iso.split("-"); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function period(first, last) {
    var a = parse(first), d = parse(last); d.setDate(d.getDate() + 1);
    var n = Math.round((d - a) / 864e5);
    var dates = a.getMonth() === d.getMonth() ? a.getDate() + "–" + d.getDate() + " " + MONTHS[d.getMonth()]
      : a.getDate() + " " + MONTHS[a.getMonth()] + " – " + d.getDate() + " " + MONTHS[d.getMonth()];
    return dates + " · " + n + " " + unit(n, "ночь");
  }

  var cards = Array.prototype.slice.call(form.querySelectorAll("[data-horse]"));
  function tiles(card) { return Array.prototype.slice.call(card.querySelectorAll("[data-night] input")); }
  function free(i) { return !i.disabled; }
  function selected(card) { return tiles(card).filter(function (i) { return free(i) && i.checked; }); }

  // Ночи одной брони идут подряд: отмеченная ночь дотягивает выбор до себя, снятая внутри — обрезает хвост.
  function onToggle(card, input) {
    var all = tiles(card), idx = all.indexOf(input);
    var on = all.map(function (i, k) { return free(i) && i.checked && k !== idx ? k : -1; }).filter(function (k) { return k >= 0; });
    if (input.checked) {
      if (on.length) {
        var lo = Math.min(on[0], idx), hi = Math.max(on[on.length - 1], idx), ok = true;
        for (var k = lo; k <= hi; k++) if (!free(all[k])) ok = false;
        all.forEach(function (i, k) { if (free(i)) i.checked = ok ? k >= lo && k <= hi : k === idx; });
      }
    } else if (on.length && idx > on[0] && idx < on[on.length - 1]) {
      all.forEach(function (i, k) { if (free(i) && k > idx) i.checked = false; });
    }
  }

  function applyCategory(card) {
    var sel = card.querySelector("[data-cat]");
    if (!sel) return;
    card.querySelectorAll("[data-night]").forEach(function (lab) {
      var inp = lab.querySelector("input");
      if (lab.classList.contains("is-busy")) return;
      var ok = (" " + lab.dataset.cats + " ").indexOf(" " + sel.value + " ") >= 0;
      inp.disabled = !ok || form.dataset.closed === "1";
      if (!ok) inp.checked = false;
      lab.classList.toggle("is-off", !ok);
    });
  }

  function sync() {
    var horses = 0, nights = 0, svc = {}, order = [];
    cards.forEach(function (card) {
      var s = selected(card), sum = card.querySelector("[data-sum]"), name = card.querySelector(".sb-card__head b");
      if (s.length) { horses++; nights += s.length; }
      if (sum) sum.textContent = s.length ? period(s[0].value, s[s.length - 1].value) : sum.dataset.empty;
      if (name && !card.querySelector(".sb-mine")) name.classList.toggle("sb-muted", !s.length);
    });
    form.querySelectorAll("[data-counter]").forEach(function (c) {
      var v = parseInt(c.querySelector("input").value, 10) || 0, id = c.dataset.svc;
      if (!v) return;
      if (!(id in svc)) { svc[id] = { name: c.dataset.sname, unit: c.dataset.unit, n: 0 }; order.push(id); }
      svc[id].n += v;
    });
    var main = form.querySelector("[data-bar-main]"), sub = form.querySelector("[data-bar-sub]"), btn = form.querySelector("[data-order-submit]");
    var lines = order.map(function (id) { var x = svc[id]; return x.name + " " + x.n + " " + unit(x.n, x.unit); });
    if (main) {
      if (horses) main.textContent = horses + " " + unit(horses, "денник") + " · " + nights + " " + unit(nights, "ночь");
      else main.textContent = lines.length ? "Без денников" : "Отметьте ночи или услуги";
    }
    if (sub) sub.textContent = lines.join(" · ");
    if (btn) btn.disabled = !horses && !lines.length;
  }

  form.addEventListener("change", function (e) {
    var t = e.target, card = t.closest("[data-horse]");
    if (card && t.closest("[data-night]")) onToggle(card, t);
    if (card && t.matches("[data-cat]")) applyCategory(card);
    sync();
  });
  form.addEventListener("input", sync);
  form.addEventListener("click", function (e) {
    var b = e.target.closest("[data-step]");
    if (!b) return;
    var inp = b.parentNode.querySelector("input");
    if (inp.disabled) return;
    inp.value = Math.max(0, (parseInt(inp.value, 10) || 0) + parseInt(b.dataset.step, 10)) || "";
    sync();
  });
  form.querySelectorAll("[data-step]").forEach(function (b) { b.hidden = false; });
  form.querySelectorAll("[data-counter]").forEach(function (c) { c.classList.add("is-js"); });

  // «Всем как у первой»: категория, ночи и количества услуг первой лошади — остальным.
  var copy = document.querySelector("[data-copy-first]");
  if (copy && cards.length > 1) {
    copy.hidden = false;
    copy.addEventListener("click", function () {
      var first = cards[0], cat = first.querySelector("[data-cat]");
      var nights = selected(first).map(function (i) { return i.value; });
      var qty = {};
      first.querySelectorAll("[data-counter]").forEach(function (c) { qty[c.dataset.svc] = c.querySelector("input").value; });
      cards.slice(1).forEach(function (card) {
        var sel = card.querySelector("[data-cat]");
        if (cat && sel && sel.value !== cat.value) { sel.value = cat.value; sel.dispatchEvent(new Event("change", { bubbles: true })); }
        tiles(card).forEach(function (i) { if (free(i)) i.checked = nights.indexOf(i.value) >= 0; });
        card.querySelectorAll("[data-counter]").forEach(function (c) {
          var inp = c.querySelector("input");
          if (!inp.disabled && c.dataset.svc in qty) inp.value = qty[c.dataset.svc];
        });
      });
      sync();
    });
  }

  // Тест 5.2, п. 23: ссылка из уведомления (#b12) раскрывает свёрнутый список броней лошади.
  function openHash() {
    var id = (location.hash || "").slice(1), el = id && document.getElementById(id);
    var box = el && el.closest("details[data-mine]");
    if (box && !box.open) { box.open = true; el.scrollIntoView({ block: "center" }); }
  }
  window.addEventListener("hashchange", openHash);
  openHash();

  // ---- Тест 5.2, вариант A (R3_04A): меню «⋯» у оформленного ----
  form.classList.add("is-js");
  function closeMenus(except) {
    document.querySelectorAll("details[data-menu][open]").forEach(function (d) { if (d !== except) d.open = false; });
  }
  document.addEventListener("click", function (e) {
    var m = e.target.closest("details[data-menu]");
    closeMenus(m);
    if (m && e.target.closest(".sb-menu__item") && !e.target.closest("[data-confirm]")) m.open = false;
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeMenus(null); });
  // Тест 5.3, п. 23: меню не уходит за край экрана — если снизу (над панелью «Заказать» и нижним меню) места нет, открывается вверх
  document.querySelectorAll("details[data-menu]").forEach(function (m) {
    m.addEventListener("toggle", function () {
      if (!m.open) return;
      m.classList.remove("is-up");
      var pop = m.querySelector(".sb-menu__pop"), r = pop.getBoundingClientRect(), bottom = window.innerHeight;
      document.querySelectorAll(".sb-bar, .bnav").forEach(function (el) {
        var cs = getComputedStyle(el);
        if (cs.display !== "none" && (cs.position === "fixed" || cs.position === "sticky")) bottom = Math.min(bottom, el.getBoundingClientRect().top);
      });
      if (r.bottom > bottom - 8 && m.getBoundingClientRect().top - r.height - 8 > 60) m.classList.add("is-up");
    });
  });
  // «Другая категория»: открывает форму изменения брони под строкой
  form.addEventListener("click", function (e) {
    var b = e.target.closest("[data-edit-form]");
    if (b) {
      var d = document.getElementById("ef" + b.dataset.editForm);
      if (d) { d.open = true; var c = d.querySelector("[data-close-act]"); if (c) c.hidden = false; var f = d.querySelector("select, input"); if (f) f.focus(); }
      return;
    }
    var x = e.target.closest("[data-close-act]");
    if (x) x.closest("details").open = false;
  });

  // ---- Тест 5.2, вариант A (R3_05A): правка ночей ожидающей брони ----
  // Нажатия только помечают: оформленная ночь — «убрать», свободная — «+ добавить». Сохраняет кнопка «Сохранить изменения»
  // через обычную форму my_edit (итоговый набор ночей); сервер заново проверяет непрерывность, вместимость и версию брони.
  function isoOf(d) { return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2); }
  function shift(iso, n) { var d = parse(iso); d.setDate(d.getDate() + n); return isoOf(d); }
  function ask(text, ok) {
    if (window.SLConfirm) return window.SLConfirm(text, ok, true);
    return Promise.resolve(window.confirm(text));
  }
  function pendingOf(card) {
    return Array.prototype.slice.call(card.querySelectorAll("[data-edit-bk]")).map(function (li) {
      return { id: li.dataset.editBk, arrive: li.dataset.arrive, depart: li.dataset.depart, cat: li.dataset.cat, name: li.dataset.hname };
    });
  }
  function nightsOf(b) { var out = [], d = b.arrive; while (d < b.depart) { out.push(d); d = shift(d, 1); } return out; }
  function labs(card) { return Array.prototype.slice.call(card.querySelectorAll("[data-night]")); }
  function contiguous(list) { for (var i = 1; i < list.length; i++) if (shift(list[i - 1], 1) !== list[i]) return false; return true; }
  var ST = { keep: "оформлено", rm: "убрать", add: "+ добавить" };

  // карточка → {b: бронь, rm: {ночь: 1}, add: {ночь: 1}}
  var edits = new Map();
  function freeFor(lab, b) { return !lab.dataset.bk && (" " + lab.dataset.cats + " ").indexOf(" " + b.cat + " ") >= 0; }
  function result(st) {
    var own = nightsOf(st.b).filter(function (n) { return !st.rm[n]; });
    return own.concat(Object.keys(st.add)).sort();
  }
  function render(card) {
    var st = edits.get(card), panel = card.querySelector("[data-edit-panel]"), intro = card.querySelector("[data-edit-intro]");
    card.classList.toggle("is-editing", !!st);
    labs(card).forEach(function (lab) {
      lab.classList.remove("is-keep", "is-rm", "is-add");
      var em = lab.querySelector(".sb-nt__st");
      if (em) em.textContent = "";
      if (!st) return;
      var n = lab.dataset.night;
      var k = lab.dataset.bk === st.b.id ? (st.rm[n] ? "rm" : "keep") : (st.add[n] ? "add" : "");
      if (k) { lab.classList.add("is-" + k); if (em) em.textContent = ST[k]; }
      lab.setAttribute("aria-pressed", k === "add" || k === "keep" ? "true" : "false");
    });
    if (intro) intro.hidden = !!st;
    if (!panel) return;
    panel.hidden = !st;
    if (!st) return;
    var res = result(st), save = panel.querySelector("[data-edit-save]"), hint = panel.querySelector("[data-edit-hint]");
    var changed = Object.keys(st.rm).length || Object.keys(st.add).length;
    panel.querySelector("[data-was]").textContent = period(st.b.arrive, shift(st.b.depart, -1));
    var now = panel.querySelector("[data-now]"), ok = res.length && contiguous(res);
    now.textContent = !res.length ? "бронь будет отменена" : ok ? period(res[0], res[res.length - 1]) : "ночи с разрывом";
    hint.textContent = res.length && !ok ? "Ночи одной брони должны идти подряд — добавьте пропущенную ночь или уберите крайнюю." : "";
    hint.classList.toggle("is-warn", !!(res.length && !ok));
    save.disabled = !changed || (res.length && !ok);
    save.textContent = res.length ? "Сохранить изменения" : "Отменить бронь";
    save.classList.toggle("btn--danger", !res.length);
    save.classList.toggle("btn--primary", !!res.length);
  }
  function start(card, b) {
    // новая бронь в этой карточке и правка — не одновременно: снимаем отмеченные ночи новой брони
    tiles(card).forEach(function (i) { if (free(i)) i.checked = false; });
    edits.set(card, { b: b, rm: {}, add: {} });
    sync();
    render(card);
  }
  function tap(card, lab) {
    var st = edits.get(card), n = lab.dataset.night;
    if (lab.dataset.bk === st.b.id) { if (st.rm[n]) delete st.rm[n]; else st.rm[n] = 1; }
    else if (freeFor(lab, st.b)) { if (st.add[n]) delete st.add[n]; else st.add[n] = 1; }
    else return;
    render(card);
  }
  function submit(st) {
    var res = result(st);
    if (!res.length) {
      ask("Убраны все ночи — бронь «" + st.b.name + "» будет отменена. Отменить бронь?", "Отменить бронь").then(function (ok) {
        var f = document.getElementById("x" + st.b.id);
        if (ok && f) f.requestSubmit ? f.requestSubmit() : f.submit();
      });
      return;
    }
    var f = document.getElementById("e" + st.b.id);
    if (!f || !contiguous(res)) return;
    f.querySelectorAll("input[data-gen]").forEach(function (i) { i.remove(); });
    res.forEach(function (n) {
      var i = document.createElement("input"); i.type = "hidden"; i.name = "nights"; i.value = n; i.dataset.gen = "1"; f.appendChild(i);
    });
    var cat = document.createElement("input"); cat.type = "hidden"; cat.name = "category"; cat.value = st.b.cat; cat.dataset.gen = "1";
    // поле категории из формы «Другая категория» связано с формой атрибутом form= — на время отправки отключаем его
    document.querySelectorAll('[name="category"][form="e' + st.b.id + '"]').forEach(function (s) { s.disabled = true; });
    f.appendChild(cat);
    f.requestSubmit ? f.requestSubmit() : f.submit();
  }

  cards.forEach(function (card) {
    var list = pendingOf(card);
    if (!list.length) return;
    var intro = card.querySelector("[data-edit-intro]");
    if (intro) intro.hidden = false;
    list.forEach(function (b) {
      card.querySelectorAll('[data-night][data-bk="' + b.id + '"]').forEach(function (lab) {
        lab.classList.add("is-tap"); lab.tabIndex = 0; lab.setAttribute("role", "button");
      });
    });
    var panel = card.querySelector("[data-edit-panel]");
    if (panel) {
      panel.querySelector("[data-edit-reset]").addEventListener("click", function () { edits.delete(card); render(card); });
      panel.querySelector("[data-edit-save]").addEventListener("click", function () { var st = edits.get(card); if (st) submit(st); });
    }
  });
  document.querySelectorAll("[data-edit-nights]").forEach(function (b) { b.hidden = false; });
  document.querySelectorAll("[data-edit-form]").forEach(function (b) { b.hidden = false; });

  function bookingById(card, id) { return pendingOf(card).filter(function (x) { return x.id === id; })[0]; }
  function onNight(e, lab) {
    var card = lab.closest("[data-horse]"), st = edits.get(card);
    if (st) { e.preventDefault(); tap(card, lab); return true; }
    var b = lab.dataset.bk && bookingById(card, lab.dataset.bk);
    if (b) { e.preventDefault(); start(card, b); tap(card, lab); return true; }
    return false;
  }
  form.addEventListener("click", function (e) {
    var lab = e.target.closest("[data-night]");
    if (lab && (lab.classList.contains("is-tap") || edits.get(lab.closest("[data-horse]")))) onNight(e, lab);
    var en = e.target.closest("[data-edit-nights]");
    if (en) {
      var row = document.getElementById("b" + en.dataset.editNights), card = row && row.closest("[data-horse]");
      if (card) { start(card, bookingById(card, en.dataset.editNights)); card.querySelector(".sb-strip").scrollIntoView({ block: "center", behavior: "smooth" }); }
    }
  });
  form.addEventListener("keydown", function (e) {
    var lab = e.target.closest && e.target.closest(".sb-nt.is-tap");
    if (lab && (e.key === "Enter" || e.key === " ")) onNight(e, lab);
  });
  // свободная соседняя ночь ожидающей брони: начинает правку («+ добавить»), а не отдельную бронь
  form.addEventListener("change", function (e) {
    var inp = e.target, lab = inp.closest && inp.closest("[data-night]"), card = lab && lab.closest("[data-horse]");
    if (!card || !inp.checked || lab.dataset.bk || edits.get(card)) return;
    var night = lab.dataset.night;
    var b = pendingOf(card).filter(function (x) {
      return (night === x.depart || night === shift(x.arrive, -1)) && (" " + lab.dataset.cats + " ").indexOf(" " + x.cat + " ") >= 0;
    })[0];
    if (!b) return;
    e.stopImmediatePropagation();
    inp.checked = false;
    start(card, b);
    tap(card, lab);
  }, true);

  cards.forEach(applyCategory);
  sync();
})();
