from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


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
            f"Description:\n{self.description[:500]}"
        )


@dataclass
class MatchResult:
    job_id: str
    score: int
    why_fits: list[str]
    watch_out: list[str]
    recommendation: str
