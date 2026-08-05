from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# Потолок длины описания у парсеров. Был 2000 — и это резало ТРЕБОВАНИЯ, которые
# в длинных вакансиях стоят в конце. Реальный случай 05.08.2026: Bybit «[Fiat]
# Fiat Operations Specialist - Brazil», описание 10 941 символ, требование
# «Fluency in English and Portuguese» на позиции 9 996 → гейт иностранных языков
# физически не мог его увидеть, и вакансия ушла кандидату.
# Описания живут только в памяти во время прогона (в БД не пишутся), поэтому
# запас дешёвый.
MAX_DESCRIPTION_CHARS = 12000

# Сколько описания уходит в промпт AI. Берём НАЧАЛО и КОНЕЦ: начало — суть роли,
# конец — требования (языки, гражданство, опыт, формат). Раньше брали только
# первые 1200 символов, то есть в модель шло маркетинговое вступление про компанию,
# а требования не доезжали.
_AI_HEAD_CHARS = 700
_AI_TAIL_CHARS = 800


def _sample_description(text: str) -> str:
    """Начало + конец описания для промпта AI (требования обычно в конце)."""
    text = text or ""
    if len(text) <= _AI_HEAD_CHARS + _AI_TAIL_CHARS:
        return text
    return (f"{text[:_AI_HEAD_CHARS]}\n"
            f"[…пропущена середина описания…]\n"
            f"{text[-_AI_TAIL_CHARS:]}")


@dataclass
class Job:
    id: str
    title: str
    company: str
    description: str
    url: str
    source: str
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_currency: str = "USD"
    location: Optional[str] = None
    is_remote: bool = False
    published_at: Optional[datetime] = None
    tags: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    match_role: Optional[str] = None
    match_reasons: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        salary = ""
        if self.salary_min or self.salary_max:
            lo = self.salary_min or ""
            hi = self.salary_max or ""
            salary = f"Salary: {lo}–{hi} {self.salary_currency}\n"

        location = f"Location: {self.location}\n" if self.location else ""
        remote = "Remote: yes\n" if self.is_remote else ""
        tags = f"Tags: {', '.join(self.tags)}\n" if self.tags else ""

        return (
            f"Title: {self.title}\n"
            f"Company: {self.company}\n"
            f"{salary}{location}{remote}{tags}"
            f"Description:\n{_sample_description(self.description)}"
        )


@dataclass
class MatchResult:
    job_id: str
    score: int
    why_fits: list[str]
    watch_out: list[str]
    recommendation: str
