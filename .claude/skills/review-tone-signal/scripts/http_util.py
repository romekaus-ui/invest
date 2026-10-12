#!/usr/bin/env python3
"""Shared helpers that outlived the archived harness.

Lifted verbatim from `archive/historical-harness/scripts/` when that design was
retired, because five live scripts imported them and the restructure left those
imports dangling. The functions are unchanged; only their home is new.

Standard library only.
"""

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

_last_call = [0.0]


def ua():
    agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not agent or "@" not in agent:
        sys.exit(
            "SEC_USER_AGENT is not set.\n"
            "SEC requires a contact in the User-Agent header. Set it to your own\n"
            "name and email - this script will not guess one for you:\n\n"
            '    export SEC_USER_AGENT="Your Name your@email.example"\n'
        )
    return agent


def fetch(url, rate=0.22, retries=3):
    """Rate-limited GET. SEC asks for <10 req/s; we stay well under."""
    for attempt in range(retries):
        wait = rate - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        req = urllib.request.Request(url, headers={
            "User-Agent": ua(),
            "Accept-Encoding": "gzip, deflate",
            "Accept": "*/*",
        })
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    return None
