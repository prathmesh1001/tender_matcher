import httpx
import pytest

from app.core.exceptions import LinkFetchError
from app.discovery.polite_fetcher import PoliteFetcher, robots_policy

AGENT = "tender-matcher-test"
PAGE = "https://mkp.gem.gov.in/valves-control-valve-pneumatic/search"


def _fetcher(
    robots: httpx.Response | Exception, seen: list[str], waits: list[float]
) -> PoliteFetcher:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            if isinstance(robots, Exception):
                raise robots
            return robots
        return httpx.Response(200, content=b"<html>page</html>")

    client = httpx.Client(transport=httpx.MockTransport(handle))
    return PoliteFetcher(client, AGENT, delay_s=3.0, max_bytes=10_000, sleep=waits.append)


def test_robots_disallow_is_respected_and_the_page_is_never_requested() -> None:
    seen: list[str] = []
    fetcher = _fetcher(httpx.Response(200, text="User-agent: *\nDisallow: /"), seen, [])
    with pytest.raises(LinkFetchError, match=r"robots\.txt disallows"):
        fetcher.get(PAGE)
    assert seen == ["https://mkp.gem.gov.in/robots.txt"]


def test_allowed_page_is_fetched_after_a_pause_and_robots_read_once() -> None:
    seen: list[str] = []
    waits: list[float] = []
    fetcher = _fetcher(httpx.Response(200, text="# no rules\n"), seen, waits)
    assert fetcher.get(PAGE).content == b"<html>page</html>"
    fetcher.get(PAGE)
    assert seen == ["https://mkp.gem.gov.in/robots.txt", PAGE, PAGE]
    assert waits == [3.0, 3.0, 3.0]  # before robots.txt and before each page


def test_robots_rules_for_another_path_still_allow_this_one() -> None:
    fetcher = _fetcher(httpx.Response(200, text="User-agent: *\nDisallow: /admin"), [], [])
    assert fetcher.allowed(PAGE)
    assert not fetcher.allowed("https://mkp.gem.gov.in/admin/x")


def test_missing_robots_allows_and_unreadable_robots_disallows() -> None:
    assert robots_policy(404, "").can_fetch(AGENT, PAGE)
    assert not robots_policy(503, "").can_fetch(AGENT, PAGE)
    assert not robots_policy(None, "").can_fetch(AGENT, PAGE)


def test_network_error_on_robots_means_nothing_is_fetched() -> None:
    seen: list[str] = []
    fetcher = _fetcher(httpx.ConnectError("down"), seen, [])
    with pytest.raises(LinkFetchError):
        fetcher.get(PAGE)
    assert seen == ["https://mkp.gem.gov.in/robots.txt"]
