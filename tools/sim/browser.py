"""Простой «браузер» для симуляции пользователей: ходит по ссылкам и отправляет формы как человек."""
import json, re, time, random
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

BASE = "http://127.0.0.1:8000"
LOG = open("/home/claude/sim/requests.jsonl", "a")
FRICTION = open("/home/claude/sim/friction.jsonl", "a")

def friction(scenario, persona, where, what, severity="medium", **kw):
    FRICTION.write(json.dumps(dict(scenario=scenario, persona=persona, where=where, what=what, severity=severity, **kw), ensure_ascii=False) + "\n")
    FRICTION.flush()

class Page:
    def __init__(self, resp, url):
        self.resp, self.url, self.status = resp, url, resp.status_code
        self.html = resp.text if "html" in resp.headers.get("content-type", "") else ""
        self.soup = BeautifulSoup(self.html, "lxml") if self.html else None
    def messages(self):
        if not self.soup: return []
        out = []
        for el in self.soup.select(".messages li, .message, .alert, [role=alert], .toast"):
            t = " ".join(el.get_text(" ").split())
            if t and t not in out: out.append(t)
        return out
    def errors(self):
        if not self.soup: return []
        out = []
        for el in self.soup.select(".errorlist li, .field__error, .form-error, .invalid-feedback"):
            t = " ".join(el.get_text(" ").split())
            if t and t not in out: out.append(t)
        return out
    def text(self):
        return " ".join(self.soup.get_text(" ").split()) if self.soup else ""
    def links(self):
        return [(" ".join(a.get_text(" ").split()), urljoin(self.url, a["href"])) for a in self.soup.select("a[href]")] if self.soup else []
    def find_link(self, *needles):
        for n in needles:
            for t, h in self.links():
                if n.lower() in t.lower(): return h
        return None
    def forms(self):
        return self.soup.find_all("form") if self.soup else []
    def form(self, action_contains=None, button=None, has_field=None):
        for f in self.forms():
            act = urljoin(self.url, f.get("action") or self.url)
            if action_contains and action_contains not in act: continue
            if button and not any(button.lower() in (b.get_text(" ") + (b.get("value") or "")).lower() for b in f.find_all(["button", "input"]) if b.name == "button" or b.get("type") == "submit"):
                continue
            if has_field and not f.find(attrs={"name": has_field}): continue
            return f
        return None

def form_defaults(form):
    data = []
    for el in form.find_all(["input", "select", "textarea"]):
        name = el.get("name")
        if not name or el.has_attr("disabled"): continue
        if el.name == "input":
            typ = (el.get("type") or "text").lower()
            if typ in ("submit", "button", "file", "image", "reset"): continue
            if typ in ("checkbox", "radio"):
                if el.has_attr("checked"): data.append((name, el.get("value", "on")))
                continue
            data.append((name, el.get("value", "")))
        elif el.name == "select":
            opt = el.find("option", selected=True) or (el.find("option") if not el.has_attr("multiple") else None)
            if opt is not None: data.append((name, opt.get("value", opt.get_text())))
        else:
            data.append((name, el.get_text()))
    return data

class Browser:
    def __init__(self, scenario, persona, level="confident"):
        self.s = requests.Session()
        self.s.trust_env = False
        self.scenario, self.persona, self.level = scenario, persona, level
        self.page = None
        self.clicks = 0
    def _log(self, method, url, resp, ms, note=""):
        LOG.write(json.dumps(dict(sc=self.scenario, p=self.persona, lvl=self.level, m=method, url=urlparse(url).path,
                                  st=resp.status_code, ms=round(ms), kb=round(len(resp.content) / 1024, 1), note=note), ensure_ascii=False) + "\n")
    def get(self, url, note=""):
        url = urljoin(BASE, url); t = time.perf_counter()
        r = self.s.get(url, allow_redirects=True); ms = (time.perf_counter() - t) * 1000
        self._log("GET", url, r, ms, note); self.clicks += 1
        self.page = Page(r, r.url); return self.page
    def post(self, url, data, files=None, note="", headers=None):
        url = urljoin(BASE, url)
        tok = self.s.cookies.get("csrftoken")
        if isinstance(data, dict): data = list(data.items())
        if tok and not any(k == "csrfmiddlewaretoken" for k, _ in data): data = data + [("csrfmiddlewaretoken", tok)]
        h = {"Referer": url, "X-CSRFToken": tok or ""}; h.update(headers or {})
        t = time.perf_counter()
        r = self.s.post(url, data=data, files=files, allow_redirects=True, headers=h); ms = (time.perf_counter() - t) * 1000
        self._log("POST", url, r, ms, note); self.clicks += 1
        if "json" in r.headers.get("content-type", ""):
            return r
        self.page = Page(r, r.url); return self.page
    def submit(self, form, overrides=None, extra=None, button_name=None, button_value=None, files=None, note="", drop=()):
        data = form_defaults(form)
        overrides = overrides or {}
        keys = set(overrides)
        data = [(k, v) for k, v in data if k not in keys and k not in drop]
        for k, v in overrides.items():
            if isinstance(v, (list, tuple)):
                data += [(k, x) for x in v]
            elif v is not None:
                data.append((k, v))
        if extra: data += list(extra)
        if button_name: data.append((button_name, button_value or ""))
        action = urljoin(self.page.url, form.get("action") or self.page.url)
        return self.post(action, data, files=files, note=note)
    def click(self, *needles, note=""):
        href = self.page.find_link(*needles)
        if not href:
            return None
        return self.get(href, note=note)
    def friction(self, where, what, severity="medium", **kw):
        friction(self.scenario, self.persona, where, what, severity, level=self.level, **kw)
