"""Business rules for browsing news: "today" and day boundaries in the app timezone, paging, search
normalisation, and the nearest earlier day with news (Show older).

The service depends only on the domain and the store port; it never imports a web framework or a store client.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from cyber_news.domain import NewsPage
from cyber_news.store.base import NewsRepository

PAGE_SIZE = 100
"""Items per page, fixed by the spec ("first 100 … load more")."""

MAX_QUERY_LEN = 200
"""Search text is cut to this many characters after stripping (cost guard, plan decision 2)."""


class InvalidRequest(ValueError):
    """The request cannot be served as asked, e.g. blank search text; the route falls back to the day view."""


@dataclass(frozen=True)
class DayResult:
    """One page of the news published on ``day``, a calendar date in the app timezone."""

    day: date
    page: NewsPage


class NewsService:
    """Day view, search and Show older on top of a NewsRepository.

    ``clock`` returns the current instant as a tz-aware datetime; ``tz`` defines "today" and the day
    boundaries. Store errors (NewsStoreError and its subclasses) propagate unchanged, and cursors are passed
    to the store as they are, so the store validates them.
    """

    def __init__(self, repository: NewsRepository, tz: ZoneInfo, clock: Callable[[], datetime]) -> None:
        self._repository = repository
        self._tz = tz
        self._clock = clock

    def today(self) -> date:
        """The current date in the app timezone."""
        return self._clock().astimezone(self._tz).date()

    def day_bounds(self, day: date) -> tuple[datetime, datetime]:
        """The half-open UTC range ``[start, end)`` of ``day``: from local midnight to the next local midnight.

        On DST days the range spans 23 or 25 hours, so ``end`` is never computed as ``start + 24h``.
        """
        start = datetime.combine(day, time(), tzinfo=self._tz)
        end = datetime.combine(day + timedelta(days=1), time(), tzinfo=self._tz)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

    def news_for_day(self, day: date | None, cursor: str | None = None) -> DayResult:
        """A page of the news published on ``day`` (today when None), newest first.

        Pass ``cursor=None`` for the first page, then the previous page's ``next_cursor``.
        """
        day = self.today() if day is None else day
        start, end = self.day_bounds(day)
        page = self._repository.published_between(start, end, cursor=cursor, limit=PAGE_SIZE)
        return DayResult(day=day, page=page)

    def older_day_news(self, before: date) -> DayResult | None:
        """The first page of the nearest earlier day with news, or None when nothing is older than ``before``.

        The older day is the local date of the newest item published before ``before``'s local midnight, so
        empty days are skipped. Call it only when the user asks for older news: it costs a store call.
        """
        latest = self._repository.latest_published_before(self.day_bounds(before)[0])
        if latest is None:
            return None
        return self.news_for_day(latest.astimezone(self._tz).date())

    def search(self, text: str, cursor: str | None = None) -> NewsPage:
        """A page of the items matching ``text`` across all dates, best match first.

        The text is stripped and cut to MAX_QUERY_LEN characters. Raises InvalidRequest when it is blank.
        """
        query = text.strip()[:MAX_QUERY_LEN].rstrip()
        if not query:
            raise InvalidRequest("search text must not be blank")
        return self._repository.search(query, cursor=cursor, limit=PAGE_SIZE)
