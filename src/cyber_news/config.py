"""Typed application settings, loaded and validated from environment variables."""

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Europe/Bucharest"

_TRUE_VALUES = frozenset({"true", "1", "yes"})
_FALSE_VALUES = frozenset({"false", "0", "no"})


class ConfigError(ValueError):
    """Raised when the environment is invalid; ``errors`` holds one message per problem."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("Invalid configuration:\n" + "\n".join(f"- {error}" for error in self.errors))


@dataclass(frozen=True)
class FieldMapping:
    """Names of the four news fields in the index; dotted paths address nested fields."""

    title: str = "title"
    description: str = "description"
    link: str = "link"
    published: str = "published"


@dataclass(frozen=True)
class ElasticsearchSettings:
    """How to reach Elasticsearch, and the index, alias or pattern that holds the news."""

    hosts: tuple[str, ...] = ("http://localhost:9200",)
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    api_key: str | None = field(default=None, repr=False)
    verify_certs: bool = True
    ca_certs: str | None = None
    request_timeout: float = 10.0
    index: str = "cyber-news"


@dataclass(frozen=True)
class Settings:
    elasticsearch: ElasticsearchSettings = field(default_factory=ElasticsearchSettings)
    fields: FieldMapping = field(default_factory=FieldMapping)
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo(DEFAULT_TIMEZONE))

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> "Settings":
        """Build settings from ``environ``; unset variables take the defaults declared above.

        Raises ConfigError listing every invalid variable, not only the first one found.
        """
        env = _EnvReader(environ)
        settings = cls(
            elasticsearch=_read_elasticsearch(env),
            fields=_read_fields(env),
            timezone=_read_timezone(env),
        )
        if env.errors:
            raise ConfigError(env.errors)
        if not settings.elasticsearch.verify_certs:
            logger.warning("ES_VERIFY_CERTS is false: Elasticsearch TLS certificates will not be verified")
        return settings


_ES_DEFAULTS = ElasticsearchSettings()
_FIELD_DEFAULTS = FieldMapping()


class _EnvReader:
    """Reads variables from one mapping and collects every problem instead of stopping at the first.

    On a problem each reader records an error and returns the default, so callers can keep going.
    Variables with a default reject a blank value; optional variables treat blank as unset.
    """

    def __init__(self, environ: Mapping[str, str]) -> None:
        self._environ = environ
        self.errors: list[str] = []

    def error(self, name: str, problem: str) -> None:
        self.errors.append(f"{name}: {problem}")

    def optional(self, name: str) -> str | None:
        """The stripped value, or None when the variable is unset or blank."""
        return self._environ.get(name, "").strip() or None

    def get(self, name: str) -> str | None:
        """The stripped value, or None when unset; a blank value is recorded as an error."""
        if name not in self._environ:
            return None
        value = self._environ[name].strip()
        if not value:
            self.error(name, "must not be empty")
            return None
        return value

    def text(self, name: str, default: str) -> str:
        return self.get(name) or default

    def boolean(self, name: str, default: bool) -> bool:
        raw = self.get(name)
        if raw is None:
            return default
        if raw.lower() in _TRUE_VALUES:
            return True
        if raw.lower() in _FALSE_VALUES:
            return False
        self.error(name, f"expected one of true/false/1/0/yes/no, got {raw!r}")
        return default

    def positive_float(self, name: str, default: float) -> float:
        raw = self.get(name)
        if raw is None:
            return default
        try:
            value = float(raw)
        except ValueError:
            value = math.nan
        if not (math.isfinite(value) and value > 0):
            self.error(name, f"expected a number greater than 0, got {raw!r}")
            return default
        return value

    def secret(self, name: str) -> str | None:
        """A secret given directly in ``name``, or read from the file named by ``name``_FILE (a Docker secret)."""
        file_name = f"{name}_FILE"
        value, path = self.optional(name), self.optional(file_name)
        if path is None:
            return value
        if value is not None:
            self.error(name, f"set {name} or {file_name}, not both")
            return None
        try:
            content = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            self.error(file_name, f"cannot read {path!r}: {exc.strerror}")
            return None
        except UnicodeDecodeError:
            self.error(file_name, f"{path!r} is not UTF-8 text")
            return None
        if not content:
            self.error(file_name, f"{path!r} is empty")
            return None
        return content


def _read_elasticsearch(env: _EnvReader) -> ElasticsearchSettings:
    username, password, api_key = _read_auth(env)
    hosts, ca_certs = _read_hosts(env), _read_ca_certs(env)
    _check_tls_options(env, hosts, ca_certs)
    return ElasticsearchSettings(
        hosts=hosts,
        username=username,
        password=password,
        api_key=api_key,
        verify_certs=env.boolean("ES_VERIFY_CERTS", _ES_DEFAULTS.verify_certs),
        ca_certs=ca_certs,
        request_timeout=env.positive_float("ES_REQUEST_TIMEOUT", _ES_DEFAULTS.request_timeout),
        index=env.text("ES_INDEX", _ES_DEFAULTS.index),
    )


def _read_hosts(env: _EnvReader) -> tuple[str, ...]:
    raw = env.get("ES_HOSTS")
    if raw is None:
        return _ES_DEFAULTS.hosts
    hosts = tuple(host.strip() for host in raw.split(",") if host.strip())
    if not hosts:
        env.error("ES_HOSTS", "expected a comma-separated list of http(s)://host[:port] URLs")
        return _ES_DEFAULTS.hosts
    for host in hosts:
        problem = _host_problem(host)
        if problem:
            env.error("ES_HOSTS", problem)
    return hosts


def _host_problem(host: str) -> str | None:
    # Checked first so that later messages can echo the host without leaking credentials.
    if "@" in host:
        return "a host URL must not contain credentials; use ES_USERNAME/ES_PASSWORD or ES_API_KEY"
    try:
        parts = urlsplit(host)
        parts.port  # raises ValueError when the port is not a number in 0-65535
    except ValueError:
        return f"{host!r} is not a valid URL"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return f"expected http(s)://host[:port], got {host!r}"
    return None


def _read_auth(env: _EnvReader) -> tuple[str | None, str | None, str | None]:
    username = env.optional("ES_USERNAME")
    password = env.secret("ES_PASSWORD")
    api_key = env.secret("ES_API_KEY")
    # Based on what was configured, so an unreadable secret file does not also report a missing value.
    has_username = username is not None
    has_password = env.optional("ES_PASSWORD") is not None or env.optional("ES_PASSWORD_FILE") is not None
    has_api_key = env.optional("ES_API_KEY") is not None or env.optional("ES_API_KEY_FILE") is not None
    if has_username != has_password:
        env.error("ES_USERNAME/ES_PASSWORD", "set both or neither")
    if has_api_key and (has_username or has_password):
        env.error("ES_API_KEY", "cannot be combined with ES_USERNAME/ES_PASSWORD")
    return username, password, api_key


def _read_ca_certs(env: _EnvReader) -> str | None:
    path = env.optional("ES_CA_CERTS")
    if path is not None and not Path(path).is_file():
        env.error("ES_CA_CERTS", f"{path!r} is not a file")
    return path


def _check_tls_options(env: _EnvReader, hosts: tuple[str, ...], ca_certs: str | None) -> None:
    # The client refuses TLS options for http:// hosts. The hosts are not echoed: they may hold credentials.
    if ca_certs is not None and any(host.lower().startswith("http://") for host in hosts):
        env.error(
            "ES_CA_CERTS/ES_HOSTS", "ES_CA_CERTS needs https:// hosts; use https:// in ES_HOSTS or unset ES_CA_CERTS"
        )


def _read_fields(env: _EnvReader) -> FieldMapping:
    return FieldMapping(
        title=_read_field_name(env, "NEWS_FIELD_TITLE", _FIELD_DEFAULTS.title),
        description=_read_field_name(env, "NEWS_FIELD_DESCRIPTION", _FIELD_DEFAULTS.description),
        link=_read_field_name(env, "NEWS_FIELD_LINK", _FIELD_DEFAULTS.link),
        published=_read_field_name(env, "NEWS_FIELD_PUBLISHED", _FIELD_DEFAULTS.published),
    )


def _read_field_name(env: _EnvReader, name: str, default: str) -> str:
    value = env.text(name, default)
    if not all(value.split(".")):
        env.error(name, f"expected a field name or dotted path such as meta.published_at, got {value!r}")
        return default
    return value


def _read_timezone(env: _EnvReader) -> ZoneInfo:
    name = env.text("APP_TIMEZONE", DEFAULT_TIMEZONE)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        env.error("APP_TIMEZONE", f"unknown IANA time zone {name!r}")
        return ZoneInfo(DEFAULT_TIMEZONE)
