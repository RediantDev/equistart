"""Ограничение неудачных попыток входа по телефону и по IP."""

import hashlib
import ipaddress

from django.conf import settings
from django.core.cache import cache


def client_ip(request) -> str:
    remote = request.META.get("REMOTE_ADDR", "")
    if remote in settings.TRUSTED_PROXY_IPS:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            candidate = forwarded.split(",")[-1].strip()
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                pass
    return remote


def _key(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.encode()).hexdigest()[:32]
    return f"login-fail:{kind}:{digest}"


def is_locked(phone: str | None, ip: str) -> bool:
    if phone and cache.get(_key("phone", phone), 0) >= settings.LOGIN_MAX_FAILURES_PER_PHONE:
        return True
    return cache.get(_key("ip", ip), 0) >= settings.LOGIN_MAX_FAILURES_PER_IP


def register_failure(phone: str | None, ip: str) -> None:
    keys = [_key("ip", ip)]
    if phone:
        keys.append(_key("phone", phone))
    for key in keys:
        cache.add(key, 0, settings.LOGIN_LOCKOUT_SECONDS)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, settings.LOGIN_LOCKOUT_SECONDS)


def reset(phone: str | None) -> None:
    if phone:
        cache.delete(_key("phone", phone))
