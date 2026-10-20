"""Saved versions of each route's description, so pings are snapped to the
route as it was when they were collected.

The collector fetches each route's `getroutebyid` payload now and then and
appends it to `ROUTE_VERSIONS_FILE` (JSON Lines) only when it differs from the
last saved version of that route. Each line is a `RouteVersion`: the raw payload
plus `route_version`, an id derived from its content.

A bus carries no reference to a route version; its `pattern_id` is the only
link. So a sighting is matched to the versions of its route that contain its
pattern, and among those, by time: the latest fetched at or before the
sighting, or, for sightings older than every saved version, the earliest.
"""

import hashlib
import json
import random
import time
import warnings
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime, timezone, tzinfo
from functools import cached_property
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from aaata_bus.api import ROUTES_COURTESY_WAIT_RANGE_S, get_route_payload
from aaata_bus.definitions import DATA_PATH
from aaata_bus.geometry import PatternGeometry
from aaata_bus.schema import Route
from aaata_bus.times import to_utc

ROUTE_VERSIONS_FILE = DATA_PATH / "route_versions.jsonl"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sorted_collection(value: Any, key: str, sort_key: Callable[[Any], str]) -> Any:
    """A getroutebyid collection (`{key: [...]}`, `{key: {...}}` or `[]`) with its
    items in a fixed order."""
    if isinstance(value, dict) and isinstance(value.get(key), list):
        return {**value, key: sorted(value[key], key=sort_key)}
    return value


def canonical_payload(payload: Any) -> Any:
    """`payload` with the orderings the API doesn't keep stable put in a fixed order.

    getroutebyid lists a route's patterns in an order that varies between
    fetches of the same data (seen on route 66), so patterns, detours and
    bulletins are sorted. Points within a pattern stay in order, since their
    order is the path.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("route"), dict):
        return payload
    route = dict(payload["route"])
    by_id: Callable[[Any], str] = lambda item: str(item.get("id")) if isinstance(item, dict) else _canonical_json(item)
    for field, key, sort_key in (("pas", "pa", by_id), ("dtrs", "dtr", by_id), ("sbs", "sb", _canonical_json)):
        if field in route:
            route[field] = _sorted_collection(route[field], key, sort_key)
    return {**payload, "route": route}


def payload_hash(payload: Any) -> str:
    """SHA-256 of a payload's canonical JSON: key order and the order of
    patterns, detours and bulletins don't matter (see `canonical_payload`)."""
    return hashlib.sha256(_canonical_json(canonical_payload(payload)).encode()).hexdigest()


class RouteVersion(BaseModel):
    """One saved `getroutebyid` payload, as written to the route versions file."""

    # "<route_id>-<first 12 hex digits of content_hash>": the same content always
    # gets the same id, so a route that changes and changes back reuses its id.
    # Lines saved before 2026-10-20 ~03:00 UTC hashed patterns in API order, so a
    # few have a second id for the same content.
    route_version: str
    route_id: str
    # When the payload was fetched; the collector saves it timezone-aware (UTC),
    # unlike RouteData.requested_at. Naive values are read as `naive_tz` (UTC).
    # The content was in effect at this time, until the next saved version.
    fetched_at: datetime
    content_hash: str
    # The raw API response, kept as-is so it can be re-parsed as the schema evolves.
    payload: dict[str, Any]

    @classmethod
    def from_payload(cls, route_id: str, payload: dict[str, Any], fetched_at: datetime) -> "RouteVersion":
        content_hash = payload_hash(payload)
        return cls(
            route_version=f"{route_id}-{content_hash[:12]}",
            route_id=route_id,
            fetched_at=fetched_at,
            content_hash=content_hash,
            payload=payload,
        )

    @cached_property
    def route(self) -> Route:
        return Route.from_api(self.payload)

    def has_pattern(self, pattern_id: str) -> bool:
        """Whether this version has the pattern. A payload the current schema
        can't parse has none (it's saved raw, so it can be fixed later)."""
        try:
            return self.route.pattern(pattern_id) is not None
        except ValidationError:
            return False


class RouteHistory:
    """Every saved version of some routes, for looking up the geometry a
    sighting should be snapped to. Implements `geometry.GeometrySource`."""

    def __init__(self, versions: Iterable[RouteVersion] = (), naive_tz: tzinfo = timezone.utc) -> None:
        self.naive_tz = naive_tz
        self._versions: dict[str, list[RouteVersion]] = defaultdict(list)
        self._geometries: dict[tuple[str, str], PatternGeometry | None] = {}
        for version in versions:
            self.add(version)

    @classmethod
    def load(cls, path: Path = ROUTE_VERSIONS_FILE, naive_tz: tzinfo = timezone.utc) -> "RouteHistory":
        """Read a route versions file; a missing file gives an empty history."""
        if not path.exists():
            return cls(naive_tz=naive_tz)
        with open(path) as f:
            return cls(
                (RouteVersion.model_validate_json(line) for line in f if line.strip()), naive_tz=naive_tz
            )

    def add(self, version: RouteVersion) -> None:
        versions = self._versions[version.route_id]
        versions.append(version)
        versions.sort(key=lambda v: v.fetched_at)

    @property
    def route_ids(self) -> set[str]:
        return {route_id for route_id, versions in self._versions.items() if versions}

    def versions(self, route_id: str) -> list[RouteVersion]:
        """A route's saved versions, oldest first."""
        return list(self._versions.get(route_id, []))

    def latest(self, route_id: str) -> RouteVersion | None:
        versions = self._versions.get(route_id)
        return versions[-1] if versions else None

    def version_at(self, route_id: str, pattern_id: str, t: datetime) -> RouteVersion | None:
        """The version of a route a sighting at `t` on `pattern_id` belongs to.

        Only versions that have the pattern are considered. Of those, the
        latest fetched at or before `t`, or failing that the earliest.
        """
        candidates = [v for v in self._versions.get(route_id, []) if v.has_pattern(pattern_id)]
        if not candidates:
            return None
        t = to_utc(t, self.naive_tz)
        i = bisect_right([to_utc(v.fetched_at, self.naive_tz) for v in candidates], t)
        return candidates[i - 1] if i else candidates[0]

    def geometry(self, route_id: str, pattern_id: str, t: datetime) -> PatternGeometry | None:
        """Geometry of `pattern_id` in the version of the route in effect at `t`."""
        version = self.version_at(route_id, pattern_id, t)
        if version is None:
            return None
        key = (version.route_version, pattern_id)
        if key not in self._geometries:
            pattern = version.route.pattern(pattern_id)
            assert pattern is not None
            try:
                self._geometries[key] = PatternGeometry(
                    route_id, pattern, route_version=version.route_version
                )
            except ValueError:  # degenerate pattern
                self._geometries[key] = None
        return self._geometries[key]


def save_route_version(version: RouteVersion, history: RouteHistory, path: Path = ROUTE_VERSIONS_FILE) -> bool:
    """Append `version` to the file (and `history`) unless its content matches
    the latest saved version of the route. Returns whether it was saved.

    The latest version's hash is recomputed rather than read from the file, so
    lines saved before a change to `payload_hash` still compare correctly.
    """
    latest = history.latest(version.route_id)
    if latest is not None and payload_hash(latest.payload) == payload_hash(version.payload):
        return False
    with open(path, "a") as f:
        f.write(version.model_dump_json() + "\n")
    history.add(version)
    return True


def load_route_history(
    route_ids: Iterable[str],
    path: Path = ROUTE_VERSIONS_FILE,
    fetch_missing: bool = True,
    courtesy_wait_range_s: tuple[float, float] = ROUTES_COURTESY_WAIT_RANGE_S,
) -> RouteHistory:
    """Saved route versions, plus (with `fetch_missing`) a live fetch of each of
    `route_ids` with no saved version, e.g. for data collected before routes
    were saved. Live versions are stamped now and kept in memory only; since a
    sighting older than every version uses the earliest, they apply to all
    earlier data. A route that fails to fetch is skipped with a warning.
    """
    history = RouteHistory.load(path)
    if not fetch_missing:
        return history
    missing = sorted(set(route_ids) - history.route_ids)
    for i, route_id in enumerate(missing):
        if i:
            time.sleep(random.uniform(*courtesy_wait_range_s))
        fetched_at = datetime.now(timezone.utc)
        try:
            payload = get_route_payload(route_id)
        except httpx.HTTPError as e:
            warnings.warn(f"couldn't fetch route {route_id}: {e}", stacklevel=2)
            continue
        history.add(RouteVersion.from_payload(route_id, payload, fetched_at))
    return history
