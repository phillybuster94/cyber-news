"""NewsRepository on Elasticsearch: PIT + ``search_after`` paging over the configured field names.

The query builders are pure functions that return request bodies, independent of any cluster.
``ElasticsearchNewsRepository`` runs them and translates every client error into the port's errors.
"""

import base64
import binascii
import json
import logging
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from elasticsearch import ApiError, BadRequestError, Elasticsearch, NotFoundError, TransportError

from cyber_news.config import ElasticsearchSettings, FieldMapping, Settings
from cyber_news.domain import NewsItem, NewsPage
from cyber_news.store.base import SUBSTRING_MIN_LENGTH, CursorExpired, InvalidCursor, NewsStoreError

logger = logging.getLogger(__name__)

PIT_KEEP_ALIVE = "2m"
SEARCH_TIMEOUT = "10s"
ISO_FORMAT = "strict_date_optional_time"
SEARCH_FLAGS = "AND|OR|NOT|PHRASE|PREFIX|FUZZY|PRECEDENCE|WHITESPACE|ESCAPE"
TITLE_BOOST = 3

_MISSING_SEARCH_CONTEXT = "search_context_missing_exception"
_PLAIN_QUERY = re.compile(r"[\w\s]+")
_WORD = re.compile(r"\w+")


def build_client(es: ElasticsearchSettings) -> Elasticsearch:
    """A client for ``es``; it opens no connection until the first request, so each process gets its own."""
    basic_auth = (es.username, es.password) if es.username is not None and es.password is not None else None
    return Elasticsearch(
        list(es.hosts),
        basic_auth=basic_auth,
        api_key=es.api_key,
        verify_certs=es.verify_certs,
        ca_certs=es.ca_certs,
        request_timeout=es.request_timeout,
        retry_on_timeout=False,
    )


def day_query(fields: FieldMapping, start: datetime, end: datetime) -> dict[str, Any]:
    """``query`` and ``sort`` for items published in ``[start, end)``, newest first."""
    query = {
        "bool": {
            "filter": [
                {"range": {fields.published: {"gte": _iso_utc(start), "lt": _iso_utc(end), "format": ISO_FORMAT}}}
            ]
        }
    }
    return {"query": query, "sort": [_newest_first(fields)]}


def search_query(fields: FieldMapping, text: str) -> dict[str, Any]:
    """``query`` and ``sort`` for simple query string ``text`` over title and description, best match first.

    Every term must match (in either field); REGEXP, NEAR and SLOP are not among the allowed flags. When
    ``text`` is only plain words, each term of at least ``SUBSTRING_MIN_LENGTH`` characters may instead match
    inside a word: a case-insensitive ``*term*`` wildcard per field, a constant score of the field's boost added
    to the whole-word score. Any other ``text`` is one simple query string, operators and all.
    """
    terms = _WORD.findall(text) if _PLAIN_QUERY.fullmatch(text) else []
    if any(len(term) >= SUBSTRING_MIN_LENGTH for term in terms):
        query: dict[str, Any] = {"bool": {"must": [_term_query(fields, term) for term in terms]}}
    else:
        query = _simple_query(fields, text)
    return {"query": query, "sort": ["_score", _newest_first(fields)]}


def _simple_query(fields: FieldMapping, text: str) -> dict[str, Any]:
    return {
        "simple_query_string": {
            "query": text,
            "fields": [f"{fields.title}^{TITLE_BOOST}", fields.description],
            "default_operator": "and",
            "lenient": True,
            "flags": SEARCH_FLAGS,
        }
    }


def _term_query(fields: FieldMapping, term: str) -> dict[str, Any]:
    """One plain term: the whole word, or else (when long enough) the term inside a word of either field."""
    if len(term) < SUBSTRING_MIN_LENGTH:
        return _simple_query(fields, term)
    return {
        "bool": {
            "should": [
                _simple_query(fields, term),
                _substring_query(fields.title, term, TITLE_BOOST),
                _substring_query(fields.description, term, 1),
            ],
            "minimum_should_match": 1,
        }
    }


def _substring_query(field: str, term: str, boost: float) -> dict[str, Any]:
    # A \w+ term holds none of the wildcard syntax characters (* ? \), so it needs no escaping.
    return {"wildcard": {field: {"value": f"*{term.lower()}*", "case_insensitive": True, "boost": boost}}}


def latest_before_query(fields: FieldMapping, instant: datetime) -> dict[str, Any]:
    """A plain search body (no PIT) for the single newest item published strictly before ``instant``."""
    return {
        "size": 1,
        "query": {"bool": {"filter": [{"range": {fields.published: {"lt": _iso_utc(instant), "format": ISO_FORMAT}}}]}},
        "sort": [{fields.published: {"order": "desc"}}],
        "fields": [_published_field(fields)],
        "_source": False,
        "track_total_hits": False,
        "timeout": SEARCH_TIMEOUT,
    }


def _page_request(
    fields: FieldMapping,
    query: dict[str, Any],
    sort: list[Any],
    pit_id: str,
    search_after: Sequence[Any] | None,
    limit: int,
) -> dict[str, Any]:
    """The search body for one page inside the PIT; it names no index, because the PIT fixes it."""
    request: dict[str, Any] = {
        "size": limit,
        "query": query,
        "sort": sort,
        "pit": {"id": pit_id, "keep_alive": PIT_KEEP_ALIVE},
        "fields": [fields.title, fields.description, fields.link, _published_field(fields)],
        "_source": False,
        "track_total_hits": True,
        "timeout": SEARCH_TIMEOUT,
    }
    if search_after is not None:
        request["search_after"] = list(search_after)
    return request


def encode_cursor(pit_id: str, sort_values: Sequence[Any], served: int) -> str:
    """Url-safe base64 (unpadded) of ``{"p": pit_id, "s": sort_values, "n": served}``."""
    payload = json.dumps({"p": pit_id, "s": list(sort_values), "n": served}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, list[Any], int]:
    """``(pit_id, sort_values, served)`` from ``encode_cursor``; InvalidCursor for anything else."""
    try:
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        payload = json.loads(raw)
    except (TypeError, ValueError, binascii.Error) as exc:  # UnicodeError and JSONDecodeError are ValueErrors
        raise InvalidCursor("malformed cursor") from exc
    if not isinstance(payload, dict):
        raise InvalidCursor("malformed cursor")
    pit_id, sort_values, served = payload.get("p"), payload.get("s"), payload.get("n")
    if not (isinstance(pit_id, str) and pit_id and isinstance(sort_values, list) and sort_values):
        raise InvalidCursor("malformed cursor")
    if not (type(served) is int and served > 0):
        raise InvalidCursor("malformed cursor")
    return pit_id, sort_values, served


def hit_to_item(hit: dict[str, Any], fields: FieldMapping) -> NewsItem:
    """The item in a hit's ``fields`` response; a missing or invalid value becomes ``""`` or None."""
    values = hit.get("fields") or {}
    title, description, link = (_field_value(values, name) for name in (fields.title, fields.description, fields.link))
    return NewsItem(
        title=title if isinstance(title, str) else "",
        description=description if isinstance(description, str) else "",
        link=link if isinstance(link, str) and link else None,
        published=_parse_instant(_field_value(values, fields.published)),
    )


class ElasticsearchNewsRepository:
    """NewsRepository over ``index`` (an index, alias or pattern) with the configured field names.

    A first page opens a point in time (PIT); its cursor carries the PIT id, the last hit's sort values
    (which end with the tiebreaker that Elasticsearch adds to PIT searches) and the number of items served.
    The PIT is closed once the last page has been served; an abandoned one expires after ``PIT_KEEP_ALIVE``.
    """

    def __init__(self, client: Elasticsearch, index: str, fields: FieldMapping) -> None:
        self._client = client
        self._index = index
        self._fields = fields

    @classmethod
    def from_settings(cls, settings: Settings) -> "ElasticsearchNewsRepository":
        return cls(build_client(settings.elasticsearch), settings.elasticsearch.index, settings.fields)

    def published_between(self, start: datetime, end: datetime, *, cursor: str | None, limit: int) -> NewsPage:
        return self._page(day_query(self._fields, start, end), cursor, limit)

    def search(self, text: str, *, cursor: str | None, limit: int) -> NewsPage:
        return self._page(search_query(self._fields, text), cursor, limit)

    def latest_published_before(self, instant: datetime) -> datetime | None:
        with _translate_errors():
            response = self._client.search(index=self._index, **latest_before_query(self._fields, instant))
        hits = response["hits"]["hits"]
        return _parse_instant(_field_value(hits[0].get("fields") or {}, self._fields.published)) if hits else None

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:  # the port promises that ping never raises
            logger.warning("Elasticsearch ping failed", exc_info=True)
            return False

    def _page(self, query_and_sort: dict[str, Any], cursor: str | None, limit: int) -> NewsPage:
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        if cursor is None:
            pit_id, search_after, served = self._open_pit(), None, 0
        else:
            pit_id, search_after, served = decode_cursor(cursor)
        request = _page_request(self._fields, **query_and_sort, pit_id=pit_id, search_after=search_after, limit=limit)
        try:
            with _translate_errors(cursor_supplied=cursor is not None):
                response = self._client.search(**request)
        except NewsStoreError:
            if cursor is None:
                self._close_pit(pit_id)
            raise
        hits = response["hits"]["hits"]
        total = response["hits"]["total"]["value"]
        pit_id = response.get("pit_id") or pit_id  # Elasticsearch may hand back an updated id
        served += len(hits)
        if hits and served < total:
            next_cursor = encode_cursor(pit_id, hits[-1]["sort"], served)
        else:
            next_cursor = None
            self._close_pit(pit_id)
        items = tuple(hit_to_item(hit, self._fields) for hit in hits)
        return NewsPage(items=items, total=total, next_cursor=next_cursor)

    def _open_pit(self) -> str:
        with _translate_errors():
            return self._client.open_point_in_time(index=self._index, keep_alive=PIT_KEEP_ALIVE)["id"]

    def _close_pit(self, pit_id: str) -> None:
        """Best effort: a PIT that cannot be closed expires on its own."""
        try:
            self._client.close_point_in_time(id=pit_id)
        except (ApiError, TransportError) as exc:
            logger.debug("Could not close an Elasticsearch point in time: %s", type(exc).__name__)


@contextmanager
def _translate_errors(*, cursor_supplied: bool = False) -> Iterator[None]:
    """Client errors → the port's errors, chained with ``from``.

    A missing search context is CursorExpired. A 400 on a request that carries a cursor is InvalidCursor: the
    first page ran the same query without one, so the cursor is foreign or tampered (e.g. a forged PIT id, or
    a day cursor used on a search). Everything else is NewsStoreError.
    """
    try:
        yield
    except NotFoundError as exc:
        if _MISSING_SEARCH_CONTEXT in _error_types(exc):
            raise CursorExpired("the search snapshot has expired; reload the results") from exc
        raise NewsStoreError(f"Elasticsearch request failed: {_describe(exc)}") from exc
    except BadRequestError as exc:
        if cursor_supplied:
            logger.warning("Elasticsearch rejected a cursor: %s", _describe(exc))
            raise InvalidCursor("cursor does not belong to this query or snapshot") from exc
        raise NewsStoreError(f"Elasticsearch request failed: {_describe(exc)}") from exc
    except (ApiError, TransportError) as exc:
        raise NewsStoreError(f"Elasticsearch request failed: {_describe(exc)}") from exc


def _error_types(exc: ApiError) -> set[str]:
    """The error type and its root-cause types; an expired PIT is a root cause of a search phase failure."""
    error = exc.body.get("error") if isinstance(exc.body, dict) else None
    if not isinstance(error, dict):
        return {exc.error}
    causes = error.get("root_cause") if isinstance(error.get("root_cause"), list) else []
    return {exc.error, *(cause.get("type") for cause in causes if isinstance(cause, dict))}


def _describe(exc: ApiError | TransportError) -> str:
    """Status and error type, or the transport error's class; never headers or request bodies."""
    if isinstance(exc, ApiError):
        return f"{exc.status_code} {exc.error}"
    return type(exc).__name__


def _newest_first(fields: FieldMapping) -> dict[str, Any]:
    # unmapped_type keeps an index pattern that includes an index without the field from failing.
    return {fields.published: {"order": "desc", "missing": "_last", "unmapped_type": "date"}}


def _published_field(fields: FieldMapping) -> dict[str, str]:
    """The published field in ISO 8601, whatever date format the mapping declares."""
    return {"field": fields.published, "format": ISO_FORMAT}


def _iso_utc(instant: datetime) -> str:
    if instant.utcoffset() is None:
        raise ValueError(f"instant must be tz-aware, got {instant!r}")
    return instant.astimezone(timezone.utc).isoformat()


def _field_value(values: dict[str, Any], name: str) -> Any:
    """The first value of ``name`` in a ``fields`` response, flat (``{"a.b": [x]}``) or nested
    (``{"a": [{"b": [x]}]}``, as returned for ``nested`` fields); None when it is absent."""
    if name in values:
        return _first(values[name])
    node: Any = values
    for part in name.split("."):
        node = _first(node) if isinstance(node, list) else node
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return _first(node)


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _parse_instant(value: Any) -> datetime | None:
    """A UTC datetime from an ISO 8601 string (naive is read as UTC), or None when it is not one."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):  # OverflowError: e.g. year 1 with a positive offset
        return None
