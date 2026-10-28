/* СтартЛист — плавная работа на телефоне (тест 5, 27.10.2026).
   - формы отправляются без перезагрузки страницы (htmx boost): экран не «дёргается»;
   - ошибка — сообщением вверху экрана и плавным переходом к полю;
   - повторное нажатие «Отправить» не создаёт дублей;
   - вкладки, даты, маршруты и езды меняют только свою часть страницы (data-swap);
   - «Поделиться» — своё окно: мессенджеры, соцсети, почта, копирование ссылки;
   - «← Назад» ведёт туда, откуда пришли;
   - кнопка поиска неактивна, пока поле пустое (data-require-input);
   - подсказки при вводе (data-suggest): регион, порода. */
(function () {
  "use strict";
  var TOUCH = window.matchMedia && matchMedia("(pointer: coarse)").matches;

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  /* ---------- Сообщение вверху экрана ---------- */
  function toastBox() {
    var box = document.querySelector(".toasts");
    if (!box) { box = el("div", "toasts"); box.setAttribute("role", "status"); box.setAttribute("aria-live", "polite"); document.body.appendChild(box); }
    return box;
  }
  function toast(text, kind) {
    if (!text) return;
    var t = el("div", "toast toast--" + (kind || "info"));
    t.appendChild(el("span", "toast__text", text));
    var x = el("button", "toast__close", "×"); x.type = "button"; x.setAttribute("aria-label", "Закрыть");
    t.appendChild(x);
    toastBox().appendChild(t);
    requestAnimationFrame(function () { t.classList.add("is-in"); });
    function hide() { t.classList.remove("is-in"); setTimeout(function () { t.remove(); }, 250); }
    x.onclick = hide;
    t.addEventListener("click", hide);
    setTimeout(hide, kind === "error" ? 7000 : 4000);
  }
  // Сообщения Django (успех/ошибка после действия) — всплывают поверх страницы
  function messagesToToasts(scope) {
    (scope || document).querySelectorAll(".messages .alert").forEach(function (a) {
      var kind = /alert--(\w+)/.exec(a.className); kind = kind ? kind[1] : "info";
      toast(a.textContent.trim(), kind);
    });
    (scope || document).querySelectorAll(".messages").forEach(function (m) { m.classList.add("is-toasted"); });
  }

  /* ---------- К ошибке ---------- */
  function fieldLabel(f) {
    var l = f.querySelector("label, .field__label, legend");
    return l ? l.textContent.replace(/\s*(необязательно|обязательно)\s*$/i, "").trim() : "";
  }
  function goToError() {
    var err = document.querySelector(".field--error, form .alert--error, .form-errors");
    if (!err || err.closest(".messages")) return false;
    var name = err.classList.contains("field--error") ? fieldLabel(err) : "";
    var msg = err.querySelector(".field__error");
    if (name && name.length > 40) name = name.slice(0, 38) + "…";
    var isClient = msg && msg.classList.contains("field__error--client");
    toast(name ? "Проверьте поле «" + name + "»" + (msg && !isClient ? ": " + msg.textContent.replace(/^!\s*/, "").trim() : "") : (err.textContent.trim().slice(0, 140) || "Проверьте форму"), "error");
    // Тест 5.1, п. 8: общая ошибка формы — только сообщением вверху, без дубля над полями
    if (err.classList.contains("alert--error")) err.hidden = true;
    setTimeout(function () {
      err.scrollIntoView({ behavior: "smooth", block: "center" });
      var ctl = err.querySelector("input:not([type=hidden]):not([type=checkbox]):not([type=radio]), textarea, .sel__btn");
      if (ctl && !TOUCH) ctl.focus({ preventScroll: true });
    }, 80);
    return true;
  }

  /* ---------- Проверка обязательных полей до отправки ---------- */
  function precheck(form) {
    var bad = null;
    form.querySelectorAll("input[required], select[required], textarea[required]").forEach(function (inp) {
      if (inp.disabled || inp.closest("[hidden]")) return;
      var empty;
      if (inp.type === "checkbox" || inp.type === "radio") {
        var group = form.querySelectorAll('input[name="' + inp.name + '"]');
        empty = !Array.prototype.some.call(group, function (g) { return g.checked; });
      } else if (inp.type === "file") empty = !(inp.files && inp.files.length);
      else empty = !String(inp.value || "").trim();
      if (!empty) return;
      var f = inp.closest(".field") || inp.parentNode;
      // Тест 5.1, п. 10: без надписей «Выберите вариант» — поле обводится красным, название — в сообщении вверху
      f.classList.add("field--error", "field--client-error");
      bad = bad || f;
    });
    if (bad) { goToError(); return false; }
    return true;
  }

  /* ---------- Защита от повторной отправки ---------- */
  function busy(form, on) {
    form.classList.toggle("is-busy", on);
    form.querySelectorAll('button[type=submit], button:not([type]), input[type=submit]').forEach(function (b) {
      if (on) { b.dataset.wasDisabled = b.disabled ? "1" : ""; b.disabled = true; b.classList.add("is-loading"); }
      else { if (!b.dataset.wasDisabled) b.disabled = false; b.classList.remove("is-loading"); }
    });
  }
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (f.classList.contains("is-busy")) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    if (!f.hasAttribute("novalidate-client") && !precheck(f)) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    // Обычная (не htmx) отправка: блокируем кнопки после того, как браузер взял значение нажатой
    if (!f.hasAttribute("hx-boost") && !f.hasAttribute("hx-post")) setTimeout(function () { busy(f, true); }, 0);
  }, true);
  // Вернулись «Назад» из кэша браузера — кнопки снова активны
  window.addEventListener("pageshow", function (e) {
    if (e.persisted) document.querySelectorAll("form.is-busy").forEach(function (f) { busy(f, false); });
  });

  /* ---------- Формы без перезагрузки ---------- */
  function boostForms(scope) {
    if (!window.htmx) return;
    scope.querySelectorAll("form").forEach(function (f) {
      if (f.hasAttribute("hx-boost") || f.hasAttribute("hx-post") || f.hasAttribute("hx-get") || f.hasAttribute("data-no-boost") || f.target) return;
      var action = f.getAttribute("action") || "";
      if (/^https?:/.test(action) && action.indexOf(location.origin) !== 0) return;
      f.setAttribute("hx-boost", "true");
      htmx.process(f);
    });
  }
  var before = null;
  document.addEventListener("htmx:configRequest", function (e) {
    // CSRF: токен меняется после входа — берём актуальный из страницы
    var t = document.querySelector("[name=csrfmiddlewaretoken]");
    if (t) e.detail.headers["X-CSRFToken"] = t.value;
  });
  document.addEventListener("htmx:beforeRequest", function (e) {
    var f = e.detail.elt && e.detail.elt.closest ? e.detail.elt.closest("form") : null;
    if (e.detail.boosted) {
      before = { path: location.pathname, y: window.scrollY };
      if (f) busy(f, true);
      document.documentElement.classList.add("is-loading");
    }
  });
  document.addEventListener("htmx:afterRequest", function (e) {
    var f = e.detail.elt && e.detail.elt.closest ? e.detail.elt.closest("form") : null;
    if (f && document.contains(f)) busy(f, false);
    document.documentElement.classList.remove("is-loading");
    if (e.detail.failed && !e.detail.xhr.status) toast("Нет связи с сервером. Проверьте интернет и повторите.", "error");
  });
  document.addEventListener("htmx:beforeSwap", function (e) {
    var xhr = e.detail.xhr, ct = xhr.getResponseHeader("Content-Type") || "";
    // Файл (PDF, таблица) — открываем обычным переходом
    if (e.detail.boosted && ct && ct.indexOf("text/html") < 0) { e.detail.shouldSwap = false; location.href = xhr.responseURL; return; }
    if (xhr.status >= 400 && xhr.status < 500 && ct.indexOf("text/html") >= 0) { e.detail.shouldSwap = true; e.detail.isError = false; }
    if (xhr.status >= 500) { e.detail.shouldSwap = false; toast("Что-то пошло не так на сервере. Мы уже знаем — попробуйте ещё раз через минуту.", "error"); }
  });
  document.addEventListener("htmx:afterSettle", function (e) {
    if (!e.detail.boosted) return;
    afterPage();
    if (goToError()) return;
    if (location.hash.length > 1) {
      var t = document.getElementById(location.hash.slice(1));
      if (t) { t.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
    }
    if (before && before.path === location.pathname) window.scrollTo(0, before.y);
    else window.scrollTo(0, 0);
  });

  /* ---------- Часть страницы: вкладки, даты, маршруты, езды ---------- */
  function swapLinks(scope) {
    if (!window.htmx) return;
    // Остальные вкладки и переключатели дней во всём сервисе — без перезагрузки страницы (п. 4.2)
    scope.querySelectorAll(".tabs a[href], .day-nav a[href], .start-drop a[href]").forEach(function (a) {
      if (a.closest("[data-swap]") || a.hasAttribute("hx-boost") || a.hasAttribute("hx-get") || a.target || (a.getAttribute("href") || "").charAt(0) === "#") return;
      a.setAttribute("hx-boost", "true");
      htmx.process(a);
    });
    scope.querySelectorAll("[data-swap]").forEach(function (box) {
      var target = box.getAttribute("data-swap");
      box.querySelectorAll("a[href]").forEach(function (a) {
        if (a.hasAttribute("hx-get") || a.hasAttribute("data-no-swap") || (a.getAttribute("href") || "").charAt(0) === "#") return;
        a.setAttribute("hx-get", a.getAttribute("href"));
        a.setAttribute("hx-select", target);
        a.setAttribute("hx-target", target);
        a.setAttribute("hx-swap", "outerHTML transition:true show:none");
        a.setAttribute("hx-push-url", "true");
        htmx.process(a);
      });
    });
  }
  document.addEventListener("htmx:afterSettle", function (e) {
    if (e.detail.boosted) return;
    var a = e.detail.requestConfig && e.detail.requestConfig.elt;
    if (!a || !a.closest) return;
    var box = document.querySelector(a.getAttribute("hx-target") || "x");
    if (box) {
      afterPage(box);
      // Вкладки остаются под шапкой: начало раздела — сразу под ними, без прыжков (тест 5.1, п. 4.1)
      var bar = document.querySelector(".topbar");
      var off = bar ? bar.getBoundingClientRect().bottom : 60;
      var top = box.getBoundingClientRect().top;
      if (top < off) window.scrollTo({ top: window.scrollY + top - off, behavior: "auto" });
    }
  });

  /* ---------- Поделиться ---------- */
  // Знаки мессенджеров и соцсетей (тест 5.1, п. 3.2)
  var LOGOS = {
    tg: '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true"><path fill="#fff" d="M20.7 4.3 3.4 11c-1.2.5-1.2 1.2-.2 1.5l4.4 1.4 1.7 5.2c.2.6.4.8.8.8.4 0 .6-.2.9-.5l2.1-2 4.4 3.2c.8.4 1.4.2 1.6-.8l2.9-13.6c.3-1.2-.4-1.7-1.3-1.3zM9 13.6l9-5.7c.4-.3.8-.1.5.2l-7.4 6.7-.3 3.2z"/></svg>',
    wa: '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true"><path fill="none" stroke="#fff" stroke-width="1.8" d="M4.2 19.8l1.1-3.9A8.5 8.5 0 1 1 8.6 19z"/><path fill="#fff" d="M9 7.6c.2-.4.4-.4.7-.4h.5c.2 0 .4 0 .6.5l.8 1.8c.1.2.1.4 0 .6l-.4.6c-.1.2-.2.3 0 .6.5.9 1.6 2 2.6 2.4.3.1.4 0 .6-.1l.6-.7c.2-.2.4-.2.6-.1l1.8.8c.3.2.5.3.5.5 0 .9-.6 1.8-1.5 2-1 .2-2.3 0-4.3-1.3-1.7-1.1-3-3-3.3-4-.3-1-.1-2 .2-2.7z"/></svg>',
    vk: '<svg viewBox="0 0 24 24" width="30" height="30" aria-hidden="true"><path fill="#fff" d="M12.8 17.5C6.9 17.5 3.5 13.4 3.4 6.6h3c.1 5 2.3 7.1 4 7.5V6.6h2.8v4.3c1.7-.2 3.5-2.2 4.1-4.3h2.8c-.5 2.7-2.4 4.7-3.8 5.5 1.4.7 3.6 2.4 4.4 5.4h-3.1c-.7-2-2.3-3.6-4.4-3.8v3.8z"/></svg>',
    vb: '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true"><path fill="none" stroke="#fff" stroke-width="1.8" stroke-linejoin="round" d="M12 3c5 0 8 2 8 7.5S17 18 12.5 18H11l-3.5 3v-3.3C5 16.8 4 14.6 4 10.5 4 5 7 3 12 3z"/><path fill="#fff" d="M9.3 7.4c.3-.2.6-.2.8.1l.9 1.3c.2.3.1.6-.1.8l-.4.4c.4 1 1.2 1.8 2.2 2.3l.4-.4c.2-.2.5-.3.8-.1l1.3.9c.3.2.3.6.1.9-.6.8-1.4 1.1-2.4.6a8 8 0 0 1-3.8-3.9c-.4-1-.1-2 .2-2.9z"/></svg>',
    ok: '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true"><circle cx="12" cy="7.5" r="3.2" fill="none" stroke="#fff" stroke-width="2.2"/><path fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" d="M8 13.2c2.5 1.6 5.5 1.6 8 0M12 15l-3.6 4.6M12 15l3.6 4.6"/></svg>',
    mail: '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true"><rect x="3" y="5.5" width="18" height="13" rx="2" fill="none" stroke="#fff" stroke-width="2"/><path d="M3.5 7l8.5 6 8.5-6" fill="none" stroke="#fff" stroke-width="2"/></svg>'
  };
  function shareSheet(url, title) {
    var enc = encodeURIComponent(url), txt = encodeURIComponent(title);
    var items = [
      ["Telegram", "https://t.me/share/url?url=" + enc + "&text=" + txt, "tg"],
      ["WhatsApp", "https://wa.me/?text=" + txt + "%20" + enc, "wa"],
      ["ВКонтакте", "https://vk.com/share.php?url=" + enc + "&title=" + txt, "vk"],
      ["Viber", "viber://forward?text=" + txt + "%20" + enc, "vb"],
      ["Одноклассники", "https://connect.ok.ru/offer?url=" + enc + "&title=" + txt, "ok"],
      ["Почта", "mailto:?subject=" + txt + "&body=" + txt + "%0A" + enc, "mail"]
    ];
    var back = el("div", "sheet"), box = el("div", "sheet__box");
    box.setAttribute("role", "dialog"); box.setAttribute("aria-label", "Поделиться");
    box.appendChild(el("div", "sheet__grip"));
    box.appendChild(el("p", "sheet__title", "Поделиться"));
    box.appendChild(el("p", "sheet__sub", title));
    var grid = el("div", "sheet__grid");
    items.forEach(function (it) {
      var a = el("a", "sheet__app sheet__app--" + it[2]);
      a.href = it[1]; a.target = "_blank"; a.rel = "noopener";
      var ico = el("span", "sheet__ico");
      ico.innerHTML = LOGOS[it[2]] || "";
      a.appendChild(ico);
      a.appendChild(el("span", "sheet__lbl", it[0]));
      a.addEventListener("click", function () { setTimeout(close, 300); });
      grid.appendChild(a);
    });
    box.appendChild(grid);
    var row = el("div", "sheet__link");
    var inp = el("input", "sheet__url"); inp.value = url; inp.readOnly = true;
    var copy = el("button", "btn btn--primary btn--sm", "Копировать"); copy.type = "button";
    copy.onclick = function () {
      var ok = function () { copy.textContent = "Скопировано ✓"; setTimeout(close, 900); };
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(url).then(ok, function () { inp.select(); document.execCommand("copy"); ok(); });
      else { inp.focus(); inp.select(); try { document.execCommand("copy"); } catch (err) {} ok(); }
    };
    row.appendChild(inp); row.appendChild(copy); box.appendChild(row);
    if (navigator.share) {
      var more = el("button", "btn btn--ghost btn--block sheet__more", "Другие приложения…"); more.type = "button";
      more.onclick = function () { close(); navigator.share({ title: title, url: url }).catch(function () {}); };
      box.appendChild(more);
    }
    back.appendChild(box); document.body.appendChild(back);
    requestAnimationFrame(function () { back.classList.add("is-open"); });
    function close() { back.classList.remove("is-open"); setTimeout(function () { back.remove(); }, 200); document.removeEventListener("keydown", esc); }
    function esc(e) { if (e.key === "Escape") close(); }
    document.addEventListener("keydown", esc);
    back.addEventListener("click", function (e) { if (e.target === back) close(); });
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-share]");
    if (!b) return;
    e.preventDefault(); e.stopImmediatePropagation();
    var url = b.getAttribute("data-share") || location.href;
    if (url.charAt(0) === "/") url = location.origin + url;
    shareSheet(url, b.getAttribute("data-share-title") || document.title);
  }, true);

  /* ---------- «← Назад» туда, откуда пришли ---------- */
  var NAV = "sl-nav";
  function navStack() { try { return JSON.parse(sessionStorage.getItem(NAV) || "[]"); } catch (e) { return []; } }
  function pageTitle() { var h = document.querySelector("main h1"); return h ? h.textContent.trim().replace(/\s+/g, " ") : document.title; }
  function trackNav() {
    var here = location.pathname + location.search, st = navStack();
    var i = st.map(function (x) { return x.u; }).lastIndexOf(here);
    if (i >= 0) st = st.slice(0, i + 1); else st.push({ u: here });
    st[st.length - 1].t = pageTitle();
    st = st.slice(-20);
    try { sessionStorage.setItem(NAV, JSON.stringify(st)); } catch (e) {}
    return st;
  }
  function smartBack(scope) {
    var st = navStack(), prev = st.length > 1 ? st[st.length - 2] : null;
    if (!prev) return;
    scope.querySelectorAll("a.back:not([data-fixed-back])").forEach(function (a) {
      var href = a.getAttribute("href") || "";
      var path = href.split("#")[0];
      if (path === prev.u || path === prev.u.split("?")[0]) return;  // и так ведёт туда, откуда пришли
      if (/^\/(login|register|logout|password-reset)\/|\/password\//.test(prev.u)) return;
      // Не ведём «назад» в форму (новая/изменить/заявка) и во вложенную страницу текущей
      var pp = prev.u.split("?")[0];
      if (/\/(new|edit|apply|confirm|add-role|phone)\/?$/.test(pp) || (pp !== location.pathname && pp.indexOf(location.pathname) === 0)) return;
      a.setAttribute("href", prev.u);
      var t = (prev.t || "Назад");
      a.textContent = "← " + (t.length > 34 ? "Назад" : t);
    });
  }

  /* ---------- Кнопка неактивна, пока поле пустое ---------- */
  function requireInput(scope) {
    scope.querySelectorAll("form[data-require-input]").forEach(function (f) {
      if (f.dataset.riInit) return; f.dataset.riInit = "1";
      var inputs = f.querySelectorAll("input[type=search], input[type=text]");
      var btn = f.querySelector("[data-require-btn]") || f.querySelector("button[type=submit], button:not([type])");
      function sync() {
        var any = Array.prototype.some.call(inputs, function (i) { return i.value.trim().length > 0; });
        // Фильтры тоже считаются условием поиска
        if (!any) any = !!f.querySelector("[data-filter-on]");
        if (btn) { btn.disabled = !any; btn.classList.toggle("is-inactive", !any); }
      }
      f.addEventListener("input", sync); f.addEventListener("change", sync); sync();
    });
  }

  /* Стёрли запрос — список сразу обновляется (тест 5.1, п. 16) */
  document.addEventListener("input", function (e) {
    var i = e.target;
    if (!i.matches || !i.matches('form[role=search] input[type=search]') || i.value !== "") return;
    var params = new URLSearchParams(location.search);
    if (!params.get(i.name)) return;
    clearTimeout(i._clr);
    i._clr = setTimeout(function () { var f = i.form; if (f.requestSubmit) f.requestSubmit(); else f.submit(); }, 350);
  });

  /* ---------- Подсказки при вводе: data-suggest='["…","…"]' или data-suggest-src="id-скрипта" ---------- */
  function norm(s) { return String(s || "").toLowerCase().replace(/ё/g, "е"); }
  function suggest(scope) {
    scope.querySelectorAll("input[data-suggest], input[data-suggest-src]").forEach(function (inp) {
      if (inp.dataset.sgInit) return; inp.dataset.sgInit = "1";
      var list;
      try {
        var src = inp.getAttribute("data-suggest-src");
        list = JSON.parse(src ? document.getElementById(src).textContent : inp.getAttribute("data-suggest"));
      } catch (e) { return; }
      inp.setAttribute("autocomplete", "off");
      var wrap = el("div", "sg"); inp.parentNode.insertBefore(wrap, inp); wrap.appendChild(inp);
      var pop = el("ul", "sg__pop"); pop.hidden = true; pop.setAttribute("role", "listbox"); wrap.appendChild(pop);
      var active = -1;
      function render() {
        var q = norm(inp.value).trim();
        pop.innerHTML = ""; active = -1;
        var hits = list.filter(function (x) { var n = norm(x); return !q || n.indexOf(q) >= 0; });
        hits.sort(function (a, b) { return (norm(a).indexOf(q) === 0 ? 0 : 1) - (norm(b).indexOf(q) === 0 ? 0 : 1); });
        hits = hits.slice(0, 8);
        if (!hits.length || (hits.length === 1 && norm(hits[0]) === q)) { pop.hidden = true; return; }
        hits.forEach(function (h) {
          var li = el("li", "sg__item", h); li.setAttribute("role", "option");
          li.addEventListener("mousedown", function (e) { e.preventDefault(); });
          li.addEventListener("click", function () { inp.value = h; pop.hidden = true; inp.dispatchEvent(new Event("change", { bubbles: true })); });
          pop.appendChild(li);
        });
        pop.hidden = false;
      }
      inp.addEventListener("input", render);
      inp.addEventListener("focus", function () {
        render();
        if (TOUCH && window.innerWidth < 720) setTimeout(function () { wrap.scrollIntoView({ behavior: "smooth", block: "start" }); }, 250);
      });
      inp.addEventListener("blur", function () { setTimeout(function () { pop.hidden = true; }, 150); });
      inp.addEventListener("keydown", function (e) {
        var items = pop.querySelectorAll(".sg__item"); if (pop.hidden || !items.length) return;
        if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); active = (active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length; items.forEach(function (x, i) { x.classList.toggle("is-active", i === active); }); }
        else if (e.key === "Enter" && active >= 0) { e.preventDefault(); items[active].click(); }
        else if (e.key === "Escape") pop.hidden = true;
      });
    });
  }

  /* ---------- Кнопка неяркая, пока не заполнены обязательные поля (тест 5.1, п. 10) ---------- */
  function filled(inp, form) {
    if (inp.disabled || inp.closest("[hidden]")) return true;
    if (inp.type === "checkbox" || inp.type === "radio") {
      return Array.prototype.some.call(form.querySelectorAll('input[name="' + inp.name + '"]'), function (g) { return g.checked; });
    }
    if (inp.type === "file") return !!(inp.files && inp.files.length);
    return !!String(inp.value || "").trim();
  }
  function requireFilled(scope) {
    scope.querySelectorAll("form[method=post]").forEach(function (f) {
      if (f.dataset.rfInit || f.hasAttribute("data-no-require")) return;
      var req = f.querySelectorAll("input[required], select[required], textarea[required]");
      if (!req.length) return;
      f.dataset.rfInit = "1";
      function sync() {
        var ok = Array.prototype.every.call(req, function (i) { return filled(i, f); });
        f.querySelectorAll("button[type=submit], button:not([type])").forEach(function (b) {
          if (b.closest(".step-pane[hidden]")) return;
          b.classList.toggle("is-inactive", !ok);
          b.setAttribute("aria-disabled", ok ? "false" : "true");
        });
      }
      f.addEventListener("input", sync); f.addEventListener("change", sync);
      f.addEventListener("click", function () { setTimeout(sync, 0); });
      sync();
    });
  }
  /* «Применить» в фильтрах — только когда что-то изменили (тест 5.1, п. 1.1) */
  function filterApply(scope) {
    scope.querySelectorAll("details[data-filters]").forEach(function (d) {
      var btn = d.querySelector("[data-filter-apply]");
      if (!btn || d.dataset.faInit) return;
      d.dataset.faInit = "1";
      var ctls = d.querySelectorAll("select, input");
      function state() { return Array.prototype.map.call(ctls, function (c) { return c.type === "checkbox" ? c.checked : c.value; }).join("|"); }
      var initial = state();
      function sync() { var same = state() === initial; btn.disabled = same; btn.classList.toggle("is-inactive", same); }
      d.addEventListener("change", sync); d.addEventListener("input", sync); sync();
    });
  }

  /* ---------- Фильтры: закрыть после «Применить», считать активные ---------- */
  document.addEventListener("submit", function (e) {
    e.target.querySelectorAll("details[data-filters][open]").forEach(function (d) { d.removeAttribute("open"); });
  });

  // Кнопки внизу экрана — только у длинных форм, короткие не перекрываем
  function markLongForms(scope) {
    scope.querySelectorAll("form[method=post]").forEach(function (f) {
      f.classList.toggle("form--long", f.offsetHeight > window.innerHeight * 1.1);
    });
  }
  function afterPage(scope) {
    scope = scope || document;
    markLongForms(scope);
    boostForms(scope);
    swapLinks(scope);
    requireInput(scope);
    requireFilled(scope);
    filterApply(scope);
    suggest(scope);
    if (scope === document || scope === document.body) {
      trackNav();
      smartBack(scope);
      messagesToToasts(scope);
    }
  }
  if (window.htmx) {
    htmx.config.historyCacheSize = 0;       // «Назад» — свежая страница, без устаревшего снимка
    htmx.config.refreshOnHistoryMiss = true;
    htmx.config.scrollIntoViewOnBoost = false;
    htmx.config.globalViewTransitions = false;
    htmx.config.defaultSwapStyle = "innerHTML";
    htmx.config.attributesToSettle = [];   // не возвращать старые классы полям после замены (иначе «двойные» списки)
  }
  window.SL = window.SL || {};
  window.SL.toast = toast;
  window.SL.share = shareSheet;
  document.addEventListener("DOMContentLoaded", function () {
    afterPage(document);
    if (!document.querySelector(".messages .alert--error")) goToError();
  });
})();
