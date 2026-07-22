#!/usr/bin/env python3
"""
cfbd_client.py — Hardened CollegeFootballData (CFBD) REST client (ARCHITECTURE §4).

Single vendor, v2 REST/JSON, Bearer auth. Fetch-hardening per §4:
  - retry/backoff for transient network errors and Cloudflare burst-blocking
    (v2 has no per-request throttle, but ~10-min Cloudflare blocks on bursts),
  - a distinct wait on 429 rate limits,
  - re-raise on a persistent outage so callers can fall back to cache (§4
    commentary-bypass) rather than silently scoring stale/partial data,
  - counts every HTTP call for the monthly-budget report (§4, 1,000/month).

No third-party HTTP dependency — stdlib urllib only (raw-urllib style §8).
"""

import json
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = "https://api.collegefootballdata.com"

# Transient HTTP statuses worth retrying (429 handled separately; 403/503 are
# how Cloudflare burst-blocking usually surfaces).
_RETRYABLE_STATUS = {403, 500, 502, 503, 504}
_TRANSIENT_NET = (URLError, ssl.SSLError, socket.timeout, ConnectionError)


class CFBDError(Exception):
    """A persistent failure after exhausting retries — fail loud."""


class CFBDClient:
    def __init__(self, api_key, timeout=30):
        self.api_key = api_key
        self.timeout = timeout
        self.call_count = 0  # BUILD 3: exact calls this process made

    def get(self, endpoint, params=None,
            net_retries=3, net_backoff=(5, 10, 20),
            burst_retries=3, burst_backoff=(15, 30, 60),
            rate_wait=60):
        """GET <endpoint> with params. Retries transient network + Cloudflare
        bursts + 429s; raises CFBDError on persistent failure."""
        url = f"{API_BASE}{endpoint}"
        if params:
            url = f"{url}?{urlencode(params)}"
        req = Request(url)
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Accept", "application/json")

        net_try = burst_try = rate_try = 0
        while True:
            self.call_count += 1
            try:
                with urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except HTTPError as e:
                if e.code == 429:
                    if rate_try >= 5:
                        raise CFBDError(f"429 rate limit persists: {url}")
                    rate_try += 1
                    print(f"  [429] rate limited — waiting {rate_wait}s (try {rate_try})")
                    time.sleep(rate_wait)
                    continue
                if e.code in _RETRYABLE_STATUS:
                    if burst_try >= burst_retries:
                        raise CFBDError(f"HTTP {e.code} persists (Cloudflare burst?): {url}")
                    wait = burst_backoff[min(burst_try, len(burst_backoff) - 1)]
                    burst_try += 1
                    print(f"  [{e.code}] retryable — backoff {wait}s (try {burst_try})")
                    time.sleep(wait)
                    continue
                # 401/404/etc. are not transient — fail loud immediately.
                raise CFBDError(f"HTTP {e.code} {e.reason}: {url}") from e
            except _TRANSIENT_NET as e:
                if net_try >= net_retries:
                    raise CFBDError(f"network failure persists: {url} ({e})") from e
                wait = net_backoff[min(net_try, len(net_backoff) - 1)]
                net_try += 1
                print(f"  [net] {type(e).__name__} — backoff {wait}s (try {net_try})")
                time.sleep(wait)
                continue
