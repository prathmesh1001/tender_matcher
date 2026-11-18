"""Read public web pages politely: robots.txt first, a pause before every request, no login.

robots.txt is read once per host. As robots.txt readers usually do: a missing file (4xx)
allows everything; a file that cannot be read (5xx, network error) allows nothing, so an
outage never turns into crawling against the site's wishes.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, Protocol
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.core.exceptions import LinkFetchError
from app.schemas.tender_files import FetchedFile
from app.tender.link_fetcher import fetch_url

logger = logging.getLogger(__name__)

_CLIENT_ERROR: Final = range(400, 500)


class PageFetcher(Protocol):
    """Fetch one URL, or say why not."""

    def get(self, url: str) -> FetchedFile:
        """Download one page.

        Raises:
            LinkFetchError: If robots.txt disallows it or the download fails.
        """
        ...


def robots_url(url: str) -> str:
    """The robots.txt of a URL's host."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))


def robots_policy(status_code: int | None, text: str) -> RobotFileParser:
    """The robots rules from a robots.txt response.

    Args:
        status_code: HTTP status, or None if it could not be read at all.
        text: The body (ignored unless the status is 2xx).

    Returns:
        A parser: the file's rules for 2xx, allow-all for 4xx, disallow-all otherwise.
    """
    parser = RobotFileParser()
    if status_code is not None and 200 <= status_code < 300:
        parser.parse(text.splitlines())
    elif status_code in _CLIENT_ERROR:
        parser.parse([])  # no rules: everything allowed
    else:
        parser.parse(["User-agent: *", "Disallow: /"])
    return parser


@dataclass(slots=True)
class PoliteFetcher:
    """A `PageFetcher` that obeys robots.txt and waits `delay_s` before every request.

    Args:
        client: HTTP client (timeout, redirects and User-Agent header set by the caller).
        user_agent: The agent name robots.txt rules are read for.
        delay_s: Pause before each request, robots.txt included.
        max_bytes: Larger pages are refused.
        sleep: Waits (replaced in tests).
    """

    client: httpx.Client
    user_agent: str
    delay_s: float
    max_bytes: int
    sleep: Callable[[float], None] = time.sleep
    _robots: dict[str, RobotFileParser] = field(default_factory=dict)

    def _wait(self) -> None:
        if self.delay_s > 0:
            self.sleep(self.delay_s)

    def _rules(self, url: str) -> RobotFileParser:
        """robots.txt rules for the URL's host, read on first use."""
        where = robots_url(url)
        if where not in self._robots:
            self._wait()
            try:
                response = self.client.get(where)
                status_code: int | None = response.status_code
                text = response.text
            except httpx.HTTPError as exc:
                logger.warning("robots.txt unreadable", extra={"url": where, "error": str(exc)})
                status_code, text = None, ""
            self._robots[where] = robots_policy(status_code, text)
        return self._robots[where]

    def allowed(self, url: str) -> bool:
        """True if robots.txt lets this agent read the URL."""
        return self._rules(url).can_fetch(self.user_agent, url)

    def get(self, url: str) -> FetchedFile:
        """Download one page after the robots check and the pause.

        Args:
            url: The page.

        Returns:
            Its body and when it was fetched.

        Raises:
            LinkFetchError: If robots.txt disallows it or the download fails.
        """
        if not self.allowed(url):
            raise LinkFetchError(
                f"robots.txt disallows {url}", details={"url": url, "reason": "robots"}
            )
        self._wait()
        return fetch_url(self.client, url, self.max_bytes)
