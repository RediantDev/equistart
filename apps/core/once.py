"""Одна отправка формы — одно действие (тест 5, п. 24.5, 25.2).

В форму кладётся случайный токен; повторная отправка того же токена (двойное нажатие,
медленная сеть, «Назад» + «Отправить») не создаёт дубль.
"""
import secrets

KEY = "once_tokens"


def new_token() -> str:
    return secrets.token_urlsafe(16)


def already_used(request) -> bool:
    """True — эту форму уже отправляли. Иначе запоминает токен и возвращает False."""
    token = (request.POST.get("once") or "")[:64]
    if not token:
        return False
    used = request.session.get(KEY, [])
    if token in used:
        return True
    request.session[KEY] = (used + [token])[-50:]
    return False
