"""Роль ai_automation должна узнавать профессию по её русскому названию.

Инцидент 15.08.2026: живая вакансия «AI-интегратор (B2B, клиентам)» получала 0
баллов с причиной «нет ролевых ключевых слов». Гейт знал слово "automation", но не
знал, как эта работа называется на русском рынке труда. Из-за этого был сделан и
неверный вывод — что запросы про AI-интеграцию бесполезны: вакансии находились
поиском, но не опознавались скорингом.
"""
from src.matcher.pre_filter import score_vacancy

DESC = ("Настройка и внедрение AI-автоматизаций под задачи бизнеса. "
        "Интеграции через API, подключение LLM, работа с n8n. Удалённо.")


class TestAiIntegratorRecognised:
    def test_ru_title_passes_gate(self):
        for title in ("AI-интегратор (B2B, клиентам)",
                      "AI-интегратор (автоматизация бизнес-процессов)",
                      "AI интегратор",
                      "Специалист по внедрению ИИ"):
            res = score_vacancy(title, DESC, "ai_automation")
            assert res["passed_gate"], f"{title}: {res['reasons'][0]}"
            assert res["score"] >= 42, f"{title}: {res['score']}"

    def test_en_title_still_works(self):
        # Регрессия: русский словарь не должен ломать то, что и так работало.
        for title in ("AI Automation Engineer", "No-code developer",
                      "Process Automation Specialist"):
            assert score_vacancy(title, DESC, "ai_automation")["passed_gate"], title


class TestNoOverreach:
    def test_plain_integrator_is_not_ai_role(self):
        # «Интегратор» без ИИ-контекста — это 1С и ERP, чужая профессия.
        for title, desc in (
            ("Интегратор 1С", "Внедрение и доработка 1С:УНФ, обмен данными, ERP."),
            ("Специалист по интеграции ERPNext", "Настройка ERPNext, миграция данных."),
        ):
            res = score_vacancy(title, desc, "ai_automation")
            assert not res["recommend"], f"{title} прошёл с {res['score']}"

    def test_test_automation_still_excluded(self):
        # Старое правило: «автоматизация тестирования» — это SDET, не наш профиль.
        res = score_vacancy(
            "Инженер по автоматизации тестирования",
            "Автотесты на Java, Selenium, CI/CD, покрытие регрессом.",
            "ai_automation")
        assert not res["recommend"], res["score"]
