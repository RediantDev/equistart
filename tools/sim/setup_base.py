"""Общие аккаунты: модератор Иван (назначается админ-командой, как в жизни)."""
import subprocess, sys
sys.path.insert(0, "/home/claude/sim")
from people import Person
from flows import register, login

IVAN = Person(0, "+79160000000", female=False, level="confident")
IVAN.last, IVAN.first = "Модератор", "Иван"


def ensure_moderator():
    b = register("base", IVAN, ["organizer"])
    if b is None:
        b = login("base", IVAN)
    subprocess.run(f". /home/claude/env.sh && cd /home/claude/equistart && python3 manage.py set_moderator {IVAN.phone}",
                   shell=True, check=True, capture_output=True)
    return login("base", IVAN, " (модератор)")
