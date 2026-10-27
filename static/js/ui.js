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
    if (openPopup && !openPopup.root.contains(e.target)) closePopup();
  });
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
      vis[i].scrollIntoView({ block: "nearest" });
      active = i;
    }
    function open() {
      if (btn.disabled) return;
      closePopup();
      build();
      pop.hidden = false;
      root.classList.add("is-open");
      var r = btn.getBoundingClientRect();
      root.classList.toggle("is-up", window.innerHeight - r.bottom < 280 && r.top > 300);
      openPopup = { root: root, close: close };
      var vis = items.filter(function (li) { return !li.hidden; });
      highlight(Math.max(0, vis.indexOf(items[sel.selectedIndex])));
      if (filter) filter.focus();
    }
    function close() {
      pop.hidden = true;
      root.classList.remove("is-open");
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
    vis.inputMode = "numeric";
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
      var title = el("div", "dp__title", MONTHS[view.getMonth()] + " " + view.getFullYear());
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
          b.onclick = function () { set(date); close(); vis.focus(); };
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
    function open() {
      closePopup();
      view = parseIso(input.value) || new Date();
      view = new Date(view.getFullYear(), view.getMonth(), 1);
      render();
      pop.hidden = false;
      root.classList.add("is-open");
      openPopup = { root: root, close: close };
    }
    function close() { pop.hidden = true; root.classList.remove("is-open"); if (openPopup && openPopup.root === root) openPopup = null; }
    vis.addEventListener("click", open);
    icon.addEventListener("click", function () { if (pop.hidden) { open(); vis.focus(); } else close(); });
    vis.addEventListener("keydown", function (e) { if (e.key === "ArrowDown") { e.preventDefault(); open(); } if (e.key === "Enter") { e.preventDefault(); vis.blur(); } });
    vis.addEventListener("focus", function () { var d = parseIso(input.value); if (d) vis.value = pad(d.getDate()) + "." + pad(d.getMonth() + 1) + "." + d.getFullYear(); });
    vis.addEventListener("blur", function () {
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
        b.onclick = function () { set(c[0] === null ? 10 : c[0], m); close(); vis.focus(); };
        mg.appendChild(b);
      })(m);
      hc.appendChild(hg); mc.appendChild(mg);
      cols.appendChild(hc); cols.appendChild(mc);
      pop.appendChild(cols);
      var foot = el("div", "dp__foot");
      var hint = el("span", "tp__hint", "Можно ввести вручную, например 9:40");
      foot.appendChild(hint);
      if (!input.required) { var clear = el("button", "dp__link", "Очистить"); clear.type = "button"; clear.onclick = function () { set(null); close(); }; foot.appendChild(clear); }
      pop.appendChild(foot);
    }
    function open() { closePopup(); render(); pop.hidden = false; root.classList.add("is-open"); openPopup = { root: root, close: close }; }
    function close() { pop.hidden = true; root.classList.remove("is-open"); if (openPopup && openPopup.root === root) openPopup = null; }
    vis.addEventListener("click", open);
    vis.addEventListener("focus", function () { setTimeout(function () { vis.select(); }, 0); });  // ввод заменяет «23:59», а не дописывается
    icon.addEventListener("click", function () { if (pop.hidden) { open(); vis.focus(); } else close(); });
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
  function enhanceFile(input) {
    if (input.dataset.ui) return;
    input.dataset.ui = "1";
    var root = el("div", "file");
    input.parentNode.insertBefore(root, input);
    root.appendChild(input);
    input.classList.add("file__native");
    var btn = el("label", "btn btn--ghost btn--sm file__btn", "Выбрать файл");
    if (!input.id) input.id = "f" + Math.random().toString(36).slice(2);
    btn.setAttribute("for", input.id);
    var name = el("span", "file__name", "Файл не выбран");
    var clear = el("button", "file__clear", "×"); clear.type = "button"; clear.hidden = true; clear.setAttribute("aria-label", "Убрать файл");
    root.appendChild(btn); root.appendChild(name); root.appendChild(clear);
    var err = el("p", "field__error"); err.hidden = true; err.setAttribute("role", "alert"); root.appendChild(err);
    function sync() {
      var f = input.files && input.files[0];
      var maxMb = +input.getAttribute("data-max-mb") || 0, bad = "";
      if (f && /pdf/.test(input.accept || "") && !/\.pdf$/i.test(f.name)) bad = "Нужен файл в формате PDF.";
      else if (f && maxMb && f.size > maxMb * 1048576) bad = "Файл больше " + maxMb + " МБ — уменьшите его и выберите снова.";
      err.textContent = bad; err.hidden = !bad;
      if (bad) { input.value = ""; f = null; }
      name.textContent = f ? f.name + " · " + (f.size < 1048576 ? Math.max(1, Math.round(f.size / 1024)) + " КБ" : (f.size / 1048576).toFixed(1).replace(".", ",") + " МБ") : "Файл не выбран";
      root.classList.toggle("has-file", !!f);
      clear.hidden = !f;
    }
    input.addEventListener("change", sync);
    clear.addEventListener("click", function () { input.value = ""; sync(); fire(input, "change"); });
    root.addEventListener("dragover", function (e) { e.preventDefault(); root.classList.add("is-drag"); });
    root.addEventListener("dragleave", function () { root.classList.remove("is-drag"); });
    root.addEventListener("drop", function (e) { e.preventDefault(); root.classList.remove("is-drag"); if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; sync(); fire(input, "change"); } });
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
    form.classList.add("is-stepped");
    function show(i) {
      cur = i;
      panes.forEach(function (p, j) { p.hidden = j !== i; });
      dots.forEach(function (d, j) { d.classList.toggle("is-current", j === i); d.classList.toggle("is-done", j < i); });
      var first = panes[i].querySelector("input:not([type=hidden]), button.sel__btn");
      if (first && document.activeElement && document.activeElement !== document.body) first.focus();
    }
    function valid(p) {
      var ok = true;
      p.querySelectorAll("input[required], select[required]").forEach(function (inp) {
        var empty;
        if (inp.type === "checkbox" && inp.name) {
          var group = p.querySelectorAll('input[name="' + inp.name + '"]');
          empty = group.length > 1 ? !p.querySelector('input[name="' + inp.name + '"]:checked') : !inp.checked;
        } else empty = !inp.value.trim();
        var f = inp.closest(".field");
        if (empty && f && !f.querySelector(".field__error--client")) {
          f.classList.add("field--error");
          var err = el("p", "field__error field__error--client");
          err.setAttribute("role", "alert");
          err.innerHTML = '<span aria-hidden="true">!</span> ' + (inp.type === "checkbox" ? "Выберите хотя бы один вариант." : "Обязательное поле.");
          f.appendChild(err);
        }
        if (empty) ok = false;
      });
      return ok;
    }
    form.addEventListener("click", function (e) {
      if (e.target.closest("[data-step-next]")) { if (valid(panes[cur])) show(cur + 1); }
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
  function restoreScroll() {
    try {
      var s = JSON.parse(sessionStorage.getItem(SCROLL_KEY) || "null");
      sessionStorage.removeItem(SCROLL_KEY);
      // Ошибка в форме — плавно к первому полю с ошибкой (тест 4.2, п. 34)
      var err = document.querySelector(".field--error, .alert--error");
      if (err && !err.closest(".messages")) {
        setTimeout(function () {
          err.scrollIntoView({ behavior: "smooth", block: "center" });
          var ctl = err.querySelector("input:not([type=hidden]), textarea, .sel__btn");
          if (ctl) ctl.focus({ preventScroll: true });
        }, 60);
        return;
      }
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
    confirmDialog(b.getAttribute("data-confirm"), b.getAttribute("data-confirm-ok") || b.textContent.trim() || "Да", danger).then(function (ok) {
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

  /* ---------- «Поделиться» ---------- */
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-share]");
    if (!b) return;
    e.preventDefault();
    var url = b.getAttribute("data-share") || location.href, title = b.getAttribute("data-share-title") || document.title;
    if (navigator.share) { navigator.share({ title: title, url: url }).catch(function () {}); return; }
    (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject()).then(function () {
      var old = b.textContent; b.textContent = "Ссылка скопирована"; setTimeout(function () { b.textContent = old; }, 1800);
    }).catch(function () { window.prompt("Скопируйте ссылку:", url); });
  });

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
  document.addEventListener("htmx:afterSwap", function (e) { init(e.target); });
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

  // «Раздел: …» в «Проведении»
  document.querySelectorAll("[data-start-drop]").forEach(function (box) {
    var btn = box.querySelector(".start-drop__btn");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var open = box.classList.toggle("is-open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  });

  // Форма поддержки: показываем только поля, нужные выбранной теме (SUP-02)
  var form = document.querySelector("[data-topic-form]");
  if (form) {
    var sel = form.querySelector("select[name=topic]");
    var sync = function () {
      var v = sel ? sel.value : "";
      form.querySelectorAll("[data-topics]").forEach(function (el) {
        var on = !!v && el.getAttribute("data-topics").split(" ").indexOf(v) >= 0;
        el.hidden = !on;
      });
    };
    if (sel) { sel.addEventListener("change", sync); sync(); }
  }
})();
