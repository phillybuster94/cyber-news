"""View model for news cards: the one place where raw feed data is made safe and readable for display.

Feed titles and descriptions may carry HTML and links may carry any scheme, so every value a template shows
comes from ``NewsCard``. Text is stripped of tags and returned as a plain ``str``, which Jinja autoescape
escapes again on output; links are kept only when they are ``http``/``https`` with a host.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from jinja2.filters import do_striptags

from cyber_news.domain import NewsItem

EXCERPT_MAX_LEN = 280
"""Excerpts are cut on a word boundary to at most this many characters, the ellipsis included."""

UNTITLED = "Untitled"
ELLIPSIS = "…"

_SAFE_SCHEMES = frozenset({"http", "https"})
# C0 controls, DEL and C1 controls; browsers drop tabs and newlines inside URLs, so "java\nscript:" runs as
# "javascript:". Removing them before parsing makes the check see the scheme the browser would see.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


@dataclass(frozen=True)
class NewsCard:
    """What a news card shows; every text field is plain text, never markup.

    - ``safe_link``: the article URL, or None when it is not ``http``/``https`` with a host (the title then
      renders as text, not as a link).
    - ``source``: the link's host name without ``www.``, or None without a safe link.
    - ``published_display`` ("24 Oct 2026 09:15") and ``published_iso`` are in the app timezone;
      ``relative_age`` is "3 h ago" style. All three are None for an undated item.
    """

    title: str
    excerpt: str
    safe_link: str | None
    source: str | None
    published_display: str | None
    published_iso: str | None
    relative_age: str | None

    @classmethod
    def from_item(cls, item: NewsItem, tz: ZoneInfo, now: datetime) -> "NewsCard":
        """Build the card for ``item``; ``now`` (tz-aware) is the instant relative ages are measured from."""
        link = safe_link(item.link)
        published = item.published.astimezone(tz) if item.published is not None else None
        return cls(
            title=plain_text(item.title) or UNTITLED,
            excerpt=excerpt(item.description),
            safe_link=link,
            source=source_name(link) if link is not None else None,
            published_display=published.strftime("%d %b %Y %H:%M") if published is not None else None,
            published_iso=published.isoformat() if published is not None else None,
            relative_age=relative_age(published, now) if published is not None else None,
        )


def plain_text(raw: str) -> str:
    """``raw`` with comments and tags removed, entities decoded and whitespace collapsed.

    The result is a plain ``str`` (not ``Markup``), so autoescape still escapes what entities decoded to.
    """
    return str(do_striptags(raw))


def excerpt(raw: str, max_len: int = EXCERPT_MAX_LEN) -> str:
    """The plain text of ``raw``, cut on a word boundary to at most ``max_len`` characters."""
    text = plain_text(raw)
    if len(text) <= max_len:
        return text
    limit = max_len - len(ELLIPSIS)
    cut = text[:limit]
    if not text[limit].isspace() and " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:-–—") + ELLIPSIS


def safe_link(raw: str | None) -> str | None:
    """``raw`` without control characters when it is an ``http``/``https`` URL with a host, else None."""
    if raw is None:
        return None
    link = _CONTROL_CHARS.sub("", raw).strip()
    try:
        parts = urlsplit(link)
        hostname = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in _SAFE_SCHEMES or not hostname:
        return None
    return link


def source_name(link: str) -> str:
    """The host name of a safe link, without a leading ``www.``."""
    hostname = urlsplit(link).hostname or ""
    return hostname.removeprefix("www.")


def relative_age(published: datetime, now: datetime) -> str:
    """How long before ``now`` the item was published: "just now", "5 min ago", "3 h ago" or "2 d ago".

    Items dated after ``now`` (feeds may post-date) read "in 5 min" and so on.
    """
    delta = now - published
    if abs(delta) < timedelta(minutes=1):
        return "just now"
    amount = _rounded_down(abs(delta))
    return f"{amount} ago" if delta > timedelta(0) else f"in {amount}"


def _rounded_down(delta: timedelta) -> str:
    if delta < timedelta(hours=1):
        return f"{delta // timedelta(minutes=1)} min"
    if delta < timedelta(days=1):
        return f"{delta // timedelta(hours=1)} h"
    return f"{delta.days} d"


def day_label(day: date) -> str:
    """A calendar day as shown in headings, e.g. "Thu 26 Mar 2026"."""
    return day.strftime("%a %d %b %Y")
