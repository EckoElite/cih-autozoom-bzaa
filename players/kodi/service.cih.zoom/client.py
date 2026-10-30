"""The analyzer's HTTP API, from the add-on's side (spec R6, R9, R11).

Nothing here imports Kodi. A lookup is one short request (default timeout 1 s) so it can never hold playback up. A
timeline that arrives is also written to the add-on's profile folder; when the analyzer cannot be reached that copy is
used. A title the analyzer does not have yet (404) is asked for with `analyze=1`, which queues it at priority.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from identify import cache_key


@dataclass
class Lookup:
    """`status`: "ok" (fresh from the analyzer), "cached" (analyzer unreachable, copy from disk), "missing" (the
    analyzer does not have it yet), "unreachable" (and no copy), or "unknown" (nothing identifies the title)."""

    status: str
    timeline: dict | None = None

    @property
    def override(self) -> dict | None:
        return (self.timeline or {}).get("override")


class AnalyzerClient:
    def __init__(self, base_url: str, api_key: str = "", cache_dir: str | None = None, timeout: float = 1.0,
                 opener=urllib.request.urlopen):  # fmt: skip
        self.base = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.opener = opener

    # ---- requests ----------------------------------------------------------------------------------------------
    def _request(self, method: str, route: str, query: dict | None = None, body: dict | None = None):
        url = self.base + route
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - the analyzer on the LAN
        if self.api_key:
            req.add_header("X-Api-Key", self.api_key)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        return self.opener(req, timeout=self.timeout)

    @staticmethod
    def _query(ident: dict) -> dict:
        """Every key we have: the analyzer tries them in turn (path, part id, rating key) until one finds the title."""
        return {k: ident[k] for k in ("part_id", "rating_key", "path") if ident.get(k)}

    def timeline(self, ident: dict, request_analysis: bool = True) -> Lookup:
        query = self._query(ident)
        if not query:
            return Lookup("unknown")
        if not self.base:
            return Lookup("unreachable", self._read_cache(ident))
        try:
            with self._request("GET", "/api/v1/timeline", query) as resp:
                timeline = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                if request_analysis:
                    self.request_analysis(ident)
                cached = self._read_cache(ident)  # an older timeline beats none while the new one is made
                return Lookup("cached", cached) if cached else Lookup("missing")
            return self._offline(ident)
        except (urllib.error.URLError, OSError, ValueError):
            return self._offline(ident)
        self._write_cache(ident, timeline)
        return Lookup("ok", timeline)

    def _offline(self, ident: dict) -> Lookup:
        cached = self._read_cache(ident)
        return Lookup("cached", cached) if cached else Lookup("unreachable")

    def request_analysis(self, ident: dict) -> bool:
        """Queue the title at priority. Returns False if the request could not be made."""
        try:
            with self._request("POST", "/api/v1/analyze", None, {k: v for k, v in ident.items() if v and k != "kind"}):
                return True
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def set_override(self, ident: dict, mode: str, preset: float | None = None) -> bool:
        """`mode`: "preset" (with a ratio), "disable" or "clear". Persisted by the analyzer, per title."""
        body = {k: v for k, v in ident.items() if v and k != "kind"}
        body["mode"] = mode
        if preset is not None:
            body["preset"] = float(preset)
        try:
            with self._request("POST", "/api/v1/override", None, body):
                return True
        except (urllib.error.URLError, OSError, ValueError):
            return False

    # ---- the on-device copy --------------------------------------------------------------------------------------
    def _path(self, ident: dict) -> str | None:
        key = cache_key(ident)
        return os.path.join(self.cache_dir, key + ".json") if key and self.cache_dir else None

    def _write_cache(self, ident: dict, timeline: dict) -> None:
        path = self._path(ident)
        if not path:
            return
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(timeline, fh)
            os.replace(tmp, path)
        except OSError:
            pass  # the copy is a courtesy

    def _read_cache(self, ident: dict) -> dict | None:
        path = self._path(ident)
        if not path:
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None
