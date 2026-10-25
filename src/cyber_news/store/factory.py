"""The one seam that picks the news store implementation."""

from cyber_news.config import Settings
from cyber_news.store.base import NewsRepository


def build_repository(settings: Settings) -> NewsRepository:
    """Build the configured news store; today that is always Elasticsearch.

    The adapter is imported here, not at module level, so that importing the factory does not load
    the Elasticsearch client.
    """
    from cyber_news.store.elasticsearch import ElasticsearchNewsRepository

    return ElasticsearchNewsRepository.from_settings(settings)
