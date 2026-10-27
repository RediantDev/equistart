"""Конструктор езды (DIR-02): лист вводится построчно — так его удобно переносить из бланка."""

from django import forms

from . import dressage as D
from .models import DressageTest

HELP_MOV = "Одна строка — одно упражнение: «№ | буквы | упражнение | коэффициент». Коэффициент можно не писать (= 1)."
HELP_COL = "Одна строка — одна общая оценка: «описание | коэффициент»."


def parse_movements(text: str) -> list[dict]:
    out = []
    for i, line in enumerate(l for l in (text or "").splitlines() if l.strip()):
        parts = [x.strip() for x in line.split("|")]
        if len(parts) == 1:
            n, letters, desc, coef = str(i + 1), "", parts[0], "1"
        elif len(parts) == 2:
            n, letters, desc, coef = parts[0], "", parts[1], "1"
        elif len(parts) == 3:
            n, letters, desc, coef = parts[0], parts[1], parts[2], "1"
        else:
            n, letters, desc, coef = parts[0], parts[1], " | ".join(parts[2:-1]) or parts[2], parts[-1] or "1"
        out.append({"n": n or str(i + 1), "letters": letters, "text": desc, "coef": _coef(coef, line)})
    return out


def parse_collective(text: str) -> list[dict]:
    out = []
    for line in (l for l in (text or "").splitlines() if l.strip()):
        parts = [x.strip() for x in line.split("|")]
        out.append({"text": parts[0], "coef": _coef(parts[1] if len(parts) > 1 and parts[1] else "1", line)})
    return out


def _coef(raw, line):
    try:
        v = float(raw.replace(",", "."))
    except ValueError:
        raise forms.ValidationError(f"Не понял коэффициент в строке «{line[:60]}». Формат: «… | 2».")
    return int(v) if v == int(v) else v


def dump_movements(items) -> str:
    return "\n".join(f"{m.get('n', '')} | {m.get('letters', '')} | {m.get('text', '')} | {m.get('coef', 1)}" for m in items or [])


def dump_collective(items) -> str:
    return "\n".join(f"{m.get('text', '')} | {m.get('coef', 1)}" for m in items or [])


class DressageTestForm(forms.ModelForm):
    step = forms.ChoiceField(label="Шаг оценки", choices=[("0.5", "0,5"), ("0.1", "0,1")], initial="0.5")
    technique_movements = forms.CharField(label="Упражнения (техника)", widget=forms.Textarea(attrs={"rows": 12}), help_text=HELP_MOV)
    technique_collective = forms.CharField(label="Общие оценки (техника)", required=False, widget=forms.Textarea(attrs={"rows": 4}), help_text=HELP_COL)
    second_collective = forms.CharField(label="Оценки второй части", required=False, widget=forms.Textarea(attrs={"rows": 5}),
                                        help_text="Для детской езды — «Качество езды» (посадка, применение средств управления…); "
                                                  "для КЮР — «Артистизм». " + HELP_COL)

    class Meta:
        model = DressageTest
        fields = ["name", "short", "level", "kind", "arena", "duration_min", "penalty_scheme", "tie_rule", "min_horse_age", "source"]
        widgets = {"penalty_scheme": forms.Select(choices=D.PENALTY_CHOICES)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["penalty_scheme"].required = True
        self.fields["penalty_scheme"].help_text = "Как штрафуются ошибки в схеме — выберите по положению турнира."
        self.fields["tie_rule"].required = True
        self.fields["tie_rule"].choices = [("", "— выберите —")] + list(DressageTest.TieRule.choices)
        inst = self.instance
        if inst and inst.parts and not self.is_bound:
            first = inst.parts[0]
            self.initial.update(step=str(first.get("step", "0.5")), technique_movements=dump_movements(first.get("movements")),
                                technique_collective=dump_collective(first.get("collective")))
            if len(inst.parts) > 1:
                self.initial["second_collective"] = dump_collective(inst.parts[1].get("movements", []) + inst.parts[1].get("collective", []))

    def clean(self):
        c = super().clean()
        kind = c.get("kind")
        if not kind or self.errors:
            return c
        step = c.get("step", "0.5")
        tech = {"key": "technique", "title": D.PART_TITLES["technique"], "step": step,
                "movements": parse_movements(c.get("technique_movements")), "collective": parse_collective(c.get("technique_collective"))}
        parts = [tech]
        if kind in ("children", "freestyle"):
            key = "quality" if kind == "children" else "artistic"
            parts.append({"key": key, "title": D.PART_TITLES[key], "step": step, "movements": [],
                          "collective": parse_collective(c.get("second_collective"))})
        try:
            D.validate_parts(kind, parts)
        except ValueError as e:
            raise forms.ValidationError(str(e))
        self.instance.parts = parts
        return c

    def _post_clean(self):
        # лист собирается в clean(); если в нём ошибка, проверка модели повторила бы её на поле, которого нет в форме
        if self.errors:
            return
        super()._post_clean()
