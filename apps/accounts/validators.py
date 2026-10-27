from django.contrib.auth.password_validation import UserAttributeSimilarityValidator
from django.core.exceptions import ValidationError


class PersonalDataSimilarityValidator(UserAttributeSimilarityValidator):
    """Тот же проверяющий Django, но с сообщением без неверного падежа («похож на фамилия»)."""

    def validate(self, password, user=None):
        try:
            super().validate(password, user)
        except ValidationError:
            raise ValidationError(
                "Пароль слишком похож на ваши личные данные (имя, фамилию, телефон или почту). Придумайте другой.",
                code="password_too_similar",
            )
