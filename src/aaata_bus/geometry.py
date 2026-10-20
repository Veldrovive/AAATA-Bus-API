"""Linear referencing: positions as distance along a route pattern instead of lat/lon.

A `PatternGeometry` is one `RoutePattern`'s path as a polyline in a local flat
coordinate system measured in feet (an equirectangular projection around the
pattern's first point, accurate to well under 0.1% at city scale). Pings are
projected onto it to get `(dist_along_ft, offset_ft)`; a large offset means the
bus is off the path (detour, deadheading, GPS garbage).

Stops sit on path vertices, so their distances along the pattern are exact.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import cos, radians
from typing import Protocol

import numpy as np

from aaata_bus.schema import Bus, Route, RouteData, RoutePattern, RoutePoint
from aaata_bus.times import observation_time

#: Mean Earth radius (6,371,008.8 m) in feet.
EARTH_RADIUS_FT = 20_902_259.0

#: Pings further than this from the path are rejected. GPS error and the gap
#: between a street's centerline and its curb lane are well under this; buses on
#: an unmapped detour or deadheading are usually far more.
DEFAULT_MAX_OFFSET_FT = 150.0

#: Limits on what counts as a stop spur (see `PatternGeometry._remove_stop_spurs`).
#: Observed curb jogs are 1–2 vertices deep and 70–130 ft round trip; real
#: out-and-back trips into side streets or lots are longer.
MAX_STOP_SPUR_VERTICES = 6
MAX_STOP_SPUR_FT = 200.0
#: A spur's return vertex is often a foot or so off its outbound one.
SPUR_RETURN_TOLERANCE_FT = 3.0


@dataclass(frozen=True)
class StopPosition:
    """A stop on a pattern, located by distance along it."""

    stop_id: str
    stop_name: str
    dist_along_ft: float
    latitude: float
    longitude: float


@dataclass(frozen=True)
class Projection:
    """Where a point lands on a pattern."""

    dist_along_ft: float  # distance from the pattern's start to the snapped point
    offset_ft: float  # perpendicular distance from the point to the path
    latitude: float  # the snapped point
    longitude: float


class PatternGeometry:
    """A pattern's path, set up for projecting points onto it.

    On a detoured pattern only the path buses actually take is used: the
    bypassed regular path (`RoutePoint.is_bypassed`) is dropped, unless
    `use_bypassed` asks for the regular path instead. `route_version` records
    which saved `RouteVersion` the pattern came from.
    """

    def __init__(
        self,
        route_id: str,
        pattern: RoutePattern,
        use_bypassed: bool = False,
        route_version: str | None = None,
    ) -> None:
        detoured = any(point.is_bypassed for point in pattern.points)
        # Undetoured patterns have no bypassed points, so they always use all of them.
        points = [p for p in pattern.points if not detoured or p.is_bypassed == use_bypassed]
        if len(points) < 2:
            raise ValueError(f"pattern {pattern.pattern_id} has fewer than two points")

        self.route_id = route_id
        # Which saved route version (`RouteVersion.route_version`) this came from, if any.
        self.route_version = route_version
        self.pattern_id = pattern.pattern_id
        self.direction = pattern.direction
        self._lat0 = points[0].latitude
        self._lon0 = points[0].longitude
        self._x_scale = EARTH_RADIUS_FT * cos(radians(self._lat0))

        path, stop_bases = self._remove_stop_spurs(points)
        self.latitudes = np.array([p.latitude for p in path])
        self.longitudes = np.array([p.longitude for p in path])
        self.xy = self._to_xy(self.latitudes, self.longitudes)
        # Vertices repeat in the API data; zero-length segments are harmless but skipped.
        segments = np.diff(self.xy, axis=0)
        lengths = np.hypot(segments[:, 0], segments[:, 1])
        self.cum_dist_ft = np.concatenate([[0.0], np.cumsum(lengths)])
        keep = lengths > 0
        if not keep.any():
            raise ValueError(f"pattern {pattern.pattern_id} has zero length")
        self._seg_start = self.xy[:-1][keep]
        self._seg_vec = segments[keep]
        self._seg_len2 = lengths[keep] ** 2
        self._seg_dist0 = self.cum_dist_ft[:-1][keep]

        self.stops = []
        for stop_point, base in stop_bases:
            assert stop_point.stop is not None
            # A spur's stop sits off to the side, so locate it on the street
            # beside where its spur left, not at the spur's base vertex.
            near = float(self.cum_dist_ft[base])
            dist = self.project(stop_point.latitude, stop_point.longitude, near).dist_along_ft
            self.stops.append(StopPosition(
                stop_point.stop.stop_id, stop_point.stop.stop_name, dist,
                stop_point.latitude, stop_point.longitude,
            ))

    def _remove_stop_spurs(
        self, points: list[RoutePoint]
    ) -> tuple[list[RoutePoint], list[tuple[RoutePoint, int]]]:
        """Drop the short out-and-back spurs the API draws to many stops.

        Such stops are drawn as a jog off the street to the curb and back, e.g.
        `A, STOP, A` or `A, B, STOP, B, A` (the return vertex is often a foot or
        so off the outbound one). Left in, each adds ~70 ft to distances along
        the path, which TheRide's `distance_to_stop_ft` doesn't count, and pulls
        pings at the curb into the spur. A backtrack is only removed if it
        returns within `SPUR_RETURN_TOLERANCE_FT` of a recent vertex, contains a
        stop, and is at most `MAX_STOP_SPUR_FT` long in total (nested spurs
        included), so real out-and-back trips to a stop, which the agency does
        count, stay.

        Returns:
            The cleaned path, and each stop's point with the index in the cleaned
            path it hangs off.
        """
        path: list[RoutePoint] = []
        xy: list[np.ndarray] = []
        removed_ft: list[float] = []  # length of spurs already cut from each vertex
        stops: list[tuple[RoutePoint, int]] = []
        for point in points:
            p_xy = self._to_xy(np.array(point.latitude), np.array(point.longitude))
            base = self._spur_base(xy, removed_ft, stops, p_xy)
            if base is not None:
                spur = np.array(xy[base:] + [p_xy])
                removed_ft[base] = float(
                    np.hypot(*np.diff(spur, axis=0).T).sum() + sum(removed_ft[base:])
                )
                stops = [(s, min(i, base)) for s, i in stops]
                del path[base + 1:], xy[base + 1:], removed_ft[base + 1:]
                continue
            path.append(point)
            xy.append(p_xy)
            removed_ft.append(0.0)
            if point.stop is not None:
                stops.append((point, len(path) - 1))
        return path, stops

    @staticmethod
    def _spur_base(
        xy: list[np.ndarray],
        removed_ft: list[float],
        stops: list[tuple[RoutePoint, int]],
        point: np.ndarray,
    ) -> int | None:
        """Index of the vertex `point` returns to if that closes a stop spur."""
        for back in range(1, min(MAX_STOP_SPUR_VERTICES, len(xy) - 1) + 1):
            base = len(xy) - 1 - back
            if np.hypot(*(xy[base] - point)) > SPUR_RETURN_TOLERANCE_FT:
                continue
            # Stops already hung on a vertex by an inner spur count too.
            if not any(i > base for _, i in stops):
                return None
            spur = np.array(xy[base:] + [point])
            length = np.hypot(*np.diff(spur, axis=0).T).sum() + sum(removed_ft[base:])
            return base if length <= MAX_STOP_SPUR_FT else None
        return None

    def __repr__(self) -> str:
        return (
            f"PatternGeometry(route={self.route_id!r}, pattern={self.pattern_id!r}, "
            f"length_ft={self.length_ft:.0f}, stops={len(self.stops)})"
        )

    @property
    def length_ft(self) -> float:
        return float(self.cum_dist_ft[-1])

    def _to_xy(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        x = np.radians(lon - self._lon0) * self._x_scale
        y = np.radians(lat - self._lat0) * EARTH_RADIUS_FT
        return np.stack([x, y], axis=-1)

    def _to_latlon(self, xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        lat = self._lat0 + np.degrees(xy[..., 1] / EARTH_RADIUS_FT)
        lon = self._lon0 + np.degrees(xy[..., 0] / self._x_scale)
        return lat, lon

    def project_many(
        self,
        latitudes: Sequence[float] | np.ndarray,
        longitudes: Sequence[float] | np.ndarray,
        near_ft: Sequence[float | None] | None = None,
        max_offset_ft: float = DEFAULT_MAX_OFFSET_FT,
    ) -> list[Projection]:
        """Project many points at once. See `project`."""
        xy = self._to_xy(np.asarray(latitudes, dtype=float), np.asarray(longitudes, dtype=float))
        # Point-to-segment projection for every (point, segment) pair.
        rel = xy[:, None, :] - self._seg_start[None, :, :]
        t = np.clip((rel * self._seg_vec).sum(-1) / self._seg_len2, 0.0, 1.0)
        foot = self._seg_start + t[..., None] * self._seg_vec
        offset = np.hypot(*(xy[:, None, :] - foot).transpose(2, 0, 1))
        along = self._seg_dist0 + t * np.sqrt(self._seg_len2)

        best = offset.argmin(axis=1)
        if near_ft is not None:
            for i, near in enumerate(near_ft):
                if near is not None:
                    best[i] = self._nearest_pass(offset[i], along[i], near, max_offset_ft, best[i])

        rows = np.arange(len(xy))
        lat, lon = self._to_latlon(foot[rows, best])
        return [
            Projection(float(a), float(o), float(la), float(lo))
            for a, o, la, lo in zip(along[rows, best], offset[rows, best], lat, lon)
        ]

    @staticmethod
    def _nearest_pass(
        offset: np.ndarray, along: np.ndarray, near: float, max_offset_ft: float, best: int
    ) -> int:
        """Index of the best segment on the pass of the path nearest `near`.

        A pass is a contiguous run of segments within `max_offset_ft` of the
        point; each is represented by its closest segment. Picking among passes
        rather than segments keeps the hint from dragging the snap along the
        pass the point is actually on.
        """
        close = offset <= max(max_offset_ft, offset[best])
        # Boundaries of each contiguous run of close segments.
        edges = np.flatnonzero(np.diff(np.concatenate([[0], close.astype(np.int8), [0]])))
        passes = [
            start + int(offset[start:end].argmin()) for start, end in zip(edges[::2], edges[1::2])
        ]
        return min(passes, key=lambda j: abs(along[j] - near))

    def project(
        self,
        latitude: float,
        longitude: float,
        near_ft: float | None = None,
        max_offset_ft: float = DEFAULT_MAX_OFFSET_FT,
    ) -> Projection:
        """Project a point onto the path.

        Args:
            latitude, longitude: The point.
            near_ft: Where along the path the point is expected to be, e.g. the
                previous ping's distance. Where the path passes the point more
                than once (loops, out-and-back streets, a terminus at both ends),
                the pass within `max_offset_ft` closest to this is used. Without
                it, the nearest pass wins.
            max_offset_ft: How far from the path counts as on it, for `near_ft`.

        Returns:
            The projection. Check `offset_ft` against a threshold such as
            `DEFAULT_MAX_OFFSET_FT` to reject off-path points.
        """
        return self.project_many([latitude], [longitude], [near_ft], max_offset_ft)[0]

    def point_at(self, dist_along_ft: float) -> tuple[float, float]:
        """(latitude, longitude) of the point this far along the path (clamped to it)."""
        d = min(max(dist_along_ft, 0.0), self.length_ft)
        x = np.interp(d, self.cum_dist_ft, self.xy[:, 0])
        y = np.interp(d, self.cum_dist_ft, self.xy[:, 1])
        lat, lon = self._to_latlon(np.array([x, y]))
        return float(lat), float(lon)

    def stop_distances(self, stop_id: str) -> list[float]:
        """Distances along the path of every visit to a stop (a loop may visit one twice)."""
        return [stop.dist_along_ft for stop in self.stops if stop.stop_id == stop_id]


def pattern_geometries(routes: Iterable[Route]) -> dict[str, PatternGeometry]:
    """Geometry for every pattern of the given routes, keyed by pattern id
    (which matches `Bus.pattern_id`). Degenerate patterns (fewer than two
    distinct points) are skipped."""
    geometries = {}
    for route in routes:
        for pattern in route.patterns:
            try:
                geometries[pattern.pattern_id] = PatternGeometry(route.route_id, pattern)
            except ValueError:
                pass
    return geometries


class GeometrySource(Protocol):
    """Looks up the geometry a sighting should be projected onto, e.g. a
    `RouteHistory` of saved route versions."""

    def geometry(self, route_id: str, pattern_id: str, t: datetime) -> PatternGeometry | None: ...


#: Either fixed geometries keyed by pattern id (from `pattern_geometries`), used
#: for every sighting, or a source that picks one by route, pattern and time.
Geometries = Mapping[str, PatternGeometry] | GeometrySource

GeometryLookup = Callable[[str, str, datetime], PatternGeometry | None]


def geometry_lookup(geometries: Geometries) -> GeometryLookup:
    """`(route_id, pattern_id, t) -> PatternGeometry | None` for either kind of `Geometries`."""
    if isinstance(geometries, Mapping):
        fixed = geometries
        return lambda route_id, pattern_id, t: fixed.get(pattern_id)
    return geometries.geometry


@dataclass(frozen=True)
class SnappedPing:
    """A bus sighting located on its pattern."""

    bus: Bus
    projection: Projection | None  # None when the pattern's geometry is unknown
    max_offset_ft: float
    geometry: PatternGeometry | None = None  # what it was projected onto

    @property
    def on_route(self) -> bool:
        return self.projection is not None and self.projection.offset_ft <= self.max_offset_ft


def snap_sightings(
    sightings: Sequence[Bus],
    geometries: Geometries,
    max_offset_ft: float = DEFAULT_MAX_OFFSET_FT,
) -> list[SnappedPing]:
    """Project one bus's time-ordered sightings onto their patterns.

    Each sighting is projected near the previous on-route one on the same
    pattern geometry, so a bus is tracked through loops rather than jumping to
    whichever pass of the path is nearest. With a `GeometrySource`, sightings
    are looked up by `Bus.route_number` and `Bus.timestamp`.
    """
    lookup = geometry_lookup(geometries)
    snapped = []
    last: tuple[PatternGeometry, float] | None = None  # (geometry, dist_along_ft)
    for bus in sightings:
        geometry = lookup(bus.route_number, bus.pattern_id, bus.timestamp)
        if geometry is None:
            snapped.append(SnappedPing(bus, None, max_offset_ft))
            continue
        near = last[1] if last is not None and last[0] is geometry else None
        ping = SnappedPing(
            bus, geometry.project(bus.latitude, bus.longitude, near, max_offset_ft), max_offset_ft, geometry
        )
        if ping.on_route and ping.projection is not None:
            last = (geometry, ping.projection.dist_along_ft)
        snapped.append(ping)
    return snapped


@dataclass(frozen=True)
class StopDistanceCheck:
    """The agency's distance to a stop against ours, for one predicted stop."""

    vehicle_id: str
    pattern_id: str
    route_version: str | None  # the saved route version the geometry came from
    stop_id: str
    reported_ft: float  # BusPrediction.distance_to_stop_ft
    projected_ft: float  # stop's distance along the pattern minus the bus's
    offset_ft: float  # how far the bus was from the path

    @property
    def error_ft(self) -> float:
        return self.projected_ft - self.reported_ft


def check_stop_distances(
    snapshots: Iterable[RouteData],
    geometries: Geometries,
    max_offset_ft: float = DEFAULT_MAX_OFFSET_FT,
) -> list[StopDistanceCheck]:
    """Compare each prediction's `distance_to_stop_ft` with our linear referencing.

    For every on-route bus with predictions, the bus is projected near the
    position its nearest predicted stop implies (stop distance minus reported
    distance), which disambiguates loops. Predicted stops that aren't on the
    bus's pattern are skipped. Where a stop is visited twice, the visit at or
    beyond the bus is used. With a `GeometrySource`, each bus uses the route
    version in effect when it was seen.
    """
    lookup = geometry_lookup(geometries)
    checks = []
    for snapshot in snapshots:
        for bus in snapshot.buses:
            geometry = lookup(snapshot.route_id, bus.pattern_id, observation_time(snapshot, bus))
            predictions = snapshot.predictions.get(bus.vehicle_id, [])
            if geometry is None or not predictions:
                continue
            first = min(predictions, key=lambda p: p.distance_to_stop_ft)
            # Predictions run on into the bus's next trip, usually on another pattern.
            predictions = [p for p in predictions if p.trip_id == first.trip_id]
            first_stops = geometry.stop_distances(first.stop_id)
            near = min(first_stops) - first.distance_to_stop_ft if first_stops else None
            projection = geometry.project(bus.latitude, bus.longitude, near, max_offset_ft)
            if projection.offset_ft > max_offset_ft:
                continue
            for prediction in predictions:
                ahead = [d for d in geometry.stop_distances(prediction.stop_id)
                         if d >= projection.dist_along_ft - max_offset_ft]
                if not ahead:
                    continue
                checks.append(StopDistanceCheck(
                    vehicle_id=bus.vehicle_id,
                    pattern_id=bus.pattern_id,
                    route_version=geometry.route_version,
                    stop_id=prediction.stop_id,
                    reported_ft=prediction.distance_to_stop_ft,
                    projected_ft=min(ahead) - projection.dist_along_ft,
                    offset_ft=projection.offset_ft,
                ))
    return checks
