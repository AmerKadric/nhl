"""HTTP client with per-host rate limiting, retries and conditional downloads."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .. import config

log = logging.getLogger("puckiq.http")

RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    pass


class HttpClient:
    """Polite client: one request per host every `min_interval` seconds,
    exponential backoff on transient failures, honours Retry-After."""

    def __init__(self, min_interval: float = 0.25, max_retries: int = 5, timeout: float = 60.0):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT, "Accept": "*/*"})
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self._last: dict[str, float] = {}

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        wait = self._last.get(host, 0.0) + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def request(self, url: str, *, params: dict | None = None, headers: dict | None = None,
                stream: bool = False) -> requests.Response:
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            self._throttle(url)
            try:
                r = self.session.get(url, params=params, headers=headers, timeout=self.timeout,
                                     stream=stream, allow_redirects=True)
                if r.status_code in RETRY_STATUS:
                    retry_after = r.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 1.5 * 2 ** attempt
                    log.warning("HTTP %s from %s; retrying in %.1fs", r.status_code, url, delay)
                    time.sleep(min(delay, 60))
                    last_err = FetchError(f"HTTP {r.status_code} for {url}")
                    continue
                if r.status_code >= 400:
                    raise FetchError(f"HTTP {r.status_code} for {url}")
                return r
            except (requests.ConnectionError, requests.Timeout) as e:
                last_err = e
                delay = 1.5 * 2 ** attempt
                log.warning("%s for %s; retrying in %.1fs", type(e).__name__, url, delay)
                time.sleep(min(delay, 60))
        raise FetchError(f"Giving up on {url}: {last_err}")

    def get_json(self, url: str, params: dict | None = None) -> Any:
        r = self.request(url, params=params)
        try:
            return r.json()
        except ValueError as e:
            raise FetchError(f"Invalid JSON from {url}: {e}") from e

    def download(self, url: str, dest: Path) -> tuple[Path, bool]:
        """Download `url` to `dest` only if it changed (ETag / Last-Modified).

        Returns (path, changed)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        meta_path = dest.with_suffix(dest.suffix + ".meta.json")
        headers = {}
        if dest.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if meta.get("etag"):
                headers["If-None-Match"] = meta["etag"]
            if meta.get("last_modified"):
                headers["If-Modified-Since"] = meta["last_modified"]
        r = self.request(url, headers=headers, stream=True)
        if r.status_code == 304:
            r.close()
            return dest, False
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        tmp.replace(dest)
        meta_path.write_text(json.dumps({
            "url": url,
            "etag": r.headers.get("ETag"),
            "last_modified": r.headers.get("Last-Modified"),
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }))
        return dest, True


_client: HttpClient | None = None


def client() -> HttpClient:
    global _client
    if _client is None:
        _client = HttpClient()
    return _client
