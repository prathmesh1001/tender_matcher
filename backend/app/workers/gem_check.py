"""One GeM check in its own process (spawned by a run's GeM branch).

Reading listings is CPU-heavy Python (HTML parsing, chunking, tagging). In the run's process it
competes with the catalogue matching for the interpreter lock: measured on 8181356, rules
176 s and the judge 398 s while new listings were stored, against 30 s and 107 s without. In a
child process it runs beside the run on another core. The child needs no catalogues and no
models: only the GeM fetcher, the file store and the database. It records everything on its
job, which the run reads.
"""

import uuid
from functools import cache

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import create_db_engine, create_session_factory
from app.discovery.gem_config import load_gem_config
from app.services.discovery_service import DiscoveryMatcher, DiscoveryService, build_discovery_tools
from app.services.match_service import UncoveredType
from app.workers.tasks import ThreadJobQueue


def _no_matcher() -> DiscoveryMatcher:
    """A GeM check never matches: the run does that in its own process."""
    raise RuntimeError("a GeM check process has no matcher")


def run_gem_check(job_id: str, product_type: str, category: str, titles: list[str]) -> None:
    """Process entry point: check one product type on GeM and record it on its job.

    Args:
        job_id: The discovery job (created by the run).
        product_type: Ontology product type.
        category: Its ontology category.
        titles: The run's item titles of the type (the GeM search words come from them).
    """
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL, json_output=settings.LOG_JSON)
    service = DiscoveryService(
        create_session_factory(create_db_engine(settings)),
        settings,
        ThreadJobQueue(),
        _no_matcher,
        cache(lambda: build_discovery_tools(settings)),
        cache(lambda: load_gem_config(settings.GEM_MARKETPLACE_FILE)),
    )
    service.check_in_run(
        uuid.UUID(job_id), UncoveredType(product_type, category, [], titles=list(titles))
    )
