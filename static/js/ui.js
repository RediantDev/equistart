/* СтартЛист — общие компоненты интерфейса (тест 4, 23.10.2026).
   Прогрессивное улучшение: без JavaScript работают обычные поля браузера.
   - выпадающие списки, календарь и выбор времени в стиле сайта;
   - кнопка выбора файла;
   - пошаговые формы (регистрация);
   - условный показ полей (data-show-when);
   - кнопка «Сохранить» неактивна, пока нет изменений (data-dirty-guard);
   - после отправки формы страница остаётся на том же месте (без прыжков вверх/вниз);
   - подтверждение опасных действий (data-confirm). */
(function () {
  "use strict";

  var MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
  var MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  var WEEK = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  // Телефон: календарь и списки открываются без клавиатуры (тест 5, п. 13.1, 21.3)
  var TOUCH = window.matchMedia && matchMedia("(pointer: coarse)").matches;
  function focusSoft(x) { if (!TOUCH && x) x.focus(); }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function fire(target, type) { target.dispatchEvent(new Event(type, { bubbles: true })); }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function iso(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
  function parseIso(v) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v || "");
    return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
  }
  function parseRu(v) {
    var m = /^\s*(\d{1,2})[.\/\- ](\d{1,2})[.\/\- ](\d{2,4})\s*$/.exec(v || "");
    if (!m) return null;
    var y = +m[3]; if (y < 100) y += 2000;
    var d = new Date(y, +m[2] - 1, +m[1]);
    return d.getMonth() === +m[2] - 1 ? d : null;
  }
  function human(d) { return d.getDate() + " " + MONTHS_GEN[d.getMonth()] + " " + d.getFullYear(); }

  var openPopup = null;
  function closePopup() {
    if (openPopup) { openPopup.close(); openPopup = null; }
  }
  document.addEventListener("mousedown", function (e) {
    if (openPopup && !openPopup.root.contains(e.target) && !(openPopup.host && openPopup.host.contains(e.target))) closePopup();
  });

  /* Тест 5.2, п. 58, 60, 63: список и календарь не уходят за экран.
     Телефон, а также поле внутри шторки/окна (контейнер обрезает содержимое) — отдельной шторкой снизу (.uisheet);
     компьютер — выпадающей панелью вниз или вверх, где больше места, высота списка — по доступному месту. */
  function asSheet(root) {
    if (window.innerWidth < 720) return true;
    for (var n = root.parentElement; n && n !== document.body; n = n.parentElement) {
      var cs = getComputedStyle(n);
      if (cs.position === "fixed" || /(auto|scroll|hidden)/.test(cs.overflowY + cs.overflowX)) return true;
    }
    return false;
  }
  function fieldTitle(root) {
    var ctl = root.querySelector("[id]"), l = ctl ? document.querySelector('label[for="' + ctl.id + '"]') : null;
    var f = root.closest(".field, .ps-f");
    if (!l && f) l = f.querySelector("label, .field__label, .ps-f__l, legend");
    return l ? l.textContent.replace(/\s*(необязательно|обязательно)\s*$/i, "").trim() : "";
  }
  // Свободное место над и под элементом: без шапки сверху и нижнего меню/закреплённых панелей снизу
  function room(r) {
    var vv = window.visualViewport, h = vv ? vv.height : window.innerHeight;
    var bar = document.querySelector(".topbar"), top = bar ? Math.max(0, bar.getBoundingClientRect().bottom) : 0;
    var bn = document.querySelector(".bnav"), bottom = h;
    if (bn && getComputedStyle(bn).display !== "none") bottom = Math.min(bottom, bn.getBoundingClientRect().top);
    return { below: bottom - r.bottom - 12, above: r.top - top - 12 };
  }
  function openSheet(pop, title, onClose) {
    var back = el("div", "uisheet"), box = el("div", "uisheet__box");
    box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true");
    if (title) box.setAttribute("aria-label", title);
    box.appendChild(el("div", "sheet__grip"));
    var head = el("div", "uisheet__head"), x = el("button", "uisheet__x", "×");
    x.type = "button"; x.setAttribute("aria-label", "Закрыть"); x.addEventListener("click", onClose);
    head.appendChild(el("span", "uisheet__title", title || "Выберите")); head.appendChild(x);
    box.appendChild(head); box.appendChild(pop); back.appendChild(box);
    document.body.appendChild(back);
    back.addEventListener("click", function (e) { if (e.target === back) onClose(); });
    // Прокручивается только список внутри шторки, страница под ней — нет
    back.addEventListener("touchmove", function (e) {
      var sc = e.target.closest && e.target.closest(".sel__list");
      if (!sc || sc.scrollHeight <= sc.clientHeight) e.preventDefault();
    }, { passive: false });
    requestAnimationFrame(function () { back.classList.add("is-open"); });
    return { back: back, box: box };
  }
  function closeSheet(sh, pop, root) {
    if (!sh) return;
    root.appendChild(pop);
    sh.back.classList.remove("is-open");
    setTimeout(function () { sh.back.remove(); }, 180);
  }
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closePopup(); });

  function placeholderFor(input) {
    var f = input.closest(".field");
    var optional = f && f.querySelector(".field__optional");
    return input.getAttribute("placeholder") || input.getAttribute("data-placeholder") || (optional ? "Необязательно" : "");
  }

  /* ---------- Выпадающий список ---------- */
  function enhanceSelect(sel) {
    if (sel.dataset.ui || sel.multiple || sel.hasAttribute("data-native") || sel.size > 1) return;
    sel.dataset.ui = "1";
    var root = el("div", "sel");
    if (sel.closest(".inline-row, .order-ctl, .bulk-bar, .tpl-picker, .chips") || sel.style.width === "auto") root.classList.add("sel--inline");
    sel.parentNode.insertBefore(root, sel);
    root.appendChild(sel);
    sel.classList.add("sel__native");
    sel.tabIndex = -1;
    var btn = el("button", "sel__btn");
    btn.type = "button";
    btn.setAttribute("aria-haspopup", "listbox");
    if (sel.id) {
      var lab = document.querySelector('label[for="' + sel.id + '"]');
      if (lab) { lab.addEventListener("click", function (e) { e.preventDefault(); btn.focus(); }); btn.setAttribute("aria-label", lab.textContent.trim()); }
    } else if (sel.getAttribute("aria-label")) btn.setAttribute("aria-label", sel.getAttribute("aria-label"));
    var label = el("span", "sel__value");
    btn.appendChild(label);
    btn.appendChild(el("span", "sel__chevron"));
    root.appendChild(btn);
    var pop = el("div", "sel__pop");
    pop.hidden = true;
    root.appendChild(pop);
    var active = -1, items = [], filter = null;

    function sync() {
      var o = sel.options[sel.selectedIndex];
      label.textContent = o ? o.textContent : "";
      root.classList.toggle("is-empty", !o || o.value === "");
      btn.disabled = sel.disabled;
      root.classList.toggle("field--error-ctl", !!sel.closest(".field--error"));
    }
    function build() {
      pop.innerHTML = "";
      items = [];
      if (sel.options.length > 10) {
        filter = el("input", "sel__filter");
        filter.type = "search";
        filter.placeholder = "Найти…";
        filter.setAttribute("aria-label", "Поиск по списку");
        filter.addEventListener("input", function () {
          var q = filter.value.trim().toLowerCase();
          items.forEach(function (li) { li.hidden = q && li.textContent.toLowerCase().indexOf(q) < 0; });
          active = -1;
        });
        filter.addEventListener("keydown", keys);
        pop.appendChild(filter);
      } else filter = null;
      var ul = el("ul", "sel__list");
      ul.setAttribute("role", "listbox");
      Array.prototype.forEach.call(sel.options, function (o, i) {
        var li = el("li", "sel__opt", o.textContent);
        li.setAttribute("role", "option");
        if (o.disabled) li.classList.add("is-disabled");
        if (i === sel.selectedIndex) { li.classList.add("is-selected"); li.setAttribute("aria-selected", "true"); }
        if (o.value === "") li.classList.add("is-placeholder");
        li.addEventListener("mousedown", function (e) { e.preventDefault(); });
        li.addEventListener("click", function () { if (!o.disabled) choose(i); });
        li.dataset.i = i;
        items.push(li);
        ul.appendChild(li);
      });
      pop.appendChild(ul);
    }
    function choose(i) {
      var changed = sel.selectedIndex !== i;
      sel.selectedIndex = i;
      sync();
      close();
      btn.focus();
      if (changed) fire(sel, "change");
    }
    function highlight(i) {
      var vis = items.filter(function (li) { return !li.hidden; });
      if (!vis.length) return;
      i = Math.max(0, Math.min(vis.length - 1, i));
      items.forEach(function (li) { li.classList.remove("is-active"); });
      vis[i].classList.add("is-active");
      var ul = vis[i].parentNode, o = vis[i].offsetTop - ul.offsetTop;  // прокручиваем только список, не страницу
      if (o < ul.scrollTop) ul.scrollTop = o;
      else if (o + vis[i].offsetHeight > ul.scrollTop + ul.clientHeight) ul.scrollTop = o + vis[i].offsetHeight - ul.clientHeight;
      active = i;
    }
    var sheet = null;
    function open() {
      if (btn.disabled) return;
      closePopup();
      build();
      pop.hidden = false;
      root.classList.add("is-open");
      var list = pop.querySelector(".sel__list");
      if (asSheet(root)) {
        sheet = openSheet(pop, fieldTitle(root) || btn.getAttribute("aria-label") || "", close);
        list.style.maxHeight = "";
      } else {
        var r = btn.getBoundingClientRect(), sp = room(r);
        var extra = (filter ? 52 : 0) + 14;  // поиск и отступы панели
        var want = Math.min(360, list.scrollHeight) + extra;
        var up = sp.below < want && sp.above > sp.below;
        root.classList.toggle("is-up", up);
        list.style.maxHeight = Math.max(140, Math.min(360, (up ? sp.above : sp.below) - extra)) + "px";
      }
      openPopup = { root: root, close: close, host: sheet && sheet.box };
      var vis = items.filter(function (li) { return !li.hidden; });
      highlight(Math.max(0, vis.indexOf(items[sel.selectedIndex])));
      if (filter) focusSoft(filter);
    }
    function close() {
      pop.hidden = true;
      root.classList.remove("is-open", "is-up");
      if (sheet) { closeSheet(sheet, pop, root); sheet = null; }
      if (openPopup && openPopup.root === root) openPopup = null;
    }
    function keys(e) {
      var isOpen = !pop.hidden;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!isOpen) { open(); return; }
        highlight(active + (e.key === "ArrowDown" ? 1 : -1));
      } else if (e.key === "Enter" || (e.key === " " && e.target === btn)) {
        e.preventDefault();
        if (!isOpen) { open(); return; }
        var vis = items.filter(function (li) { return !li.hidden; });
        if (vis[active]) choose(+vis[active].dataset.i);
      } else if (e.key === "Tab") close();
    }
    btn.addEventListener("click", function () { if (pop.hidden) open(); else close(); });
    btn.addEventListener("keydown", keys);
    sel.addEventListener("change", sync);
    new MutationObserver(sync).observe(sel, { childList: true, attributes: true, attributeFilter: ["disabled"] });
    sync();
  }

  /* ---------- Календарь ---------- */
  function enhanceDate(input) {
    if (input.dataset.ui) return;
    input.dataset.ui = "1";
    var min = parseIso(input.min), max = parseIso(input.max);
    var root = el("div", "dp");
    input.parentNode.insertBefore(root, input);
    var vis = el("input", "dp__input");
    vis.type = "text";
    vis.autocomplete = "off";
    // Тест 5.2, п. 60: на телефоне дата выбирается только календарём — без клавиатуры
    var typing = !TOUCH && window.innerWidth >= 720;
    vis.inputMode = typing ? "numeric" : "none";
    if (!typing) vis.readOnly = true;
    if (input.id) { vis.id = input.id; input.removeAttribute("id"); }
    vis.placeholder = placeholderFor(input) || "выберите дату";
    if (input.getAttribute("aria-describedby")) vis.setAttribute("aria-describedby", input.getAttribute("aria-describedby"));
    input.type = "hidden";
    root.appendChild(vis);
    root.appendChild(input);
    var icon = el("button", "dp__icon");
    icon.type = "button";
    icon.tabIndex = -1;
    icon.setAttribute("aria-label", "Открыть календарь");
    root.appendChild(icon);
    var pop = el("div", "dp__pop");
    pop.hidden = true;
    root.appendChild(pop);
    var view;

    function show() { var d = parseIso(input.value); vis.value = d ? human(d) : ""; }
    function set(d) {
      var v = d ? iso(d) : "";
      if (v !== input.value) { input.value = v; fire(input, "change"); fire(input, "input"); }
      show();
    }
    function render() {
      pop.innerHTML = "";
      var head = el("div", "dp__head");
      var prev = el("button", "dp__nav", "‹"); prev.type = "button"; prev.setAttribute("aria-label", "Предыдущий месяц");
      var next = el("button", "dp__nav", "›"); next.type = "button"; next.setAttribute("aria-label", "Следующий месяц");
      // Тест 5.1, п. 22.7: месяц и год выбираются списком (на телефоне — системное колесо), а не листанием
      var title = el("div", "dp__title");
      var ms = el("select", "dp__sel"); ms.setAttribute("data-native", ""); ms.setAttribute("aria-label", "Месяц");
      MONTHS.forEach(function (m, i) { var o = el("option", null, m); o.value = i; if (i === view.getMonth()) o.selected = true; ms.appendChild(o); });
      var ys = el("select", "dp__sel"); ys.setAttribute("data-native", ""); ys.setAttribute("aria-label", "Год");
      var yNow = new Date().getFullYear(), yMin = min ? min.getFullYear() : yNow - 90, yMax = max ? max.getFullYear() : yNow + 3;
      for (var yy = yMax; yy >= yMin; yy--) { var oy = el("option", null, String(yy)); oy.value = yy; if (yy === view.getFullYear()) oy.selected = true; ys.appendChild(oy); }
      if (view.getFullYear() > yMax || view.getFullYear() < yMin) { var ox = el("option", null, String(view.getFullYear())); ox.value = view.getFullYear(); ox.selected = true; ys.appendChild(ox); }
      ms.onchange = function () { view = new Date(view.getFullYear(), +ms.value, 1); render(); };
      ys.onchange = function () { view = new Date(+ys.value, view.getMonth(), 1); render(); };
      ms.addEventListener("mousedown", function (e) { e.stopPropagation(); });
      ys.addEventListener("mousedown", function (e) { e.stopPropagation(); });
      title.appendChild(ms); title.appendChild(ys);
      prev.onclick = function () { view = new Date(view.getFullYear(), view.getMonth() - 1, 1); render(); };
      next.onclick = function () { view = new Date(view.getFullYear(), view.getMonth() + 1, 1); render(); };
      head.appendChild(prev); head.appendChild(title); head.appendChild(next);
      pop.appendChild(head);
      var grid = el("div", "dp__grid");
      WEEK.forEach(function (w, i) { grid.appendChild(el("span", "dp__wd" + (i > 4 ? " is-weekend" : ""), w)); });
      var first = new Date(view.getFullYear(), view.getMonth(), 1);
      var shift = (first.getDay() + 6) % 7;
      for (var i = 0; i < shift; i++) grid.appendChild(el("span", "dp__blank"));
      var days = new Date(view.getFullYear(), view.getMonth() + 1, 0).getDate();
      var sel = parseIso(input.value), today = new Date();
      for (var d = 1; d <= days; d++) {
        (function (day) {
          var date = new Date(view.getFullYear(), view.getMonth(), day);
          var b = el("button", "dp__day", String(day));
          b.type = "button";
          if (sel && iso(sel) === iso(date)) b.classList.add("is-selected");
          if (iso(today) === iso(date)) b.classList.add("is-today");
          if ((date.getDay() + 6) % 7 > 4) b.classList.add("is-weekend");
          if ((min && date < min) || (max && date > max)) b.disabled = true;
          b.onclick = function () { set(date); close(); focusSoft(vis); };
          grid.appendChild(b);
        })(d);
      }
      pop.appendChild(grid);
      var foot = el("div", "dp__foot");
      var todayBtn = el("button", "dp__link", "Сегодня"); todayBtn.type = "button";
      todayBtn.onclick = function () { set(new Date(today.getFullYear(), today.getMonth(), today.getDate())); close(); };
      foot.appendChild(todayBtn);
      if (!input.required) {
        var clear = el("button", "dp__link", "Очистить"); clear.type = "button";
        clear.onclick = function () { set(null); close(); };
        foot.appendChild(clear);
      }
      pop.appendChild(foot);
    }
    var sheet = null;
    function open() {
      if (!pop.hidden) return;
      closePopup();
      view = parseIso(input.value) || new Date();
      view = new Date(view.getFullYear(), view.getMonth(), 1);
      render();
      pop.hidden = false;
      root.classList.add("is-open");
      if (asSheet(root)) {
        if (document.activeElement === vis) vis.blur();
        sheet = openSheet(pop, fieldTitle(root) || "Дата", close);
      } else {
        // Панель — в пределах экрана: вправо/влево и вверх/вниз по свободному месту
        var r = root.getBoundingClientRect(), sp = room(r), w = pop.offsetWidth || 304, h = pop.offsetHeight || 380;
        root.classList.toggle("is-right", r.left + w > document.documentElement.clientWidth - 8 && r.right - w >= 8);
        root.classList.toggle("is-up", sp.below < h && sp.above > sp.below);
      }
      openPopup = { root: root, close: close, host: sheet && sheet.box };
    }
    function close() {
      pop.hidden = true;
      root.classList.remove("is-open", "is-up", "is-right");
      if (sheet) { closeSheet(sheet, pop, root); sheet = null; }
      if (openPopup && openPopup.root === root) openPopup = null;
    }
    vis.addEventListener("click", open);
    icon.addEventListener("click", function () { if (pop.hidden) { open(); if (!sheet) focusSoft(vis); } else close(); });
    vis.addEventListener("keydown", function (e) { if (e.key === "ArrowDown") { e.preventDefault(); open(); } if (e.key === "Enter") { e.preventDefault(); vis.blur(); } });
    vis.addEventListener("focus", function () { if (!typing) return; var d = parseIso(input.value); if (d) vis.value = pad(d.getDate()) + "." + pad(d.getMonth() + 1) + "." + d.getFullYear(); });
    // Тест 5.1, п. 22.6: при вводе с клавиатуры точки ставятся сами — дд.мм.гггг
    if (typing) vis.placeholder = vis.placeholder === "Необязательно" || !vis.placeholder ? "дд.мм.гггг" : vis.placeholder;
    else if (vis.placeholder === "Необязательно") vis.placeholder = "выберите дату";
    vis.addEventListener("input", function (e) {
      if (e.inputType && e.inputType.indexOf("delete") === 0) return;
      var d = vis.value.replace(/\D/g, "").slice(0, 8), out = d.slice(0, 2);
      if (d.length > 2) out += "." + d.slice(2, 4); else if (d.length === 2) out += ".";
      if (d.length > 4) out += "." + d.slice(4); else if (d.length === 4) out += ".";
      vis.value = out;
    });
    vis.addEventListener("blur", function () {
      if (!typing) return;
      var raw = vis.value.trim();
      if (!raw) { set(null); return; }
      var d = parseRu(raw);
      if (d) set(d); else show();
    });
    show();
  }

  /* ---------- Выбор времени ---------- */
  function enhanceTime(input) {
    if (input.dataset.ui) return;
    input.dataset.ui = "1";
    var root = el("div", "tp");
    input.parentNode.insertBefore(root, input);
    var vis = el("input", "tp__input");
    vis.type = "text";
    vis.autocomplete = "off";
    vis.inputMode = "numeric";
    vis.placeholder = placeholderFor(input) || "чч:мм";
    // Тест 6, п. 13: на телефоне время выбирается только в окне — клавиатура не открывается и не закрывает окно
    var touch = TOUCH || window.innerWidth < 720;
    if (touch) { vis.readOnly = true; vis.inputMode = "none"; vis.classList.add("tp__input--pick"); }
    if (input.id) { vis.id = input.id; input.removeAttribute("id"); }
    input.type = "hidden";
    root.appendChild(vis);
    root.appendChild(input);
    var icon = el("button", "tp__icon"); icon.type = "button"; icon.tabIndex = -1; icon.setAttribute("aria-label", "Выбрать время");
    root.appendChild(icon);
    var pop = el("div", "tp__pop"); pop.hidden = true; root.appendChild(pop);

    function cur() { var m = /^(\d{1,2}):(\d{2})/.exec(input.value || ""); return m ? [+m[1], +m[2]] : null; }
    function show() { var c = cur(); vis.value = c ? pad(c[0]) + ":" + pad(c[1]) : ""; }
    function set(h, m) {
      var v = h === null ? "" : pad(h) + ":" + pad(m);
      if (v !== input.value) { input.value = v; fire(input, "change"); }
      show();
    }
    function render() {
      pop.innerHTML = "";
      var c = cur() || [null, null];
      var cols = el("div", "tp__cols");
      var hc = el("div", "tp__col"), mc = el("div", "tp__col");
      hc.appendChild(el("div", "tp__cap", "Часы")); mc.appendChild(el("div", "tp__cap", "Минуты"));
      var hg = el("div", "tp__grid tp__grid--h"), mg = el("div", "tp__grid tp__grid--m");
      for (var h = 6; h < 24; h++) (function (h) {
        var b = el("button", "tp__cell", pad(h)); b.type = "button";
        if (c[0] === h) b.classList.add("is-selected");
        b.onclick = function () { set(h, c[1] === null ? 0 : c[1]); render(); };
        hg.appendChild(b);
      })(h);
      for (var m = 0; m < 60; m += 5) (function (m) {
        var b = el("button", "tp__cell", pad(m)); b.type = "button";
        if (c[1] === m) b.classList.add("is-selected");
        b.onclick = function () { set(c[0] === null ? 10 : c[0], m); close(); if (!touch) focusSoft(vis); };
        mg.appendChild(b);
      })(m);
      hc.appendChild(hg); mc.appendChild(mg);
      cols.appendChild(hc); cols.appendChild(mc);
      pop.appendChild(cols);
      var foot = el("div", "dp__foot");
      if (!touch) foot.appendChild(el("span", "tp__hint", "Можно ввести вручную, например 9:40"));
      else { var ok = el("button", "btn btn--primary btn--sm", "Готово"); ok.type = "button"; ok.onclick = function () { close(); }; foot.appendChild(ok); }
      if (!input.required) { var clear = el("button", "dp__link", "Очистить"); clear.type = "button"; clear.onclick = function () { set(null); close(); }; foot.appendChild(clear); }
      pop.appendChild(foot);
    }
    var sheet = null;
    function open() {
      if (!pop.hidden) return;
      closePopup(); render(); pop.hidden = false; root.classList.add("is-open");
      if (asSheet(root)) sheet = openSheet(pop, fieldTitle(root) || "Время", close);
      else {
        var r = root.getBoundingClientRect(), sp = room(r);
        root.classList.toggle("is-right", r.left + pop.offsetWidth > document.documentElement.clientWidth - 8 && r.right - pop.offsetWidth >= 8);
        root.classList.toggle("is-up", sp.below < pop.offsetHeight && sp.above > sp.below);
      }
      openPopup = { root: root, close: close, host: sheet && sheet.box };
    }
    function close() {
      pop.hidden = true; root.classList.remove("is-open", "is-up", "is-right");
      if (sheet) { closeSheet(sheet, pop, root); sheet = null; }
      if (openPopup && openPopup.root === root) openPopup = null;
    }
    vis.addEventListener("click", open);
    vis.addEventListener("focus", function () { if (touch) { open(); return; } setTimeout(function () { vis.select(); }, 0); });  // ввод заменяет «23:59», а не дописывается
    icon.addEventListener("click", function () { if (pop.hidden) { open(); focusSoft(vis); } else close(); });
    vis.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); vis.blur(); close(); } });
    vis.addEventListener("blur", function () {
      var raw = vis.value.trim().replace(/[.,\-]/, ":");
      if (!raw) { set(null); return; }
      var m = /^(\d{1,2}):?(\d{2})$/.exec(raw);
      if (m && +m[1] < 24 && +m[2] < 60) set(+m[1], +m[2]); else show();
    });
    show();
  }

  /* ---------- Выбор файла ---------- */
  // Тест 5, п. 16.1, 24.3–24.5: проверка формата по полю accept (не только PDF), понятная ошибка,
  // фото — миниатюрой с крестиком, несколько файлов — несколько миниатюр.
  function acceptOk(f, accept) {
    if (!accept) return true;
    var name = f.name.toLowerCase(), type = (f.type || "").toLowerCase();
    return accept.split(",").some(function (a) {
      a = a.trim().toLowerCase();
      if (!a) return false;
      if (a.charAt(0) === ".") return name.slice(-a.length) === a;
      if (/\/\*$/.test(a)) return type.indexOf(a.slice(0, -1)) === 0;
      return type === a;
    });
  }
  function acceptHuman(accept) {
    var map = { "application/pdf": "PDF", ".pdf": "PDF", "image/jpeg": "JPEG", ".jpg": "JPEG", ".jpeg": "JPEG", "image/png": "PNG", ".png": "PNG", "image/webp": "WEBP", ".webp": "WEBP", "image/heic": "HEIC", ".heic": "HEIC", "image/*": "фото" };
    var out = [];
    accept.split(",").forEach(function (a) { var h = map[a.trim().toLowerCase()]; if (h && out.indexOf(h) < 0) out.push(h); });
    return out.join(", ");
  }
  // Тест 6, пп. 5, 20, 21: выбор файла — плитка «+». Фото — плитки в сетке, файл — карточка
  // с именем, размером и корзиной. data-autosubmit — отправить форму сразу после выбора.
  function enhanceFile(input) {
    if (input.dataset.ui) return;
    input.dataset.ui = "1";
    var imgOnly = /^(image\/|\.(jpe?g|png|webp|heic))/i.test((input.accept || "").trim()) && !/pdf/i.test(input.accept || "");
    var root = el("div", "file " + (imgOnly ? "file--tiles" : "file--doc"));
    input.parentNode.insertBefore(root, input);
    root.appendChild(input);
    input.classList.add("file__native");
    if (!input.id) input.id = "f" + Math.random().toString(36).slice(2);
    var label = input.getAttribute("data-plus-label") || (imgOnly ? "Фото" : (/pdf/i.test(input.accept || "") ? "Файл PDF" : "Файл"));
    var thumbs = el("div", "file__thumbs"); thumbs.hidden = true;
    var plus = el("label", "file__plus");
    plus.setAttribute("for", input.id);
    plus.innerHTML = '<span class="file__plus-ico" aria-hidden="true">+</span>';
    plus.appendChild(el("span", "file__plus-text", label));
    var card = el("div", "file__card"); card.hidden = true;
    var cardIco = el("span", "file__card-ico", "PDF"); cardIco.setAttribute("aria-hidden", "true");
    var cardTxt = el("span", "file__card-txt");
    var name = el("span", "file__name"), meta = el("span", "file__meta");
    cardTxt.appendChild(name); cardTxt.appendChild(meta);
    var clear = el("button", "file__clear"); clear.type = "button"; clear.setAttribute("aria-label", "Убрать файл");
    clear.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>';
    card.appendChild(cardIco); card.appendChild(cardTxt); card.appendChild(clear);
    if (imgOnly) { thumbs.appendChild(plus); root.appendChild(thumbs); thumbs.hidden = false; }
    else { root.appendChild(plus); root.appendChild(card); }
    var err = el("p", "field__error"); err.hidden = true; err.setAttribute("role", "alert"); root.appendChild(err);
    function size(f) { return f.size < 1048576 ? Math.max(1, Math.round(f.size / 1024)) + " КБ" : (f.size / 1048576).toFixed(1).replace(".", ",") + " МБ"; }
    function removeAt(idx) {
      try {
        var dt = new DataTransfer();
        Array.prototype.forEach.call(input.files, function (f, i) { if (i !== idx) dt.items.add(f); });
        input.files = dt.files;
      } catch (e) { input.value = ""; }
      fire(input, "change");
    }
    function sync() {
      var files = Array.prototype.slice.call(input.files || []);
      var maxMb = +input.getAttribute("data-max-mb") || 0, bad = "";
      files.forEach(function (f) {
        if (bad) return;
        if (!acceptOk(f, input.accept)) bad = "«" + f.name + "» — неподходящий формат." + (acceptHuman(input.accept || "") ? " Подходят: " + acceptHuman(input.accept) + "." : "");
        else if (maxMb && f.size > maxMb * 1048576) bad = "«" + f.name + "» больше " + maxMb + " МБ — уменьшите файл и выберите снова.";
      });
      err.innerHTML = bad ? '<span aria-hidden="true">!</span> ' : ""; err.appendChild(document.createTextNode(bad)); err.hidden = !bad;
      var f0 = root.closest(".field"); if (f0) f0.classList.toggle("field--error", !!bad);
      if (bad) { input.value = ""; files = []; if (window.SL && SL.toast) SL.toast(bad, "error"); }
      root.classList.toggle("has-file", files.length > 0);
      if (imgOnly) {
        thumbs.querySelectorAll(".file__thumb").forEach(function (t) { t.remove(); });
        files.forEach(function (f, i) {
          var t = el("div", "file__thumb");
          if (/^image\//.test(f.type) && !/heic/i.test(f.type)) {
            var im = el("img"); im.alt = f.name; im.src = URL.createObjectURL(f); im.onload = function () { URL.revokeObjectURL(im.src); };
            t.appendChild(im);
          } else { t.appendChild(el("span", "file__thumb-name", f.name)); }
          var x = el("button", "file__thumb-x", "×"); x.type = "button"; x.setAttribute("aria-label", "Убрать " + f.name);
          x.onclick = function () { removeAt(i); };
          t.appendChild(x); thumbs.insertBefore(t, plus);
        });
        plus.hidden = !input.multiple && files.length > 0;
      } else {
        var f = files[0];
        card.hidden = !f; plus.hidden = !!f;
        if (f) { name.textContent = f.name; meta.textContent = size(f); cardIco.textContent = (f.name.split(".").pop() || "").slice(0, 4).toUpperCase() || "ФАЙЛ"; }
      }
      return files.length > 0 && !bad;
    }
    input.addEventListener("change", function (e) {
      var ok = sync();
      if (e.isTrusted && ok && input.hasAttribute("data-autosubmit") && input.form) {
        if (input.form.requestSubmit) input.form.requestSubmit(); else input.form.submit();
      }
    });
    clear.addEventListener("click", function () { input.value = ""; fire(input, "change"); });
    root.addEventListener("dragover", function (e) { e.preventDefault(); root.classList.add("is-drag"); });
    root.addEventListener("dragleave", function () { root.classList.remove("is-drag"); });
    root.addEventListener("drop", function (e) { e.preventDefault(); root.classList.remove("is-drag"); if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; fire(input, "change"); } });
    sync();
  }

  /* ---------- Условный показ: data-show-when="name=v1,v2" | "name:checked" | "name!=" ---------- */
  function initShowWhen(scope) {
    var forms = [];
    scope.querySelectorAll("[data-show-when]").forEach(function (box) {
      if (box.dataset.swInit) return;
      box.dataset.swInit = "1";
      var rule = box.getAttribute("data-show-when"), form = box.closest("form") || document;
      var m = /^([\w\-]+)(:checked|!=|=)(.*)$/.exec(rule);
      if (!m) return;
      function value() {
        var ctl = form.querySelectorAll('[name="' + m[1] + '"]');
        if (!ctl.length) return "";
        if (ctl[0].type === "checkbox") return ctl[0].checked ? "on" : "";
        if (ctl[0].type === "radio") { var c = form.querySelector('[name="' + m[1] + '"]:checked'); return c ? c.value : ""; }
        for (var i = 0; i < ctl.length; i++) if (!ctl[i].disabled) return ctl[i].value;
        return ctl[0].value;
      }
      box._swSync = function () {
        var v = value(), show;
        if (m[2] === ":checked") show = v === "on";
        else if (m[2] === "!=") show = v !== m[3];
        else show = m[3].split(",").indexOf(v) >= 0;
        box.hidden = !show;
      };
      if (forms.indexOf(form) < 0) forms.push(form);
    });
    forms.forEach(function (form) {
      function syncAll() {
        // два прохода: вложенные условия зависят от внешних
        for (var k = 0; k < 2; k++) {
          form.querySelectorAll("[data-show-when]").forEach(function (b) { if (b._swSync) b._swSync(); });
          // поля в скрытых блоках не отправляются
          form.querySelectorAll("[data-show-when] input, [data-show-when] select, [data-show-when] textarea").forEach(function (i) {
            if (i.dataset.swOrigDisabled === undefined) i.dataset.swOrigDisabled = i.disabled ? "1" : "0";
            var hiddenAnc = i.closest("[data-show-when][hidden]");
            i.disabled = !!hiddenAnc || i.dataset.swOrigDisabled === "1";
          });
        }
      }
      if (!form.dataset.swBound) { form.addEventListener("change", syncAll); form.dataset.swBound = "1"; }
      syncAll();
    });
  }

  /* ---------- Пошаговая форма ---------- */
  function initSteps(form) {
    var panes = Array.prototype.slice.call(form.querySelectorAll(".step-pane"));
    if (!panes.length) return;
    var dots = document.querySelectorAll("[data-step-dot]");
    var cur = 0;
    var bad = panes.findIndex(function (p) { return p.querySelector(".field--error, .alert--error"); });
    if (bad >= 0) cur = bad;
    else { var hm = /^#step-(\d+)$/.exec(location.hash); if (hm && panes[+hm[1] - 1]) cur = +hm[1] - 1; }  // «Рассказ о клубе» → сразу шаг 2
    form.classList.add("is-stepped");
    function show(i) {
      cur = i;
      panes.forEach(function (p, j) { p.hidden = j !== i; });
      dots.forEach(function (d, j) { d.classList.toggle("is-current", j === i); d.classList.toggle("is-done", j < i); });
      var first = panes[i].querySelector("input:not([type=hidden]), button.sel__btn");
      if (first && !TOUCH && document.activeElement && document.activeElement !== document.body) first.focus({ preventScroll: true });
      // Тест 5, п. 7.2: на новом шаге — плавно к первым полям
      if (moved) { var top = (form.closest(".card") || form).getBoundingClientRect().top + window.scrollY - 80; window.scrollTo({ top: Math.max(0, top), behavior: "smooth" }); }
      moved = true;
    }
    var moved = false;
    function valid(p) {
      var ok = true;
      p.querySelectorAll("input[required], select[required]").forEach(function (inp) {
        var empty;
        if (inp.type === "checkbox" && inp.name) {
          var group = p.querySelectorAll('input[name="' + inp.name + '"]');
          empty = group.length > 1 ? !p.querySelector('input[name="' + inp.name + '"]:checked') : !inp.checked;
        } else empty = !inp.value.trim();
        var f = inp.closest(".field");
        if (empty && f) f.classList.add("field--error", "field--client-error");  // тест 5.1: красная рамка без надписи
        if (empty) ok = false;
      });
      return ok;
    }
    form.addEventListener("click", function (e) {
      if (e.target.closest("[data-step-next]")) { if (valid(panes[cur])) show(cur + 1); else if (window.SL && SL.toast) SL.toast("Заполните поля, обведённые красным", "error"); }
      if (e.target.closest("[data-step-prev]")) show(cur - 1);
    });
    form.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && e.target.tagName === "INPUT" && cur < panes.length - 1) { e.preventDefault(); if (valid(panes[cur])) show(cur + 1); }
    });
    show(cur);
  }

  /* ---------- Ошибка поля исчезает, когда его исправляют ---------- */
  document.addEventListener("input", clearError, true);
  document.addEventListener("change", clearError, true);
  function clearError(e) {
    var f = e.target.closest && e.target.closest(".field--error");
    if (!f) return;
    f.classList.remove("field--error");
    f.querySelectorAll(".field__error").forEach(function (x) { x.remove(); });
    var s = f.querySelector(".sel"); if (s) s.classList.remove("field--error-ctl");
  }

  /* ---------- «Сохранить» активна только при изменениях ---------- */
  function initDirty(form) {
    if (form.dataset.dirtyInit) return;
    form.dataset.dirtyInit = "1";
    function state() {
      var parts = [];
      form.querySelectorAll("input, select, textarea").forEach(function (i) {
        if (!i.name || i.name === "csrfmiddlewaretoken") return;
        if (i.type === "checkbox" || i.type === "radio") parts.push(i.name + "=" + i.checked);
        else if (i.type === "file") parts.push(i.name + "=" + (i.files && i.files.length));
        else parts.push(i.name + "=" + i.value);
      });
      return parts.join("&");
    }
    var initial = state();
    var buttons = form.querySelectorAll("button[type=submit], button:not([type])");
    function sync() {
      var dirty = state() !== initial || form.querySelector(".field--error");
      buttons.forEach(function (b) { if (!b.hasAttribute("data-always")) { b.disabled = !dirty; b.classList.toggle("is-inactive", !dirty); } });
    }
    form.addEventListener("input", sync);
    form.addEventListener("change", sync);
    form.addEventListener("click", function () { setTimeout(sync, 0); });
    sync();
  }

  /* ---------- Страница не прыгает после отправки формы ---------- */
  var SCROLL_KEY = "sl-scroll";
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if ((f.method || "").toLowerCase() !== "post" || f.hasAttribute("data-no-keep-scroll")) return;
    try { sessionStorage.setItem(SCROLL_KEY, JSON.stringify({ path: location.pathname, y: window.scrollY, at: Date.now() })); } catch (err) {}
  });
  // Тест 6, п. 33: «Сохранить и добавить ещё» — экран наверх, курсор в первое поле (адрес с ?top=1)
  function toTop() {
    if (!/[?&]top=1(&|$)/.test(location.search)) return false;
    if ("scrollRestoration" in history) history.scrollRestoration = "manual";
    window.scrollTo(0, 0);
    var f = document.querySelector("[data-autofocus]");
    if (f && !TOUCH) f.focus({ preventScroll: true });
    return true;
  }
  document.addEventListener("htmx:afterSettle", function h() { document.removeEventListener("htmx:afterSettle", h); setTimeout(function () { if (toTop()) try { history.replaceState(history.state, "", location.href.replace(/([?&])top=1&?/, "$1").replace(/[?&]$/, "")); } catch (e) {} }, 0); });
  function restoreScroll() {
    try {
      var s = JSON.parse(sessionStorage.getItem(SCROLL_KEY) || "null");
      sessionStorage.removeItem(SCROLL_KEY);
      // Ошибка в форме — плавно к первому полю с ошибкой (тест 4.2, п. 34)
      var err = document.querySelector(".field--error, .alert--error");
      if (err && !err.closest(".messages")) return;  // к ошибке ведёт smooth.js
      if (toTop()) return;
      // Ссылка ведёт к блоку (#…) — плавно к нему, а не на прежнее место
      if (location.hash.length > 1) {
        var target = document.getElementById(location.hash.slice(1));
        if (target) { setTimeout(function () { target.scrollIntoView({ behavior: "smooth", block: "start" }); }, 60); return; }
      }
      if (s && s.path === location.pathname && Date.now() - s.at < 15000) {
        if ("scrollRestoration" in history) history.scrollRestoration = "manual";
        window.scrollTo(0, s.y);
        requestAnimationFrame(function () { window.scrollTo(0, s.y); });
      }
    } catch (err) {}
  }

  /* ---------- Подтверждение ---------- */
  // Подтверждение — окно в стиле сайта вместо системного (тест 4.2, п. 23, 33)
  function confirmDialog(text, okLabel, danger) {
    return new Promise(function (resolve) {
      var back = el("div", "modal");
      var box = el("div", "modal__box");
      box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true");
      var p = el("p", "modal__text", text);
      var row = el("div", "modal__actions");
      var no = el("button", "btn btn--ghost", "Отмена"); no.type = "button";
      var yes = el("button", "btn " + (danger ? "btn--danger-solid" : "btn--primary"), okLabel || "Да"); yes.type = "button";
      row.appendChild(no); row.appendChild(yes);
      box.appendChild(p); box.appendChild(row); back.appendChild(box);
      document.body.appendChild(back);
      requestAnimationFrame(function () { back.classList.add("is-open"); });
      yes.focus();
      function done(v) { back.classList.remove("is-open"); setTimeout(function () { back.remove(); }, 150); document.removeEventListener("keydown", esc); resolve(v); }
      function esc(e) { if (e.key === "Escape") done(false); }
      document.addEventListener("keydown", esc);
      no.onclick = function () { done(false); };
      yes.onclick = function () { done(true); };
      back.addEventListener("mousedown", function (e) { if (e.target === back) done(false); });
    });
  }
  window.SLConfirm = confirmDialog;
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-confirm]");
    if (!b || b.dataset.confirmed) return;
    e.preventDefault(); e.stopPropagation();
    var danger = b.classList.contains("btn--danger") || /удал|убра|отклон|отмен/i.test(b.textContent);
    var text = b.getAttribute("data-confirm");
    // {value} — значение поля формы (data-confirm-field), например телефон (тест 5, п. 18.2)
    if (b.form && b.getAttribute("data-confirm-field")) {
      var fld = b.form.elements[b.getAttribute("data-confirm-field")];
      var v = fld ? fld.value.trim() : "";
      if (!v) { if (fld) fld.focus(); return; }
      text = text.replace("{value}", v);
    }
    confirmDialog(text, b.getAttribute("data-confirm-ok") || b.textContent.trim() || "Да", danger).then(function (ok) {
      if (!ok) return;
      b.dataset.confirmed = "1";
      if (b.form && (b.type === "submit" || b.tagName === "BUTTON")) {
        if (b.name) { var h = document.createElement("input"); h.type = "hidden"; h.name = b.name; h.value = b.value; b.form.appendChild(h); }
        b.form.requestSubmit ? b.form.requestSubmit() : b.form.submit();
      } else b.click();
      setTimeout(function () { delete b.dataset.confirmed; }, 500);
    });
  }, true);

  /* ---------- Телефон: +7 подставляется сам, 8 и 7 = +7, маска +7 900 000-00-00 ---------- */
  // Группировка номеров других стран — как в apps/accounts/phone.py (COUNTRIES)
  var PHONE_CC = { "998": [2, 3, 2, 2], "996": [3, 3, 3], "992": [2, 3, 2, 2], "993": [2, 2, 2, 2], "994": [2, 3, 2, 2], "995": [3, 2, 2, 2],
    "374": [2, 3, 3], "373": [2, 3, 3], "380": [2, 3, 2, 2], "371": [2, 3, 3], "370": [3, 2, 3], "972": [2, 3, 4], "971": [2, 3, 4], "90": [3, 3, 2, 2], "375": [2, 3, 2, 2] };
  function formatIntl(d) {
    d = d.slice(0, 15);
    var cc = PHONE_CC[d.slice(0, 3)] ? d.slice(0, 3) : (PHONE_CC[d.slice(0, 2)] ? d.slice(0, 2) : null);
    if (!cc) return "+" + d;
    var rest = d.slice(cc.length), g = PHONE_CC[cc], parts = [], i = 0;
    for (var k = 0; k < g.length && i < rest.length; k++) { parts.push(rest.slice(i, i + g[k])); i += g[k]; }
    if (i < rest.length) parts[parts.length - 1] += rest.slice(i);
    var out = "+" + cc;
    if (parts.length) out += " " + parts[0];
    if (parts.length > 1) out += " " + parts.slice(1).join("-");
    return out;
  }
  function formatPhone(raw) {
    var t = raw.trim(), p = t.lastIndexOf("+");
    if (p > 0) t = t.slice(p);  // к подставленному «+7 » дописали «+375…» / «+998…» — берём номер с последнего «+»
    var plus = t[0] === "+", d = t.replace(/\D/g, "");
    if (!d) return plus ? "+" : "";
    if (d.indexOf("375") === 0 && (plus || d.length >= 12)) return formatIntl(d.slice(0, 12));
    if (plus && d[0] !== "7") return formatIntl(d);  // другие страны
    if (!plus && d.length > 11 && d[0] !== "7" && d[0] !== "8") return formatIntl(d);
    if (d.length > 11 && (d.indexOf("77") === 0 || d.indexOf("78") === 0)) d = d.slice(1); // «+7 » уже стоял, а номер вставили целиком
    if (d[0] === "8") d = "7" + d.slice(1);
    else if (d[0] === "9") d = "7" + d;
    else if (d[0] !== "7") return formatIntl(d);
    d = d.slice(0, 11);
    var out = "+7";
    if (d.length > 1) out += " " + d.slice(1, 4);
    if (d.length > 4) out += " " + d.slice(4, 7);
    if (d.length > 7) out += "-" + d.slice(7, 9);
    if (d.length > 9) out += "-" + d.slice(9, 11);
    return out;
  }
  function enhancePhone(input) {
    if (input.dataset.ui) return;
    input.dataset.ui = "1";
    input.addEventListener("focus", function () { if (!input.value) input.value = "+7 "; });
    input.addEventListener("blur", function () { if (input.value.trim() === "+7") input.value = ""; });
    input.addEventListener("input", function () {
      var atEnd = input.selectionStart === input.value.length;
      var v = formatPhone(input.value);
      if (v !== input.value) { input.value = v; if (atEnd) input.setSelectionRange(v.length, v.length); }
    });
    if (input.value) input.value = formatPhone(input.value);
  }

  /* «Поделиться» — окно в smooth.js (тест 5, п. 2.2) */

  function init(scope) {
    scope = scope || document;
    scope.querySelectorAll("select").forEach(enhanceSelect);
    scope.querySelectorAll('input[type="date"]').forEach(enhanceDate);
    scope.querySelectorAll('input[type="time"]').forEach(enhanceTime);
    scope.querySelectorAll('input[type="file"]').forEach(enhanceFile);
    scope.querySelectorAll('input[type="tel"]').forEach(enhancePhone);
    initShowWhen(scope);
    scope.querySelectorAll("form[data-steps]").forEach(initSteps);
    scope.querySelectorAll("form[data-dirty-guard]").forEach(initDirty);
  }
  window.SL = { init: init };
  document.addEventListener("DOMContentLoaded", function () { init(document); restoreScroll(); });
  document.addEventListener("htmx:afterSettle", function (e) { init(e.target); });
})();

/* СтартЛист — меню на телефоне, раздел «Проведения», поля формы поддержки по теме (26.10.2026) */
(function () {
  document.documentElement.classList.add("js");

  // Мобильное меню: закрыть по клику вне, Escape и при переходе
  document.addEventListener("click", function (e) {
    document.querySelectorAll("details[data-mnav][open]").forEach(function (d) {
      if (!d.contains(e.target)) d.removeAttribute("open");
    });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    document.querySelectorAll("details[data-mnav][open]").forEach(function (d) { d.removeAttribute("open"); d.querySelector("summary").focus(); });
  });

  // Тест 6, п. 5: плитка «+» без обёртки (data-ui="raw") — форма отправляется сразу после выбора
  document.addEventListener("change", function (e) {
    var inp = e.target;
    if (!inp.matches || !inp.matches('input[type="file"][data-autosubmit][data-ui="raw"]') || !inp.files || !inp.files.length) return;
    var max = (+inp.getAttribute("data-max-mb") || 0) * 1048576;
    for (var i = 0; i < inp.files.length; i++) {
      if (max && inp.files[i].size > max) {
        var msg = "«" + inp.files[i].name + "» больше " + inp.getAttribute("data-max-mb") + " МБ — уменьшите файл и выберите снова.";
        if (window.SL && SL.toast) SL.toast(msg, "error"); else alert(msg);
        inp.value = ""; return;
      }
    }
    var tile = inp.form && inp.form.querySelector(".file__plus");
    if (tile) tile.classList.add("is-busy");
    if (inp.form.requestSubmit) inp.form.requestSubmit(); else inp.form.submit();
  });

  // Тест 6, п. 18: пока открыта клавиатура, нижнее меню скрыто (не поднимается вместе с ней)
  (function () {
    var root = document.documentElement, vv = window.visualViewport, t = null;
    function typing(el) {
      if (!el || !el.matches) return false;
      if (el.matches("textarea, [contenteditable=true]")) return !el.readOnly;
      return el.matches("input") && !el.readOnly && !/^(checkbox|radio|file|button|submit|reset|range|color|hidden)$/i.test(el.type) && el.inputMode !== "none";
    }
    function sync() {
      var on = typing(document.activeElement);
      if (vv && !on) on = vv.height < window.innerHeight * 0.72;
      root.classList.toggle("kb-open", on && window.innerWidth < 720);
    }
    document.addEventListener("focusin", sync);
    document.addEventListener("focusout", function () { clearTimeout(t); t = setTimeout(sync, 60); });
    if (vv) vv.addEventListener("resize", function () { clearTimeout(t); t = setTimeout(sync, 60); });
  })();

  // Тест 6, п. 6: поле текста растёт по мере ввода
  function grow(ta) { ta.style.height = "auto"; ta.style.height = Math.max(ta.scrollHeight + 2, 96) + "px"; }
  document.addEventListener("input", function (e) { if (e.target.matches && e.target.matches("textarea[data-autogrow]")) grow(e.target); });
  function growAll() { document.querySelectorAll("textarea[data-autogrow]").forEach(grow); }
  document.addEventListener("DOMContentLoaded", growAll);
  document.addEventListener("htmx:afterSettle", growAll);

  // Меню «три точки» (тест 6): закрыть по клику вне и по Escape; открыто только одно
  document.addEventListener("click", function (e) {
    document.querySelectorAll("details.kebab[open], details.rswitch[open], details.msearch__filter[open]").forEach(function (d) { if (!d.contains(e.target)) d.removeAttribute("open"); });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") document.querySelectorAll("details.kebab[open], details.rswitch[open], details.msearch__filter[open]").forEach(function (d) { d.removeAttribute("open"); });
  });

  // Тест 6, п. 8: автосохранение черновика турнира (формы с data-autosave). Кнопки «Сохранить черновик» нет:
  // поля сохраняются сами через ~1 с после ввода, а при уходе со страницы (назад, другой раздел) — сразу.
  (function () {
    var form = null, timer = null, dirty = false, busy = false;
    function status(text) { var el = document.querySelector("[data-autosave-status]"); if (el) el.textContent = text; }
    function data(keep) {
      var fd = new FormData(form);
      Array.from(fd.keys()).forEach(function (k) { fd.getAll(k).forEach(function (v) { if (typeof v !== "string") fd.delete(k); }); });
      fd.set("autosave", "1");
      return fd;
    }
    var pending = null;
    function apply(j) {
      if (!j || !j.ok) { status(j && j.reason === "conflict" ? "Данные изменились в другой вкладке — обновите страницу" : j && j.reason === "title" ? "" : "Черновик не сохранён"); return; }
      var rev = form.querySelector("[name=revision]"), key = form.querySelector("[name=create_key]");
      if (rev) rev.value = j.revision;
      if (key && j.url) {
        // первый автосейв нового турнира: дальше работаем с созданным черновиком
        var inp = document.createElement("input"); inp.type = "hidden"; inp.name = "revision"; inp.value = j.revision;
        form.appendChild(inp); key.remove();
        form.setAttribute("action", j.url);
        try { history.replaceState(history.state, "", j.url); } catch (e) {}
      }
      status("Черновик сохранён в " + j.at);
    }
    function save(keep) {
      if (!form || !dirty) return;
      var title = form.querySelector("[name=title]");
      if (title && !title.value.trim()) return;  // черновик появляется, когда есть название
      if (busy && !keep) { clearTimeout(timer); timer = setTimeout(save, 800); return; }
      if (busy && keep && form.querySelector("[name=create_key]")) return;  // первый черновик ещё создаётся — второй не нужен
      dirty = false; busy = true;
      var url = form.getAttribute("action") || location.href;
      var opts = { method: "POST", body: data(), credentials: "same-origin", headers: { "X-Requested-With": "fetch", "Accept": "application/json" } };
      var t = document.querySelector("[name=csrfmiddlewaretoken]"); if (t) opts.headers["X-CSRFToken"] = t.value;
      if (keep) opts.keepalive = true;
      status("Сохраняем черновик…");
      pending = fetch(url, opts).then(function (r) { return r.json().catch(function () { return null; }); }).then(function (j) { busy = false; pending = null; apply(j); })
        .catch(function () { busy = false; pending = null; dirty = true; status("Черновик не сохранён — проверьте связь"); });
    }
    function bind(scope) {
      var f = (scope || document).querySelector("form[data-autosave]");
      if (!f || f === form) return;
      form = f; dirty = false;
      form.addEventListener("input", function (e) { if (e.target.type === "file") return; dirty = true; clearTimeout(timer); timer = setTimeout(save, 1200); });
      form.addEventListener("change", function (e) { if (e.target.type === "file") return; dirty = true; clearTimeout(timer); timer = setTimeout(save, 600); });
      form.addEventListener("submit", function (e) {
        clearTimeout(timer); dirty = false;
        // автосохранение ещё идёт: дождаться новой версии, иначе обычная отправка получит конфликт версий
        if (pending) {
          e.preventDefault(); e.stopImmediatePropagation();
          var btn = e.submitter, f = form;
          pending.then(function () { if (f.requestSubmit) f.requestSubmit(btn || undefined); else f.submit(); });
        }
      }, true);
    }
    document.addEventListener("DOMContentLoaded", function () { bind(document); });
    document.addEventListener("htmx:afterSettle", function () { bind(document); });
    // уход со страницы: ссылка, «назад», закрытие вкладки
    document.addEventListener("click", function (e) {
      var a = e.target.closest && e.target.closest("a[href]");
      if (a && form && dirty && !form.contains(a)) save(true);
    }, true);
    window.addEventListener("pagehide", function () { save(true); });
    document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") save(true); });
  })();

  // «Раздел: …» в «Проведении»
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-start-drop] .start-drop__btn");
    if (!btn) return;
    var box = btn.closest("[data-start-drop]");
    var open = box.classList.toggle("is-open");
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  });

  // Форма поддержки: показываем только поля, нужные выбранной теме (SUP-02)
  function topicSync() {
    var form = document.querySelector("[data-topic-form]");
    if (!form) return;
    var sel = form.querySelector("select[name=topic]");
    var v = sel ? sel.value : "";
    form.querySelectorAll("[data-topics]").forEach(function (el) {
      var on = !!v && el.getAttribute("data-topics").split(" ").indexOf(v) >= 0;
      el.hidden = !on;
    });
  }
  document.addEventListener("change", function (e) { if (e.target.closest && e.target.closest("[data-topic-form] select[name=topic]")) topicSync(); });
  topicSync();
  document.addEventListener("htmx:afterSettle", topicSync);
})();
