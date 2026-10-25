"""Error mapping and security headers, registered on the app by ``create_app``.

- InvalidRequest / InvalidCursor → 400; CursorExpired → 410 ("Results changed — reload");
  any other NewsStoreError → 503. Store error details are logged, never shown.
- The fragment routes (Load more, Show older) answer with a small alert fragment that news.js inserts;
  every other route answers with the full error page.
"""

import logging

from flask import Flask, Response, render_template, request, url_for
from flask.typing import ResponseReturnValue
from werkzeug.exceptions import HTTPException

from cyber_news.service import InvalidRequest
from cyber_news.store.base import CursorExpired, InvalidCursor, NewsStoreError

logger = logging.getLogger(__name__)

FRAGMENT_ENDPOINTS = frozenset({"news.items", "news.older"})

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

STORE_UNAVAILABLE = "News store is unavailable, try again shortly."
RESULTS_CHANGED = "Results changed — reload"
BAD_REQUEST = "This request is not valid. Check the address, or start again from today's news."


def register_error_handlers(app: Flask) -> None:
    app.register_error_handler(InvalidRequest, _bad_request)
    app.register_error_handler(InvalidCursor, _bad_request)
    app.register_error_handler(CursorExpired, _cursor_expired)
    app.register_error_handler(NewsStoreError, _store_unavailable)
    app.register_error_handler(HTTPException, _http_error)


def add_security_headers(response: Response) -> Response:
    """``after_request`` hook: the same headers on every response, static files included."""
    response.headers.update(SECURITY_HEADERS)
    return response


def _bad_request(exc: Exception) -> ResponseReturnValue:
    logger.info("Bad request %s: %s", request.full_path, exc)
    return _error(400, "Bad request", BAD_REQUEST)


def _cursor_expired(exc: CursorExpired) -> ResponseReturnValue:
    logger.info("Expired cursor on %s: %s", request.path, exc)
    return _error(410, "Results changed", RESULTS_CHANGED, reload_url=_reload_url())


def _store_unavailable(exc: NewsStoreError) -> ResponseReturnValue:
    logger.error("News store error on %s", request.path, exc_info=exc)
    return _error(503, "Service unavailable", STORE_UNAVAILABLE)


def _http_error(exc: HTTPException) -> ResponseReturnValue:
    code = exc.code or 500
    message = BAD_REQUEST if code == 400 else (exc.description or exc.name)
    return _error(code, exc.name, message)


def _error(status: int, title: str, message: str, reload_url: str | None = None) -> ResponseReturnValue:
    if request.endpoint in FRAGMENT_ENDPOINTS:
        return render_template("_message.html", message=message, kind="error", reload_url=reload_url), status
    return render_template("error.html", status=status, title=title, message=message, reload_url=reload_url), status


def _reload_url() -> str:
    """The page the user started from (the search or the selected day), without any cursor."""
    kept = {name: request.args[name] for name in ("q", "date") if request.args.get(name)}
    return url_for("news.index", **kept)
