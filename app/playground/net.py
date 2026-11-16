"""Compliant page fetching.

Rules this module enforces for every outbound page request:
  * http/https on ports 80/443 only, and never to private, loopback,
    link-local or otherwise reserved addresses (SSRF protection), re-checked
    on every redirect hop.
  * robots.txt is read and obeyed. If a site disallows us, we stop.
  * We identify ourselves honestly with a User-Agent. We never rotate
    identities, solve CAPTCHAs or otherwise bypass access controls.
  * A bot-challenge or access-denied response is reported as "blocked" and
    the agent falls back to what a licensed search API reported.
  * Per-host throttling, hard timeouts and response size limits.
"""
from __future__ import annotations

import ipaddress
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

from .models import now_iso

OK = "ok"
BLOCKED_ROBOTS = "blocked_robots"
BLOCKED_CHALLENGE = "blocked_challenge"
HTTP_ERROR = "http_error"
NETWORK_ERROR = "network_error"
UNSAFE_URL = "unsafe_url"
TOO_LARGE = "too_large"
NOT_HTML = "not_html"

CHALLENGE_MARKERS = (
    "captcha", "robot check", "are you a robot", "unusual traffic",
    "access denied", "verify you are human", "enable javascript and cookies",
    "request blocked", "pardon our interruption",
)


@dataclass
class FetchResult:
    status: str
    url: str
    final_url: str | None = None
    http_status: int | None = None
    text: str = ""
    content: bytes = b""
    content_type: str = ""
    detail: str = ""
    retrieved_at: str = ""

    @property
    def ok(self):
        return self.status == OK


def is_public_address(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )


def check_url_safety(url: str, resolver=socket.getaddrinfo):
    """Return (ok, reason)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "malformed URL"
    if parsed.scheme not in ("http", "https"):
        return False, "only http and https are allowed"
    if not parsed.hostname:
        return False, "missing host"
    if parsed.username or parsed.password:
        return False, "credentials in URL are not allowed"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in (80, 443):
        return False, "unusual port"
    try:
        infos = resolver(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return False, "host could not be resolved"
    addresses = {info[4][0] for info in infos}
    if not addresses:
        return False, "host could not be resolved"
    for address in addresses:
        if not is_public_address(address):
            return False, "host resolves to a non-public address"
    return True, ""


class SafeFetcher:
    def __init__(self, user_agent, timeout=12, max_bytes=1_500_000, min_host_interval=1.0,
                 session=None, resolver=socket.getaddrinfo, obey_robots=True):
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.min_host_interval = min_host_interval
        self.session = session or requests.Session()
        self.resolver = resolver
        self.obey_robots = obey_robots
        self._robots = {}
        self._last_hit = {}
        self._lock = threading.Lock()

    # -- throttling -------------------------------------------------------
    def _throttle(self, host):
        with self._lock:
            wait = self._last_hit.get(host, 0) + self.min_host_interval - time.monotonic()
            self._last_hit[host] = max(time.monotonic(), self._last_hit.get(host, 0) + self.min_host_interval)
        if wait > 0:
            time.sleep(wait)

    # -- robots.txt -------------------------------------------------------
    def _robots_allows(self, url):
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        entry = self._robots.get(origin)
        if entry is None or entry[0] < time.monotonic():
            parser = RobotFileParser()
            allow_all = False
            deny_all = False
            robots_url = origin + "/robots.txt"
            safe, _ = check_url_safety(robots_url, self.resolver)
            if not safe:
                deny_all = True
            else:
                try:
                    self._throttle(parsed.netloc)
                    response = self.session.get(
                        robots_url, headers={"User-Agent": self.user_agent},
                        timeout=self.timeout, allow_redirects=False,
                    )
                    if response.status_code == 200:
                        parser.parse(response.text.splitlines())
                    elif response.status_code in (301, 302, 303, 307, 308):
                        deny_all = True  # do not chase redirects for robots; be conservative
                    elif 400 <= response.status_code < 500:
                        allow_all = True  # no robots.txt: crawling allowed
                    else:
                        deny_all = True   # server trouble: be conservative
                except requests.RequestException:
                    deny_all = True
            entry = (time.monotonic() + 3600, parser, allow_all, deny_all)
            self._robots[origin] = entry
        _, parser, allow_all, deny_all = entry
        if deny_all:
            return False
        if allow_all:
            return True
        return parser.can_fetch(self.user_agent, url)

    # -- main entry --------------------------------------------------------
    def fetch(self, url, accept="text/html,application/xhtml+xml", want_bytes=False,
              allowed_types=("text/html", "application/xhtml+xml"), max_bytes=None):
        limit = max_bytes or self.max_bytes
        current = url
        for _hop in range(5):
            safe, reason = check_url_safety(current, self.resolver)
            if not safe:
                return FetchResult(UNSAFE_URL, url, detail=reason, retrieved_at=now_iso())
            if self.obey_robots and not self._robots_allows(current):
                return FetchResult(BLOCKED_ROBOTS, url, final_url=current,
                                   detail="robots.txt does not allow automated access",
                                   retrieved_at=now_iso())
            self._throttle(urlparse(current).netloc)
            try:
                response = self.session.get(
                    current, headers={"User-Agent": self.user_agent, "Accept": accept,
                                      "Accept-Language": "en-IN,en;q=0.8"},
                    timeout=self.timeout, allow_redirects=False, stream=True,
                )
            except requests.RequestException as error:
                return FetchResult(NETWORK_ERROR, url, final_url=current,
                                   detail=type(error).__name__, retrieved_at=now_iso())
            try:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        return FetchResult(HTTP_ERROR, url, final_url=current,
                                           http_status=response.status_code,
                                           detail="redirect without location", retrieved_at=now_iso())
                    current = urljoin(current, location)
                    continue

                ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if response.status_code in (401, 403, 429, 503):
                    return FetchResult(BLOCKED_CHALLENGE, url, final_url=current,
                                       http_status=response.status_code,
                                       detail="the site refused automated access", retrieved_at=now_iso())
                if response.status_code != 200:
                    return FetchResult(HTTP_ERROR, url, final_url=current,
                                       http_status=response.status_code,
                                       detail=f"HTTP {response.status_code}", retrieved_at=now_iso())
                if allowed_types and ctype not in allowed_types and not any(
                        ctype.startswith(t.rstrip("*")) for t in allowed_types if t.endswith("*")):
                    return FetchResult(NOT_HTML, url, final_url=current, http_status=200,
                                       content_type=ctype, detail=f"unexpected content type {ctype or 'unknown'}",
                                       retrieved_at=now_iso())

                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > limit:
                        return FetchResult(TOO_LARGE, url, final_url=current, http_status=200,
                                           content_type=ctype, detail="response too large",
                                           retrieved_at=now_iso())
                    chunks.append(chunk)
                body = b"".join(chunks)
            finally:
                response.close()

            if want_bytes:
                return FetchResult(OK, url, final_url=current, http_status=200, content=body,
                                   content_type=ctype, retrieved_at=now_iso())

            text = body.decode(response.encoding or "utf-8", errors="replace")
            head = text[:6000].lower()
            if any(marker in head for marker in CHALLENGE_MARKERS) and "application/ld+json" not in text:
                return FetchResult(BLOCKED_CHALLENGE, url, final_url=current, http_status=200,
                                   detail="the page is a bot-check or access-denied screen",
                                   retrieved_at=now_iso())
            return FetchResult(OK, url, final_url=current, http_status=200, text=text,
                               content_type=ctype, retrieved_at=now_iso())
        return FetchResult(HTTP_ERROR, url, detail="too many redirects", retrieved_at=now_iso())

    def fetch_image(self, url, max_bytes=2_000_000):
        """Fetch an image; verified only if the bytes really are an image."""
        result = self.fetch(url, accept="image/*", want_bytes=True,
                            allowed_types=("image/*",), max_bytes=max_bytes)
        return result
