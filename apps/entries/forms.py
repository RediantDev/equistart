

"""Формы заявок."""

from django import forms

from apps.profiles.models import COLORS, RANKS, SEXES

# ---------------- быстрое добавление пары судьёй (тест 4) ----------------


class QuickAddForm(forms.Form):
    """Судья вносит пару вручную, когда участник не смог подать заявку сам. Профили не становятся «своими» у судьи."""

    athlete_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    last_name = forms.CharField(label="Фамилия", max_length=100, required=False)
    first_name = forms.CharField(label="Имя", max_length=100, required=False)
    middle_name = forms.CharField(label="Отчество", max_length=100, required=False)
    birth_date = forms.DateField(label="Дата рождения", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    rank = forms.ChoiceField(label="Разряд", choices=[("", "— выберите —")] + RANKS, required=False)
    club = forms.CharField(label="Клуб", max_length=200, required=False)
    region = forms.CharField(label="Регион", max_length=120, required=False)
    fksr_number = forms.CharField(label="Номер ФКСР", max_length=40, required=False)
    coach_name = forms.CharField(label="Тренер (ФИО)", max_length=200, required=False)
    coach_phone = forms.CharField(label="Телефон тренера", max_length=32, required=False, help_text="Виден только судьям и организатору.")

    horse_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    horse_name = forms.CharField(label="Кличка", max_length=100, required=False)
    horse_year = forms.IntegerField(label="Год рождения", required=False, min_value=1980, max_value=2100)
    horse_passport = forms.CharField(label="Номер паспорта ФКСР", max_length=40, required=False)
    horse_sex = forms.ChoiceField(label="Пол", choices=[("", "—")] + SEXES, required=False)
    horse_color = forms.ChoiceField(label="Масть", choices=[("", "—")] + COLORS, required=False)
    horse_breed = forms.CharField(label="Порода", max_length=100, required=False)
    horse_sire = forms.CharField(label="Отец", max_length=100, required=False)
    horse_birthplace = forms.CharField(label="Место рождения", max_length=200, required=False)
    horse_owner = forms.CharField(label="Владелец", max_length=200, required=False)

    classes = forms.MultipleChoiceField(label="Маршрут и зачёт", widget=forms.CheckboxSelectMultiple, error_messages={"required": "Отметьте хотя бы один зачёт."})

    REQUIRED_ATHLETE = ("last_name", "first_name", "birth_date", "rank")
    REQUIRED_HORSE = ("horse_name", "horse_year")

    def __init__(self, tournament, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tournament = tournament
        from apps.tournaments.models import TournamentClass

        self.class_objs = {str(c.pk): c for c in TournamentClass.objects.filter(program__tournament=tournament, merged_into__isnull=True)
                           .select_related("program").order_by("program__day", "program__start_time", "program__position", "position")}
        self.fields["classes"].choices = [(k, f"{c.program.name} · {c.label}") for k, c in self.class_objs.items()]
        for f in self.REQUIRED_ATHLETE + self.REQUIRED_HORSE:
            self.fields[f].show_required = True

    def clean_fksr_number(self):
        from apps.profiles.models import FKSR_ERROR, fksr_valid

        v = self.cleaned_data.get("fksr_number", "")
        if not fksr_valid(v):
            raise forms.ValidationError(FKSR_ERROR)
        return v

    def clean_horse_passport(self):
        from apps.profiles.models import FKSR_ERROR, fksr_valid

        v = self.cleaned_data.get("horse_passport", "")
        if not fksr_valid(v):
            raise forms.ValidationError(FKSR_ERROR)
        return v

    def clean(self):
        from apps.profiles.models import Athlete, Horse

        c = super().clean()
        if c.get("athlete_id"):
            c["athlete"] = Athlete.objects.filter(pk=c["athlete_id"], archived=False).first()
            if not c["athlete"]:
                self.add_error(None, "Выбранный спортсмен не найден — найдите заново или заполните поля.")
        else:
            for f in self.REQUIRED_ATHLETE:
                if not c.get(f):
                    self.add_error(f, "Обязательное поле.")
        if c.get("horse_id"):
            c["horse"] = Horse.objects.filter(pk=c["horse_id"], archived=False).first()
            if not c["horse"]:
                self.add_error(None, "Выбранная лошадь не найдена — найдите заново или заполните поля.")
        else:
            for f in self.REQUIRED_HORSE:
                if not c.get(f):
                    self.add_error(f, "Обязательное поле.")
        c["class_list"] = [self.class_objs[k] for k in c.get("classes") or [] if k in self.class_objs]
        return c
