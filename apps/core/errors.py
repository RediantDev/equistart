"""Страницы ошибок на русском (ТЗ UI-03): что случилось, почему и что делать дальше."""

import logging

from django.http import JsonResponse
from django.template import loader
from django.http import HttpResponse

log = logging.getLogger(__name__)

PAGES = {
    400: {
        "title": "Не удалось обработать запрос",
        "why": "Сервер получил данные, которые не смог принять: форма отправлена не полностью, ссылка повреждена или страница слишком давно открыта.",
        "todo": ["Вернитесь назад и отправьте форму ещё раз.", "Если ошибка повторяется — обновите страницу и заполните форму заново."],
    },
    403: {
        "title": "Нет доступа к этой странице",
        "why": "У вашего аккаунта нет роли, которая открывает эту страницу. Например, заявки турнира видят только организатор и судьи этого турнира, а лист судьи — только сам судья.",
        "todo": ["Проверьте, что вы вошли в нужный аккаунт.", "Если вас пригласили в команду турнира — примите приглашение в кабинете.",
                 "Если доступ должен быть — напишите организатору турнира."],
    },
    404: {
        "title": "Страница не найдена",
        "why": "Ссылка устарела или в ней ошибка. Возможно, турнир или заявку удалили либо они ещё не опубликованы.",
        "todo": ["Проверьте адрес страницы.", "Найдите турнир в календаре."],
    },
    500: {
        "title": "На сервере произошла ошибка",
        "why": "Это наша ошибка, а не ваша. Мы уже получили сведения о ней.",
        "todo": ["Попробуйте повторить действие через минуту.", "Если турнир идёт сейчас и ошибка мешает судейству — сообщите в поддержку."],
    },
    "csrf": {
        "title": "Страница устарела",
        "why": "Форма была открыта слишком долго, вы вышли из аккаунта в другой вкладке или вошли под другим пользователем. Для защиты данных сервер не принял отправку.",
        "todo": ["Обновите страницу и отправьте форму ещё раз — введённое придётся повторить.", "Если вы вышли из аккаунта — войдите снова."],
    },
}


def _wants_json(request):
    return request is not None and request.headers.get("X-Requested-With") == "fetch"


def _render(request, key, status, detail=""):
    page = dict(PAGES[key])
    if detail:
        page["detail"] = detail
    if _wants_json(request):
        return JsonResponse({"ok": False, "error": page["title"] + ". " + (detail or page["why"])}, status=status)
    try:
        tpl = loader.get_template("errors/page.html")
        html = tpl.render({"page": page, "status": status}, request if key != 500 else None)
    except Exception:  # шаблон или контекст сломаны — отдаём простую страницу
        log.exception("error page render failed")
        html = loader.get_template("errors/plain.html").render({"page": page, "status": status})
    return HttpResponse(html, status=status)


def _message(exception):
    msg = str(exception or "")
    # Свои сообщения в коде — по-русски; технические английские тексты Django не показываем.
    return msg if msg and any("а" <= ch.lower() <= "я" for ch in msg) else ""


def bad_request(request, exception=None):
    return _render(request, 400, 400, _message(exception))


def permission_denied(request, exception=None):
    return _render(request, 403, 403, _message(exception))


def page_not_found(request, exception=None):
    return _render(request, 404, 404, _message(exception))


def server_error(request):
    return _render(request, 500, 500)


def csrf_failure(request, reason=""):
    return _render(request, "csrf", 403)
