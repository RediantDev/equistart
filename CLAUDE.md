# EquiStart — правила работы для агента

Заказчик: Иван (модератор и владелец продукта). Общаться по-русски, кратко и по делу.
Исполнитель: Claude. Продукт строится с нуля по `docs/TZ_v2.md` — это главный источник требований.

## Перед работой
1. Прочитать `docs/TZ_v2.md` (особенно раздел 2 «Неизменные принципы» и раздел 19 «Журнал решений»).
2. Прочитать `docs/QUESTIONS.md` — открытые вопросы; не решать их за заказчика.
3. Прогнать `python manage.py test`.

## Правила
- Не придумывать спортивные формулы, обязательные документы, роли, условия отмены и возврата. Если не хватает решения — добавить вопрос в `docs/QUESTIONS.md` и продолжать независимую работу.
- Права проверяются на сервере в каждой view и запросе. Скрытая кнопка — не защита. На каждую новую view — тест на чужой объект и гостя.
- Заявка — контейнер независимых позиций: решение, допуск, бронь, оплата — отдельные поля/модели.
- Деньги — целые копейки (`BigIntegerField`), не float/Decimal в рублях.
- Даты турнира — в часовом поясе места проведения; в БД — UTC.
- Изменения, влияющие на конкуренцию (последний денник, решения, публикация), — в транзакции с `select_for_update`; повтор запроса не создаёт дубль.
- Публичные страницы выдают только разрешённые поля (ТЗ разделы 6, 7, 10): без телефонов и полных дат рождения.
- Новые изменения схемы — новыми миграциями; применённые миграции не править.
- Интерфейс: серверные шаблоны + HTMX, общие компоненты из `templates/partials/` и `static/css/app.css`. Проверять ширины 1440 и 360.
- Коммиты небольшие, по законченному сценарию; CI должен быть зелёным.

## Окружение
- Зависимости: `pip install -r requirements.txt` (Python 3.13). Нужен PostgreSQL 16; БД и пользователь `equistart`/`equistart` на 127.0.0.1 (или `DATABASE_URL`).
- Если PyPI недоступен в окружении: пакеты можно взять из исходников GitHub (django, asgiref, sqlparse, psycopg, whitenoise) и добавить в PYTHONPATH.
- Окончательная проверка — GitHub Actions (`.github/workflows/ci.yml`).

## Этапы (ТЗ раздел 16)
0–5 готовы (основа · аккаунты и турниры · спортсмены, лошади, заявки · протоколы и результаты · деньги · денники и услуги). 6 — пилот: код готов (подтверждение телефона по СМС, печать протоколов, nginx, копии, `docs/DEPLOY.md`); осталось развернуть на российском хостинге и провести реальный турнир.

## Дизайн
- Значения — `design/tokens.json` → `python design/build_tokens.py` → `static/css/tokens.css`. Компоненты — `static/css/app.css`. Правила — `design/README.md`.
- Дизайн-система в Claude: https://claude.ai/artifact/7b6juh3zbmci2Sab5KWPeb (собрана из этих файлов; при изменении токенов/компонентов — обновить и её).
- Макеты экранов: https://claude.ai/artifact/9vALzjx2Anf1riqWsy1mAG (календарь, турнир, мастер, заявка, судья и протокол на телефоне).

## Ближайшие задачи
1. Получить ответы по `docs/QUESTIONS.md` и внести их в ТЗ (раздел 19).
2. Развернуть стенд по `docs/DEPLOY.md` (хостинг и домен выбирает заказчик), включить подтверждение телефона.
3. Пилотный турнир; правки по обратной связи.

## Устройство
- `apps/tournaments`: Venue, Tournament (рабочая копия + снимок публикации `snapshot`/`pub_*`), ProgramItem, TournamentClass, TeamMember; `services.py` — права (`roles_of`, `can_manage`), submit/approve/return/cancel.
- `apps/profiles`: Athlete (один управляющий, делегаты, связь с аккаунтом), Horse (управляет только создатель), документы лошади, Offer.
- `apps/entries`: Application (идемпотентность), Entry (решение, допуск, отмена — отдельные поля), ChangeProposal. Связь с деньгами — `money_hook` → `apps/finance/hooks.py`.
- `apps/conduct`: StartSlot, StartListPublication, Result, ResultPublication. В порядок — принятые+допущенные+`stables.stable_dependency_ok`. Публика видит только опубликованные версии.
- `apps/finance`: Charge (суммы считаются из журналов: Adjustment, Allocation→Payment, Refund), касса для ролей organizer/cashier. Отмена турнира → `tournament_cancelled`.
- `apps/stables`: StableCategory, Booking (вместимость под `select_for_update` категории), Service, ServiceOrder, Delivery.
- `apps/accounts`: телефон-логин, `verification.py` + `sms.py` (console/smsru), `PhoneVerificationMiddleware`, команды `set_moderator`, `set_password`.
- Юридические тексты — `templates/legal/`; реквизиты оператора — настройки `OPERATOR_*`.
