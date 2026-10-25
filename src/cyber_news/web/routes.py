"""HTTP routes: the page, the Load more and Show older fragments, and the health probes.

Routes parse and validate the query string, call NewsService, and render. Errors raised here or by the
service and the store are turned into responses by ``cyber_news.web.errors``.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from werkzeug.exceptions import BadRequest

from cyber_news.domain import NewsPage
from cyber_news.service import DayResult, InvalidRequest, NewsService
from cyber_news.store.base import NewsRepository
from cyber_news.web.view import NewsCard, day_label

EXTENSION_KEY = "cyber_news"

MIN_DAY = date(1970, 1, 1)
MAX_DAY = date(9998, 12, 31)
"""Accepted ``date``/``before`` range; it keeps the next local midnight and the UTC bounds representable."""

_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")

bp = Blueprint("news", __name__, template_folder="templates", static_folder="static", static_url_path="/static")


@dataclass(frozen=True)
class WebContext:
    """What the routes need, stored in ``app.extensions[EXTENSION_KEY]`` by ``create_app``."""

    service: NewsService
    repository: NewsRepository
    tz: ZoneInfo
    clock: Callable[[], datetime]


@bp.app_template_filter("day_label")
def _day_label_filter(day: date) -> str:
    return day_label(day)


@bp.app_context_processor
def _template_globals() -> dict[str, str]:
    return {"timezone_name": str(_context().tz)}


@bp.get("/")
def index() -> ResponseReturnValue:
    """The page: search when ``q`` is not blank, else the no-JS Show older fallback (``before``), else a day."""
    q = request.args.get("q", "")
    cursor = request.args.get("cursor") or None
    if q.strip():
        return _render_search_page(q, cursor)
    before = request.args.get("before")
    if before is not None:
        older_day = parse_day(before)
        if older_day is not None:
            return _older_day_redirect(older_day)
        return _render_day_page(None, None, alert=_bad_date_alert(before))
    raw_day = request.args.get("date") or None
    if raw_day is None:
        return _render_day_page(None, cursor)
    day = parse_day(raw_day)
    if day is None:
        # A cursor belongs to the day it was issued for, so the fallback to today starts at the first page.
        return _render_day_page(None, None, alert=_bad_date_alert(raw_day))
    return _render_day_page(day, cursor)


@bp.get("/news")
def items() -> ResponseReturnValue:
    """The Load more fragment: the next page of a search (``q``) or of a day (``date``); ``cursor`` is required."""
    cursor = request.args.get("cursor")
    if not cursor:
        raise BadRequest("cursor is required")
    q = request.args.get("q")
    if q is not None:
        page = _context().service.search(q, cursor=cursor)
    else:
        page = _context().service.news_for_day(_required_day("date"), cursor=cursor).page
    return render_template("_news_items.html", cards=_cards(page), page=page)


@bp.get("/news/older")
def older() -> ResponseReturnValue:
    """The Show older fragment: the section of the nearest earlier day with news, or "No older news"."""
    result = _context().service.older_day_news(_required_day("before"))
    if result is None:
        return render_template("_message.html", message="No older news", kind="no-older")
    return render_template("_day_section.html", result=result, cards=_cards(result.page), no_older=False)


@bp.get("/healthz")
def healthz() -> ResponseReturnValue:
    """Liveness: the process answers; it never touches the store."""
    return {"status": "ok"}


@bp.get("/readyz")
def readyz() -> ResponseReturnValue:
    """Readiness: 200 when the store answers a ping, else 503."""
    if _context().repository.ping():
        return {"status": "ready"}
    return {"status": "unavailable"}, 503


def parse_day(raw: str) -> date | None:
    """A ``YYYY-MM-DD`` date between MIN_DAY and MAX_DAY, or None for anything else."""
    if not _ISO_DAY.fullmatch(raw):
        return None
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        return None
    return day if MIN_DAY <= day <= MAX_DAY else None


def _context() -> WebContext:
    return current_app.extensions[EXTENSION_KEY]


def _cards(page: NewsPage) -> list[NewsCard]:
    context = _context()
    now = context.clock()
    return [NewsCard.from_item(item, context.tz, now) for item in page.items]


def _required_day(name: str) -> date:
    """The day in query parameter ``name``; fragments never fall back to today, so a bad value is a 400."""
    day = parse_day(request.args.get(name, ""))
    if day is None:
        raise InvalidRequest(f"{name} must be a date as YYYY-MM-DD")
    return day


def _bad_date_alert(raw: str) -> str:
    return f"“{raw}” is not a valid date (use YYYY-MM-DD), so today's news is shown."


def _older_day_redirect(before: date) -> ResponseReturnValue:
    result = _context().service.older_day_news(before)
    if result is None:
        return _render_day_page(before, None, alert="No older news.", no_older=True)
    return redirect(url_for("news.index", date=result.day.isoformat()), code=302)


def _render_day_page(day: date | None, cursor: str | None, alert: str | None = None, no_older: bool = False) -> str:
    service = _context().service
    result: DayResult = service.news_for_day(day, cursor=cursor)
    today = service.today()
    return render_template(
        "index.html",
        mode="day",
        q="",
        alert=alert,
        result=result,
        cards=_cards(result.page),
        no_older=no_older,
        today=today,
        previous_day=result.day - timedelta(days=1) if result.day > MIN_DAY else None,
        next_day=result.day + timedelta(days=1) if result.day not in (today, MAX_DAY) else None,
    )


def _render_search_page(q: str, cursor: str | None) -> str:
    # The stripped text normalises like the raw one, so Load more links built from it keep the cursor valid.
    page = _context().service.search(q, cursor=cursor)
    return render_template(
        "index.html",
        mode="search",
        q=q.strip(),
        alert=None,
        page=page,
        cards=_cards(page),
        today=_context().service.today(),
    )
