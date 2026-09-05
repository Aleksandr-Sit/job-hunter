"""Пересмотр отказов HH: какие отказы вообще имеет смысл перепроверять описанием.

Повод — замер 05.09.2026 на боевом прогоне. HH отдаёт в RSS медианно 126 символов,
поэтому отказ «нет ролевых слов / нет крипто-контекста» выносится фактически по
заголовку. Из 227 новых вакансий HH предфильтр пропускал 4, ещё 17 упирались в
настоящие блокеры, а 206 отсеивались по огрызку.

Здесь проверяется не «работает ли скоринг», а ГРАНИЦА: что мы соглашаемся качать
заново, а что нет. Ошибка в эту сторону дорогая — качать отказ по английскому
бессмысленно (описание его только подтвердит), а платить за это ~0.8 МБ и 0.87 с
на вакансию придётся.
"""
from src.matcher.pre_filter import is_soft_reject, score_job
from src.models import Job


def _job(title, desc="", location=None):
    return Job(id="hh_1", title=title, company="Тест", description=desc,
               url="https://hh.ru/vacancy/1", source="hh.ru", location=location)


class TestSoftReject:
    def test_нет_ролевых_слов_это_мягкий_отказ(self):
        """Обычный отказ по огрызку — именно его и надо перепроверять."""
        j = _job("Начинающий специалист", "Регион: Самара")
        assert is_soft_reject(score_job(j))

    def test_прошедшая_отбор_не_кандидат(self):
        """Уже прошла — перекачивать нечего, её дообогащает другая стадия."""
        j = _job("Специалист поддержки криптобиржи",
                 "Поддержка пользователей криптовалютной биржи, удалённая работа")
        sc = score_job(j)
        assert sc["best"]["passed_gate"] and sc["best"]["recommend"]
        assert not is_soft_reject(sc)

    def test_свободный_английский_не_перевернуть_описанием(self):
        """Жёсткий блокер: полное описание его только подтвердит, качать незачем."""
        j = _job("Customer Support Specialist",
                 "Crypto exchange support. Fluent English is required. Remote.")
        assert not is_soft_reject(score_job(j))

    def test_dev_роль_не_кандидат(self):
        j = _job("Backend Developer", "Python, PostgreSQL, крипто-биржа")
        assert not is_soft_reject(score_job(j))

    def test_балл_ниже_порога_тоже_кандидат(self):
        """Гейт пустил, но боостов не из чего набрать — полный текст их даст.

        Этот случай легко потерять: он не «отказ гейта», а недобор балла, и
        отдельная проверка нужна, чтобы его не отсекли вместе с блокерами.
        """
        j = _job("Оператор", "Работа с криптовалютой")
        sc = score_job(j)
        if sc["best"]["passed_gate"] and not sc["best"]["recommend"]:
            assert is_soft_reject(sc)
