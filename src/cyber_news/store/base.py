"""The news store port: the read operations the app needs, and the errors every store raises.

The port is owned by the policy side (service and web) and imports only the domain. Each store
implementation must honour the contract documented on ``NewsRepository``.
"""

from datetime import datetime
from typing import Protocol

from cyber_news.domain import NewsPage

SUBSTRING_MIN_LENGTH = 3
"""Plain search terms at least this long also match inside a word (``llm`` matches "LiteLLM")."""


class NewsStoreError(Exception):
    """The store failed or is unreachable; every backend failure is translated into this."""


class InvalidCursor(NewsStoreError):
    """The cursor is malformed, or was not issued by this store for this query."""


class CursorExpired(NewsStoreError):
    """The cursor was valid but its snapshot is gone (e.g. a keep-alive elapsed); the user must reload."""


class NewsRepository(Protocol):
    """Read-only access to indexed news.

    Contract shared by every method:

    - Instants passed in must be tz-aware; any offset is accepted. Instants returned are tz-aware UTC.
    - ``limit`` is the maximum number of items on a page and must be at least 1.
    - Paging: pass ``cursor=None`` for the first page, then the previous page's ``next_cursor``.
      Cursors are opaque strings, valid only with the same method and the same arguments. Following
      them returns every match exactly once, with no overlap or gap between pages, even when many
      items share a timestamp. The last page has ``next_cursor=None``.
    - ``NewsPage.total`` counts every match of the query, not only the items on the page.
    - Errors: a malformed or foreign cursor raises InvalidCursor, a cursor whose snapshot is gone
      raises CursorExpired, and any other backend failure raises NewsStoreError.
    """

    def published_between(self, start: datetime, end: datetime, *, cursor: str | None, limit: int) -> NewsPage:
        """Items published in the half-open range ``[start, end)``, newest first.

        Items without a publication date are never in a range.
        """
        ...

    def search(self, text: str, *, cursor: str | None, limit: int) -> NewsPage:
        """Items whose title or description matches ``text``, across all dates, best match first.

        ``text`` is free text. Shared semantics are fixed only for plain whole-word terms: matching is
        case-insensitive, and an item matches when every term occurs in its title or its description.
        When ``text`` is made only of plain words (word characters and whitespace), a term of at least
        ``SUBSTRING_MIN_LENGTH`` characters also occurs when it is part of a word (``llm`` in "LiteLLM");
        shorter terms, and every term of a query with operator syntax, match whole words only.
        Title matches rank above description-only matches, and a whole-word match never ranks below a
        substring-only match of the same field; equally relevant items are newest first, with undated
        items after dated ones. Operator syntax, if any, belongs to the implementation.
        """
        ...

    def latest_published_before(self, instant: datetime) -> datetime | None:
        """The newest publication instant strictly before ``instant``, in tz-aware UTC, or None.

        Items without a publication date are ignored. It needs no cursor or snapshot.
        """
        ...

    def ping(self) -> bool:
        """True when the store is reachable and can answer queries; never raises."""
        ...
