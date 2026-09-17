"""Описание вакансии должно доезжать до гейтов текстом, а не разметкой.

Ревизия 17.09.2026 нашла три источника, где описание терялось молча:
- Lever клал в описание `text` — а это НАЗВАНИЕ вакансии, не текст;
- Greenhouse отдаёт экранированный HTML, и в гейты уходили теги (25 из 25
  описаний Bitpanda начинались с `&lt;div`);
- CryptoJobsList больше не отдаёт `jobPostingJSONLD`, описание осталось
  заглушкой «должность at компания», а ссылка на вакансию вела в 404.
"""
from src.parsers.normalize import html_to_text
from src.parsers.web.cryptojoblist import CryptoJobListParser
from src.parsers.web.greenhouse import GreenhouseParser
from src.parsers.web.lever import LeverParser


class TestHtmlToText:
    def test_escaped_html_becomes_text(self):
        out = html_to_text("&lt;div&gt;Fluent English required&lt;/div&gt;")
        assert out == "Fluent English required"

    def test_raw_html_becomes_text(self):
        assert html_to_text("<p>Remote</p><ul><li>USDT</li></ul>") == "Remote\nUSDT"

    def test_plain_text_untouched(self):
        assert html_to_text("Просто текст") == "Просто текст"

    def test_entities_without_tags(self):
        assert html_to_text("Sales &amp; Support") == "Sales & Support"

    def test_empty(self):
        assert html_to_text(None) == "" and html_to_text("   ") == ""


class TestLever:
    @staticmethod
    def _item(**over):
        item = {
            "id": "abc",
            "text": "Web3 Operations Specialist",              # это ЗАГОЛОВОК
            "descriptionPlain": "You will run daily crypto operations and reconciliations.",
            "lists": [{"text": "Requirements",
                       "content": "<ul><li>USDT settlements</li><li>English B2</li></ul>"}],
            "additionalPlain": "We offer relocation support.",
            "categories": {"location": "Dubai", "commitment": "Full-time", "team": "Ops"},
            "hostedUrl": "https://jobs.lever.co/x/abc",
            "createdAt": 1757000000000,
        }
        item.update(over)
        return item

    def test_body_and_lists_and_additional_are_in_description(self):
        job = LeverParser.__new__(LeverParser)._parse_item(self._item(), "Binance")
        assert "daily crypto operations" in job.description
        assert "USDT settlements" in job.description and "English B2" in job.description
        assert "relocation support" in job.description
        assert "<li>" not in job.description and "<ul>" not in job.description

    def test_title_alone_is_no_longer_the_description(self):
        job = LeverParser.__new__(LeverParser)._parse_item(self._item(), "Binance")
        assert job.title == "Web3 Operations Specialist"
        assert job.description.strip() != job.title

    def test_workplace_type_decides_format(self):
        p = LeverParser.__new__(LeverParser)
        remote = p._parse_item(self._item(workplaceType="remote"), "X")
        hybrid = p._parse_item(self._item(workplaceType="hybrid"), "X")
        assert remote.is_remote is True
        assert hybrid.is_remote is False
        # формат обязан попасть в ТЕКСТ: скоринг читает описание, а не поля Job
        assert "on-site presence required" in hybrid.description

    def test_html_only_description_still_works(self):
        item = self._item(descriptionPlain=None,
                          description="<p>Support for crypto wallets</p>")
        job = LeverParser.__new__(LeverParser)._parse_item(item, "X")
        assert "Support for crypto wallets" in job.description


class TestGreenhouse:
    def test_escaped_html_is_unescaped_before_truncation(self):
        item = {"id": 1, "title": "Operations Intern",
                "content": "&lt;div&gt;&lt;p&gt;Fluent English required&lt;/p&gt;&lt;/div&gt;",
                "location": {"name": "Vienna"}, "absolute_url": "http://x"}
        job = GreenhouseParser.__new__(GreenhouseParser)._parse_item(item, "Bitpanda")
        assert job.description == "Fluent English required"
        assert "&lt;" not in job.description and "<div" not in job.description


class _FakeResponse:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class _FakeSession:
    def __init__(self, response):
        self._response, self.calls = response, []

    def get(self, url, **kw):
        self.calls.append(url)
        return self._response


class TestCryptoJobsList:
    @staticmethod
    def _parser(response=None):
        p = CryptoJobListParser.__new__(CryptoJobListParser)
        p._session = _FakeSession(response)
        return p

    def test_job_url_has_jobs_prefix(self):
        item = {"id": "1", "seoSlug": "ops-at-kleros", "jobTitle": "Ops", "companyName": "Kleros",
                "jobPostingJSONLD": '{"description": "desc"}'}
        job = self._parser()._parse_item(item)
        assert job.url == "https://cryptojobslist.com/jobs/ops-at-kleros"

    def test_description_comes_from_job_page(self, monkeypatch):
        monkeypatch.setattr("src.parsers.web.cryptojoblist.time.sleep", lambda s: None)
        html = ('<html><body><div class="JobView_description__DP1X0">'
                '<p>Job Description</p><p>We need P2P operations support in USDT.</p>'
                '</div></body></html>')
        p = self._parser(_FakeResponse(html))
        job = p._parse_item({"id": "2", "seoSlug": "p2p-at-x", "jobTitle": "P2P",
                             "companyName": "X", "tags": []})
        assert "P2P operations support in USDT" in job.description
        assert p._session.calls == ["https://cryptojobslist.com/jobs/p2p-at-x"]

    def test_site_footer_is_cut_off(self, monkeypatch):
        monkeypatch.setattr("src.parsers.web.cryptojoblist.time.sleep", lambda s: None)
        html = ("<html><body><main><p>Real duties here</p>"
                "<p>Related Salaries in Web3 Salary developer Salary manager</p>"
                "</main></body></html>")
        job = self._parser(_FakeResponse(html))._parse_item(
            {"id": "3", "seoSlug": "x-at-y", "jobTitle": "T", "companyName": "C", "tags": []})
        assert "Real duties here" in job.description
        assert "Salary developer" not in job.description, "подвал обманывает ролевой гейт"

    def test_page_failure_falls_back_to_stub(self, monkeypatch):
        monkeypatch.setattr("src.parsers.web.cryptojoblist.time.sleep", lambda s: None)
        job = self._parser(_FakeResponse("", status=503))._parse_item(
            {"id": "4", "seoSlug": "s", "jobTitle": "Support", "companyName": "Acme",
             "jobLocation": "Remote", "tags": ["crypto"]})
        assert "Support at Acme" in job.description

    def test_jsonld_still_wins_without_extra_request(self):
        p = self._parser()
        job = p._parse_item({"id": "5", "seoSlug": "s", "jobTitle": "T", "companyName": "C",
                             "jobPostingJSONLD": '{"description": "Full text from JSONLD"}'})
        assert job.description == "Full text from JSONLD"
        assert p._session.calls == [], "лишний запрос к странице"
