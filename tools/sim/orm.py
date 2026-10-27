"""Только чтение из БД для сверки (что создалось) — не используется для действий пользователей."""
import json, subprocess

def q(code):
    full = ("import json\nfrom apps.tournaments.models import *\nfrom apps.entries.models import *\nfrom apps.conduct.models import *\n"
            "from apps.profiles.models import *\nout=None\n" + code + "\nprint('@@'+json.dumps(out, ensure_ascii=False, default=str))")
    r = subprocess.run(". /home/claude/env.sh && cd /home/claude/equistart && python3 manage.py shell", shell=True, input=full,
                       capture_output=True, text=True)
    line = [l for l in r.stdout.splitlines() if l.startswith("@@")]
    if not line:
        raise RuntimeError(r.stderr[-2000:])
    return json.loads(line[-1][2:])

def classes_of(pk):
    return q(f"""out=[dict(id=c.id, program=c.program_id, pname=c.program.name, day=str(c.program.day), heights=c.program.heights, height=c.height,
    name=c.name, by_from=c.birth_year_from, by_to=c.birth_year_to, ha_min=c.horse_age_min, ha_max=c.horse_age_max, kind=c.horse_kind,
    rank_rule=c.rank_rule, rank=c.rank, disc=c.program.discipline) for c in TournamentClass.objects.filter(program__tournament_id={pk}).select_related('program').order_by('program__day','program__position','position')]""")
