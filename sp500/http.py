"""Resilient HTTP: identifying User-Agent, timeouts, retries with backoff, rate limiting, robots.txt."""
from __future__ import annotations

import logging
import threading
import time
import urllib.robotparser
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "sp500-tracker/0.1 (+https://github.com/alfiochoa/sp500-tracker; contact: alf8a94@gmail.com)"
log = logging.getLogger("sp500")


def setup_logging(level: int = logging.INFO) -> None:
    """Structured (key=value) logging to stderr."""
    logging.basicConfig(level=level, format="ts=%(asctime)s level=%(levelname)s logger=%(name)s %(message)s")


def make_session(retries: int = 4, backoff: float = 1.0) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    retry = Retry(total=retries, backoff_factor=backoff, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET",), respect_retry_after_header=True)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s


class RateLimiter:
    """Blocks so that at most `per_minute` calls happen per rolling minute (thread-safe)."""

    def __init__(self, per_minute: int, clock=time.monotonic, sleep=time.sleep):
        self.interval, self._clock, self._sleep = 60.0 / per_minute, clock, sleep
        self._next, self._lock = 0.0, threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if delay:
            self._sleep(delay)


def allowed_by_robots(session: requests.Session, url: str, timeout: float = 10) -> bool:
    """True if robots.txt permits our User-Agent to fetch `url` (fails closed on a 4xx-free parse error)."""
    parts = urlsplit(url)
    rp = urllib.robotparser.RobotFileParser()
    try:
        r = session.get(f"{parts.scheme}://{parts.netloc}/robots.txt", timeout=timeout)
        rp.parse(r.text.splitlines())
    except requests.RequestException as e:
        log.warning("event=robots_unreachable url=%s err=%s", url, e)
        return False
    return rp.can_fetch(USER_AGENT, url)
