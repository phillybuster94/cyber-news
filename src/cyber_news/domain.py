"""News entities shared by the store port, the service and the web layer; they import nothing from the project."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class NewsItem:
    """One news entry as stored; the content is raw feed data, sanitised only for display.

    ``published`` is tz-aware UTC, or None when the source had no usable date.
    """

    title: str
    description: str
    link: str | None
    published: datetime | None


@dataclass(frozen=True)
class NewsPage:
    """One page of results; ``total`` counts every match, not only the items on this page."""

    items: tuple[NewsItem, ...]
    total: int
    next_cursor: str | None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None
