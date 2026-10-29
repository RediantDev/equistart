/* СтартЛист — календарь-лента/месяц (тест 5.2, вариант B: макеты R3_01B1/B2, R3_03B).
   Разметка — templates/partials/cal.html. Без перезагрузки: листание месяцев и недель, выбор даты.
   - при открытии раздела календарь развёрнут (месяц); если пользователь свернул — свёрнут до конца сессии
     (sessionStorage, ключ data-cal-key);
   - точки на датах: одна — один турнир, две — два и больше; у «Моих соревнований» прошедшие — кольцом;
   - «сегодня» обведено; нажатие на дату — событие "cal:select" { date: "ГГГГ-ММ-ДД" | null }. */
(function () {
  "use strict";
  var MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];
  var GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function iso(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
  function parse(s) { var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || ""); return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null; }
  function addDays(d, n) { return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n); }
  function monday(d) { return addDays(d, -((d.getDay() + 6) % 7)); }
  function store(key, v) { try { if (v === undefined) return sessionStorage.getItem(key); sessionStorage.setItem(key, v); } catch (e) { return null; } return null; }
  function plural(n) { var a = n % 10, b = n % 100; return a === 1 && b !== 11 ? "турнир" : a >= 2 && a <= 4 && (b < 12 || b > 14) ? "турнира" : "турниров"; }

  // Данные: {"ГГГГ-ММ-ДД": число} или {"upcoming": [...], "past": [...]}
  function load(cal) {
    var out = {}, el = document.getElementById(cal.getAttribute("data-cal-src") || "");
    var raw = {};
    try { raw = el ? JSON.parse(el.textContent) : {}; } catch (e) { raw = {}; }
    if (raw && (Array.isArray(raw.upcoming) || Array.isArray(raw.past))) {
      (raw.upcoming || []).forEach(function (d) { out[d] = { n: 1, kind: "up" }; });
      (raw.past || []).forEach(function (d) { if (!out[d]) out[d] = { n: 1, kind: "past" }; });
    } else Object.keys(raw || {}).forEach(function (d) { out[d] = { n: +raw[d] || 1, kind: "" }; });
    return out;
  }

  function init(cal) {
    if (cal._cal) return cal._cal;
    var key = "sl-cal:" + (cal.getAttribute("data-cal-key") || "main");
    var grid = cal.querySelector("[data-cal-grid]"), title = cal.querySelector(".cal__title");
    var toggle = cal.querySelector("[data-cal-toggle]"), toggleText = cal.querySelector("[data-cal-toggle-text]");
    var today = parse(cal.getAttribute("data-cal-today")) || new Date();
    today = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    var data = load(cal);
    var sel = cal.getAttribute("data-cal-selected") || null;
    var start = parse(cal.getAttribute("data-cal-start")) || parse(sel) || today;
    var mode = store(key) === "week" ? "week" : "month";
    var view = mode === "week" ? monday(start) : new Date(start.getFullYear(), start.getMonth(), 1);

    function title_() {
      if (mode === "month") return MONTHS[view.getMonth()] + " " + view.getFullYear();
      var end = addDays(view, 6);
      if (view.getMonth() === end.getMonth()) return view.getDate() + "–" + end.getDate() + " " + GEN[end.getMonth()];
      return view.getDate() + " " + GEN[view.getMonth()] + " – " + end.getDate() + " " + GEN[end.getMonth()];
    }
    function render() {
      cal.classList.toggle("is-week", mode === "week");
      cal.setAttribute("aria-label", (cal.getAttribute("aria-label") || "Календарь").split(",")[0] + ", " + (mode === "week" ? "неделя" : "месяц"));
      title.textContent = title_();
      toggleText.textContent = mode === "week" ? "Месяц" : "Неделя";
      toggle.setAttribute("aria-expanded", mode === "month" ? "true" : "false");
      cal.querySelector("[data-cal-prev]").setAttribute("aria-label", mode === "week" ? "Предыдущая неделя" : "Предыдущий месяц");
      cal.querySelector("[data-cal-next]").setAttribute("aria-label", mode === "week" ? "Следующая неделя" : "Следующий месяц");
      var first = mode === "week" ? view : monday(view), count = mode === "week" ? 7 : 42;
      var tIso = iso(today);  // тест 5.3, п. 10: подсвечивается только выбранный день, не неделя
      var frag = document.createDocumentFragment();
      for (var i = 0; i < count; i++) {
        var d = addDays(first, i), k = iso(d), info = data[k];
        var b = document.createElement("button");
        b.type = "button"; b.className = "cal__d"; b.setAttribute("data-date", k);
        b.appendChild(document.createTextNode(String(d.getDate())));
        var label = d.getDate() + " " + GEN[d.getMonth()];
        if (mode === "month" && d.getMonth() !== view.getMonth()) b.classList.add("is-out");
        if ((d.getDay() + 6) % 7 > 4) b.classList.add("is-we");
        if (k === tIso) { b.classList.add("is-today"); label += ", сегодня"; }
        if (info) {
          var dot = document.createElement("i");
          dot.className = "cal__dot" + (info.n > 1 ? " cal__dot--2" : "") + (info.kind === "past" ? " cal__dot--past" : "");
          dot.setAttribute("aria-hidden", "true");
          b.appendChild(dot);
          b.classList.add("has-ev");
          if (info.kind === "past") b.classList.add("is-past-ev");
          label += info.kind ? (info.kind === "past" ? ", прошедшее соревнование" : ", есть соревнование") : ", " + info.n + " " + plural(info.n);
        }
        if (k === sel) { b.classList.add("is-sel"); b.setAttribute("aria-pressed", "true"); label += ", выбрано"; }
        else b.setAttribute("aria-pressed", "false");
        b.setAttribute("aria-label", label);
        frag.appendChild(b);
      }
      grid.innerHTML = "";
      grid.appendChild(frag);
    }
    function select(d, silent) {
      sel = d || null;
      if (sel) cal.setAttribute("data-cal-selected", sel); else cal.removeAttribute("data-cal-selected");
      var sd = parse(sel);
      if (sd && mode === "month" && (sd.getMonth() !== view.getMonth() || sd.getFullYear() !== view.getFullYear())) view = new Date(sd.getFullYear(), sd.getMonth(), 1);
      render();
      if (!silent) cal.dispatchEvent(new CustomEvent("cal:select", { bubbles: true, detail: { date: sel, kind: sel && data[sel] ? data[sel].kind : "" } }));
    }
    cal.addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (!b || !cal.contains(b)) return;
      if (b.hasAttribute("data-cal-prev") || b.hasAttribute("data-cal-next")) {
        var s = b.hasAttribute("data-cal-next") ? 1 : -1;
        view = mode === "week" ? addDays(view, 7 * s) : new Date(view.getFullYear(), view.getMonth() + s, 1);
        render();
      } else if (b.hasAttribute("data-cal-toggle")) {
        if (mode === "month") {
          mode = "week";
          var sd = parse(sel), inView = function (d) { return d && d.getMonth() === view.getMonth() && d.getFullYear() === view.getFullYear(); };
          view = monday(inView(sd) ? sd : inView(today) ? today : view);
        } else {
          mode = "month";
          var anchor = parse(sel) && iso(monday(parse(sel))) === iso(view) ? parse(sel) : addDays(view, 3);
          view = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
        }
        store(key, mode);
        render();
      } else if (b.hasAttribute("data-date")) {
        var k = b.getAttribute("data-date");
        select(k === sel ? null : k);
      }
    });
    // Листание жестом по ленте/сетке
    var tx = null;
    grid.addEventListener("touchstart", function (e) { tx = e.touches.length === 1 ? [e.touches[0].clientX, e.touches[0].clientY] : null; }, { passive: true });
    grid.addEventListener("touchend", function (e) {
      if (!tx) return;
      var dx = e.changedTouches[0].clientX - tx[0], dy = e.changedTouches[0].clientY - tx[1];
      tx = null;
      if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) cal.querySelector(dx < 0 ? "[data-cal-next]" : "[data-cal-prev]").click();
    }, { passive: true });
    render();
    cal._cal = { select: select, get: function () { return sel; }, reload: function () { data = load(cal); render(); } };
    return cal._cal;
  }

  function initAll(scope) { (scope || document).querySelectorAll("[data-cal]").forEach(init); }
  window.SLCal = { init: init, initAll: initAll };
  document.addEventListener("DOMContentLoaded", function () { initAll(document); });
  document.addEventListener("htmx:afterSettle", function (e) { initAll(e.target && e.target.querySelectorAll ? e.target : document); });

  /* ---------- Календарь соревнований: дата — список турниров этого дня без перезагрузки ---------- */
  function mainCal() { return document.querySelector('[data-cal][data-cal-key="cal-main"]'); }
  function dayUrl(date) {
    var u = new URL(location.href);
    if (date) u.searchParams.set("day", date); else u.searchParams.delete("day");
    return u.pathname + (u.searchParams.toString() ? "?" + u.searchParams.toString() : "");
  }
  document.addEventListener("cal:select", function (e) {
    var cal = e.target;
    if (cal.getAttribute("data-cal-key") !== "cal-main") return;
    var url = dayUrl(e.detail.date);
    // День сохраняется и при поиске
    var form = document.querySelector("form.cal-search"), hid = form && form.querySelector("input[name=day]");
    if (form) {
      if (e.detail.date) { if (!hid) { hid = document.createElement("input"); hid.type = "hidden"; hid.name = "day"; form.appendChild(hid); } hid.value = e.detail.date; }
      else if (hid) hid.remove();
    }
    try { history.replaceState(history.state, "", url); } catch (err) {}
    if (window.htmx) htmx.ajax("GET", url, { target: "#cal-list", select: "#cal-list", swap: "outerHTML" });
    else location.href = url;
  });
  // Поиск и фильтры по мере ввода (smooth.js, data-live) — точки на датах обновляются
  document.addEventListener("live:updated", function () { var cal = mainCal(); if (cal && cal._cal) cal._cal.reload(); });
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("[data-cal-all]");
    var cal = mainCal();
    if (!a || !cal || !cal._cal) return;
    e.preventDefault();
    cal._cal.select(null);
  });
})();
