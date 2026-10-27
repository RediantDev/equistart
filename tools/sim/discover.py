import sys, json; sys.path.insert(0, "/home/claude/sim")
from crawl import crawl, find
from flows import login
from people import Person
from orm import q

def run(label, person, goals):
    b = login("crawl", person, " " + label)
    s = crawl(b)
    res = {}
    for g, p in goals.items():
        r = find(s, p)
        res[g] = {"clicks": r[0], "path": " → ".join(r[1][1:])} if r else {"clicks": None, "path": "не найдено за 4 клика"}
    return res

out = {}
out["Судья-организатор (S2)"] = run("судья", Person(201, "+79182000001"), {
    "Рассмотреть заявки": r"/organizer/t/1/entries/", "Проведение турнира": r"/organizer/t/1/conduct/$",
    "Порядок стартов маршрута": r"/conduct/p/\d+/$", "Экран судьи у поля": r"/conduct/p/\d+/(field|live)/",
    "Команда турнира": r"/organizer/t/1/team/", "Публичные результаты": r"/t/1/\?tab=results"})
# тренер S2: idx 210 (первый тренер)
coach_phone = ["+79182000210"]
out["Тренер (S2)"] = run("тренер", Person(210, coach_phone[0]), {
    "Найти турнир и подать заявку": r"/t/\d+/apply/", "Мои заявки": r"/my/applications/", "Заявка и решения": r"/applications/\d+/",
    "Добавить спортсмена": r"/athletes/new/", "Добавить лошадь": r"/horses/new/", "Стартовый протокол": r"/t/\d+/\?tab=starts",
    "Результаты": r"/t/\d+/\?tab=results"})
json.dump(out, open("/home/claude/sim/discover.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
