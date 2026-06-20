from abc import ABC, abstractmethod
from ..models import Job


class BaseParser(ABC):
    name: str = "base"

    @abstractmethod
    def parse(self) -> list[Job]:
        """Возвращает список вакансий из источника."""
        ...
