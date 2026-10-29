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

  // Тест 5.2, п. 25: ночи ожидающей брони меняются нажатием на ленту — крайний день убирает ночь, соседний добавляет.
  // Отправляется обычная форма изменения брони (my_edit): сервер заново проверяет подряд идущие ночи, вместимость и состояние брони.
  function isoOf(d) { return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2); }
  function shift(iso, n) { var d = parse(iso); d.setDate(d.getDate() + n); return isoOf(d); }
  function ask(text, ok) {
    if (window.SLConfirm) return window.SLConfirm(text, ok, false);
    return Promise.resolve(window.confirm(text));
  }
  function hint(card, text, warn) {
    var h = card.querySelector("[data-edit-hint]");
    if (!h) return;
    if (!h.dataset.base) h.dataset.base = h.textContent;
    h.textContent = text || h.dataset.base;
    h.classList.toggle("is-warn", !!warn);
  }
  function pendingOf(card) {
    return Array.prototype.slice.call(card.querySelectorAll("[data-edit-bk]")).map(function (li) {
      return { id: li.dataset.editBk, arrive: li.dataset.arrive, depart: li.dataset.depart, cat: li.dataset.cat, name: li.dataset.hname };
    });
  }
  function submitEdit(b, arrive, depart) {
    // поля формы изменения связаны с ней атрибутом form= (календарь ui.js переносит id на видимое поле, поэтому ищем по name)
    function fld(name) { return document.querySelector('[name="' + name + '"][form="e' + b.id + '"]'); }
    var a = fld("arrive"), d = fld("depart"), f = document.getElementById("e" + b.id);
    if (!a || !d || !f) return;
    a.value = arrive; d.value = depart;
    var cat = fld("category");
    if (cat) cat.value = b.cat;
    f.requestSubmit ? f.requestSubmit() : f.submit();
  }
  function change(card, b, arrive, depart) {
    var was = period(b.arrive, shift(b.depart, -1)), now = period(arrive, shift(depart, -1));
    ask("Изменить бронь «" + b.name + "»: было " + was + ", станет " + now + "? Бронь по-прежнему ждёт подтверждения конюшни.", "Изменить бронь")
      .then(function (ok) { if (ok) { hint(card, "Сохраняем…"); submitEdit(b, arrive, depart); } });
  }

  cards.forEach(function (card) {
    var list = pendingOf(card);
    if (!list.length) return;
    var h = card.querySelector("[data-edit-hint]");
    if (h) h.hidden = false;
    list.forEach(function (b) {
      card.querySelectorAll('[data-night][data-bk="' + b.id + '"]').forEach(function (lab) {
        lab.classList.add("is-edit");
        lab.tabIndex = 0;
        lab.setAttribute("role", "button");
        lab.setAttribute("aria-label", lab.dataset.night + ": ночь брони, ожидающей подтверждения. Нажмите, чтобы убрать");
      });
    });
  });

  // Нажатие на ночь ожидающей брони: крайняя — убрать, единственная — предложить отмену, средняя — подсказка.
  function tapBooked(lab) {
    var card = lab.closest("[data-horse]"), b = pendingOf(card).filter(function (x) { return x.id === lab.dataset.bk; })[0];
    if (!b) return;
    var night = lab.dataset.night, last = shift(b.depart, -1);
    if (night === b.arrive && night === last) {
      ask("Это единственная ночь брони «" + b.name + "». Отменить бронь?", "Отменить бронь").then(function (ok) {
        var f = document.getElementById("x" + b.id);
        if (ok && f) f.requestSubmit ? f.requestSubmit() : f.submit();
      });
    } else if (night === b.arrive) change(card, b, shift(b.arrive, 1), b.depart);
    else if (night === last) change(card, b, b.arrive, last);
    else hint(card, "Ночи брони идут подряд — убрать можно только первую или последнюю ночь.", true);
  }
  form.addEventListener("click", function (e) {
    var lab = e.target.closest(".sb-nt.is-edit");
    if (!lab) return;
    e.preventDefault();
    tapBooked(lab);
  });
  form.addEventListener("keydown", function (e) {
    var lab = e.target.closest && e.target.closest(".sb-nt.is-edit");
    if (lab && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); tapBooked(lab); }
  });

  // Соседняя свободная ночь: добавляется к ожидающей брони (в её категории), а не создаёт отдельную бронь.
  form.addEventListener("change", function (e) {
    var inp = e.target, lab = inp.closest && inp.closest("[data-night]"), card = lab && lab.closest("[data-horse]");
    if (!card || !inp.checked || lab.dataset.bk) return;
    var night = lab.dataset.night;
    var b = pendingOf(card).filter(function (x) {
      return (night === x.depart || night === shift(x.arrive, -1)) && (" " + lab.dataset.cats + " ").indexOf(" " + x.cat + " ") >= 0;
    })[0];
    if (!b) return;
    e.stopImmediatePropagation();
    inp.checked = false;
    sync();
    if (night === b.depart) change(card, b, b.arrive, shift(b.depart, 1));
    else change(card, b, night, b.depart);
  }, true);

  cards.forEach(applyCategory);
  sync();
})();
