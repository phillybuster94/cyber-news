"""Cyber news: a read-only Flask web app over cyber security news indexed from RSS feeds.

``create_app`` is the composition root. Flask, the web layer and the store factory are imported inside it, so
``import cyber_news`` stays light and never loads the Elasticsearch client.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask

    from cyber_news.config import Settings
    from cyber_news.store.base import NewsRepository

__version__ = "0.1.0"


def create_app(
    settings: Settings | None = None,
    repository: NewsRepository | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Flask:
    """Build the Flask app.

    - ``settings``: defaults to ``Settings.from_env(os.environ)``, which raises ConfigError when invalid.
    - ``repository``: defaults to ``build_repository(settings)``; the store is not contacted until a request.
    - ``clock``: returns the current tz-aware instant; defaults to the system clock in UTC.
    """
    from flask import Flask

    from cyber_news.config import Settings
    from cyber_news.service import NewsService
    from cyber_news.store.factory import build_repository
    from cyber_news.web.errors import add_security_headers, register_error_handlers
    from cyber_news.web.routes import EXTENSION_KEY, WebContext, bp

    settings = settings if settings is not None else Settings.from_env(os.environ)
    repository = repository if repository is not None else build_repository(settings)
    clock = clock if clock is not None else _utc_now

    app = Flask(__name__, static_folder=None)
    app.extensions[EXTENSION_KEY] = WebContext(
        service=NewsService(repository, settings.timezone, clock),
        repository=repository,
        tz=settings.timezone,
        clock=clock,
    )
    app.register_blueprint(bp)
    register_error_handlers(app)
    app.after_request(add_security_headers)
    return app


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
