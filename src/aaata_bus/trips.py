"""Trip reconstruction: each vehicle's pings split into trips along a pattern.

A trip is one run of a vehicle along one pattern. A vehicle's time-ordered pings
start a new trip when its `pattern_id` changes, when it's not seen for a while,
or when its distance along the pattern jumps back (it started the pattern over).
Each trip's distances are smoothed into a non-decreasing curve by isotonic
regression, which absorbs GPS jitter and snapping noise.

Times are timezone-aware UTC. Snapshot times (`RouteData.requested_at`) and
`Bus.timestamp` are naive datetimes on the collector's clock, which is UTC in
the Docker container; pass `naive_tz` if data was collected elsewhere.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

from aaata_bus.geometry import DEFAULT_MAX_OFFSET_FT, PatternGeometry
from aaata_bus.schema import Bus, BusPrediction, RouteData

#: A vehicle unseen for longer than this starts a new trip.
DEFAULT_TRIP_GAP = timedelta(minutes=10)

#: Consecutive trips of a vehicle on the same pattern are rejoined across a gap
#: up to this long when the second carries on where the first left off (and
#: their trip ids don't disagree): a bus briefly off the path or unpolled.
DEFAULT_MERGE_GAP = timedelta(minutes=30)

#: A drop in distance along the pattern bigger than this (from the furthest the
#: trip has reached) means the vehicle started the pattern over. Snapping noise
#: is a few feet, so this only fires on real restarts.
DEFAULT_RESET_FT = 1000.0

#: Speed (ft/s, ~55 mph) above which a bus must have moved during a run of
#: repeated positions. Normal 30 s moves reach ~75 ft/s at the 99th percentile.
DEFAULT_STALE_SPEED_FTPS = 80.0


@dataclass(frozen=True)
class TripPing:
    """One sighting of a vehicle on a trip."""

    t: datetime  # observation time, UTC
    bus: Bus
    raw_dist_ft: float  # projected distance along the pattern
    offset_ft: float  # distance from the pattern's path
    # Same position as the vehicle's previous sighting. Usually the bus is
    # stopped, but sometimes the AVL unit just hasn't reported.
    is_repeat: bool
    dist_ft: float = 0.0  # raw_dist_ft made non-decreasing over the trip (set by isotonic fit)


@dataclass
class Trip:
    """One vehicle's run along one pattern."""

    route_id: str
    vehicle_id: str
    pattern_id: str
    pings: list[TripPing] = field(default_factory=list)
    # Pings dropped because they repeated a position the bus must have left
    # (see `DEFAULT_STALE_SPEED_FTPS`).
    stale_pings: list[TripPing] = field(default_factory=list)
    # The real-time trip id each prediction snapshot during the trip pointed at,
    # with counts. See `trip_id`.
    trip_id_votes: Counter[str] = field(default_factory=Counter)
    # From the predictions for the winning trip_id, when there are any.
    scheduled_trip_id: str | None = None
    service_date: date | None = None
    trip_start_seconds: int | None = None  # scheduled start, seconds after midnight
    # One prediction per voted trip id, to fill in the schedule fields above.
    _predictions: dict[str, BusPrediction] = field(default_factory=dict, repr=False, compare=False)

    @property
    def trip_key(self) -> str:
        """Unique key: vehicle, pattern and start time."""
        return f"{self.vehicle_id}-{self.pattern_id}-{self.start:%Y%m%dT%H%M%SZ}"

    @property
    def start(self) -> datetime:
        return self.pings[0].t

    @property
    def end(self) -> datetime:
        return self.pings[-1].t

    @property
    def trip_id(self) -> str | None:
        """The real-time trip id (`BusPrediction.trip_id`) most predictions
        during this trip were for; None when no predictions were collected."""
        return self.trip_id_votes.most_common(1)[0][0] if self.trip_id_votes else None


def to_utc(value: datetime, naive_tz: tzinfo = timezone.utc) -> datetime:
    """Make `value` aware UTC, reading naive datetimes as `naive_tz`."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=naive_tz)
    return value.astimezone(timezone.utc)


def observation_time(snapshot: RouteData, bus: Bus, naive_tz: tzinfo = timezone.utc) -> datetime:
    """When a sighting was made: the snapshot's request time, or for data saved
    before `requested_at` existed, the bus's (local, post-response) timestamp."""
    return to_utc(snapshot.requested_at or bus.timestamp, naive_tz)


def isotonic_fit(values: Sequence[float]) -> list[float]:
    """Least-squares non-decreasing fit to `values` (pool adjacent violators)."""
    blocks: list[list[float]] = []  # [mean, count]
    for value in values:
        blocks.append([value, 1.0])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            mean, count = blocks.pop()
            prev = blocks[-1]
            prev[0] = (prev[0] * prev[1] + mean * count) / (prev[1] + count)
            prev[1] += count
    fitted: list[float] = []
    for mean, count in blocks:
        fitted.extend([mean] * int(count))
    return fitted


@dataclass(frozen=True)
class _Sighting:
    t: datetime
    route_id: str
    bus: Bus
    predictions: list[BusPrediction]


def _vehicle_sightings(snapshots: Iterable[RouteData], naive_tz: tzinfo) -> dict[str, list[_Sighting]]:
    by_vehicle: dict[str, list[_Sighting]] = defaultdict(list)
    for snapshot in snapshots:
        for bus in snapshot.buses:
            by_vehicle[bus.vehicle_id].append(_Sighting(
                observation_time(snapshot, bus, naive_tz),
                snapshot.route_id,
                bus,
                snapshot.predictions.get(bus.vehicle_id, []),
            ))
    for sightings in by_vehicle.values():
        sightings.sort(key=lambda s: s.t)
    return by_vehicle


def _current_trip_prediction(
    predictions: list[BusPrediction], geometry: PatternGeometry
) -> BusPrediction | None:
    """The prediction for the bus's next stop, if that stop is on its pattern.

    Predictions run on into the bus's next trip; near a terminus the nearest
    predicted stop can belong to that next trip on another pattern, so it only
    counts when the stop is on this one.
    """
    if not predictions:
        return None
    nearest = min(predictions, key=lambda p: p.distance_to_stop_ft)
    return nearest if geometry.stop_distances(nearest.stop_id) else None


def _drop_stale_repeats(trip: Trip, stale_speed_ftps: float) -> None:
    """Drop runs of repeated positions after which the bus moves implausibly
    fast, i.e. it must have been moving while its position was frozen."""
    kept: list[TripPing] = []
    run: list[TripPing] = []
    for ping in trip.pings:
        if ping.is_repeat:
            run.append(ping)
            continue
        if run:
            last = run[-1]
            elapsed = (ping.t - last.t).total_seconds()
            moved = ping.raw_dist_ft - last.raw_dist_ft
            if elapsed > 0 and moved / elapsed > stale_speed_ftps:
                trip.stale_pings.extend(run)
            else:
                kept.extend(run)
            run = []
        kept.append(ping)
    kept.extend(run)  # a trailing run has nothing after it to judge by
    trip.pings = kept


def _continues(first: Trip, second: Trip, merge_gap: timedelta, reset_ft: float) -> bool:
    """Whether `second` is the rest of `first`, split by a gap."""
    return (
        first.vehicle_id == second.vehicle_id
        and first.pattern_id == second.pattern_id
        and second.start - first.end <= merge_gap
        and second.pings[0].raw_dist_ft >= max(p.raw_dist_ft for p in first.pings) - reset_ft
        and (first.trip_id is None or second.trip_id is None or first.trip_id == second.trip_id)
    )


def _merge(first: Trip, second: Trip) -> Trip:
    first.pings.extend(second.pings)
    first.trip_id_votes.update(second.trip_id_votes)
    for trip_id, prediction in second._predictions.items():
        first._predictions.setdefault(trip_id, prediction)
    return first


def _finish(trip: Trip, stale_speed_ftps: float) -> Trip:
    _drop_stale_repeats(trip, stale_speed_ftps)
    fitted = isotonic_fit([p.raw_dist_ft for p in trip.pings])
    trip.pings = [
        TripPing(p.t, p.bus, p.raw_dist_ft, p.offset_ft, p.is_repeat, dist) for p, dist in zip(trip.pings, fitted)
    ]
    if (prediction := trip._predictions.get(trip.trip_id or "")) is not None:
        trip.scheduled_trip_id = prediction.scheduled_trip_id
        trip.service_date = prediction.service_date
        trip.trip_start_seconds = prediction.trip_start_seconds
    return trip


def reconstruct_trips(
    snapshots: Iterable[RouteData],
    geometries: dict[str, PatternGeometry],
    max_gap: timedelta = DEFAULT_TRIP_GAP,
    merge_gap: timedelta = DEFAULT_MERGE_GAP,
    reset_ft: float = DEFAULT_RESET_FT,
    max_offset_ft: float = DEFAULT_MAX_OFFSET_FT,
    stale_speed_ftps: float = DEFAULT_STALE_SPEED_FTPS,
    naive_tz: tzinfo = timezone.utc,
) -> list[Trip]:
    """Split every vehicle's pings into trips along their patterns.

    Pings on patterns without a geometry, or further than `max_offset_ft` from
    the path (deadheading, unmapped detours, GPS garbage), are left out; they
    don't end a trip by themselves.

    Args:
        snapshots: Collected snapshots, in any order.
        geometries: From `pattern_geometries`, keyed by pattern id.
        max_gap: Start a new trip after a vehicle goes unseen this long.
        merge_gap: Afterwards, rejoin consecutive trips on the same pattern
            split by a gap up to this long, if the later one carries on
            forward and their trip ids don't disagree.
        reset_ft: Start a new trip when the distance along the pattern falls
            this far below the furthest the trip has reached.
        max_offset_ft: Leave out pings further than this from the path.
        stale_speed_ftps: Drop runs of repeated positions followed by a move
            faster than this.
        naive_tz: Timezone of naive snapshot and bus timestamps.

    Returns:
        Trips with at least one ping, sorted by vehicle then start time.
    """
    pieces: list[Trip] = []
    for vehicle_id, sightings in sorted(_vehicle_sightings(snapshots, naive_tz).items()):
        trip: Trip | None = None
        furthest = 0.0
        last: _Sighting | None = None  # the vehicle's previous sighting, on route or not
        last_on_route: datetime | None = None

        def close() -> None:
            nonlocal trip
            if trip is not None:
                pieces.append(trip)
            trip = None

        for sighting in sightings:
            bus = sighting.bus
            is_repeat = (
                last is not None and (last.bus.latitude, last.bus.longitude) == (bus.latitude, bus.longitude)
            )
            last = sighting
            geometry = geometries.get(bus.pattern_id)
            if geometry is None:
                continue

            continues = (
                trip is not None
                and trip.pattern_id == bus.pattern_id
                and last_on_route is not None
                and sighting.t - last_on_route <= max_gap
            )
            # A new trip starts at the pattern's start, which resolves a
            # terminus that is also the end of the path.
            near = trip.pings[-1].raw_dist_ft if continues and trip is not None else 0.0
            projection = geometry.project(bus.latitude, bus.longitude, near, max_offset_ft)
            if projection.offset_ft > max_offset_ft:
                continue
            if continues and projection.dist_along_ft < furthest - reset_ft:
                continues = False
                projection = geometry.project(bus.latitude, bus.longitude, 0.0, max_offset_ft)
            if not continues:
                close()
                trip = Trip(sighting.route_id, vehicle_id, bus.pattern_id)
                furthest = 0.0
            assert trip is not None

            trip.pings.append(TripPing(
                sighting.t, bus, projection.dist_along_ft, projection.offset_ft, is_repeat
            ))
            furthest = max(furthest, projection.dist_along_ft)
            last_on_route = sighting.t
            if (prediction := _current_trip_prediction(sighting.predictions, geometry)) is not None:
                trip.trip_id_votes[prediction.trip_id] += 1
                trip._predictions.setdefault(prediction.trip_id, prediction)
        close()

    trips: list[Trip] = []
    for piece in pieces:
        if trips and _continues(trips[-1], piece, merge_gap, reset_ft):
            _merge(trips[-1], piece)
        else:
            trips.append(piece)
    return [_finish(trip, stale_speed_ftps) for trip in trips]


#: Columns of `trip_pings_rows`, in order.
TRIP_PING_COLUMNS = [
    "trip_key", "t", "dist_ft", "raw_dist_ft", "offset_ft", "is_repeat",
    "latitude", "longitude", "route_id", "pattern_id", "vehicle_id", "operator_id",
    "run", "block_id", "trip_id", "scheduled_trip_id", "service_date", "trip_start_seconds",
]


def trip_pings_rows(trips: Iterable[Trip]) -> list[dict[str, Any]]:
    """The tidy `trip_pings` table: one row per kept ping, columns as in
    `TRIP_PING_COLUMNS`. Stale pings are left out."""
    rows = []
    for trip in trips:
        for ping in trip.pings:
            rows.append({
                "trip_key": trip.trip_key,
                "t": ping.t.isoformat(),
                "dist_ft": round(ping.dist_ft, 1),
                "raw_dist_ft": round(ping.raw_dist_ft, 1),
                "offset_ft": round(ping.offset_ft, 1),
                "is_repeat": ping.is_repeat,
                "latitude": ping.bus.latitude,
                "longitude": ping.bus.longitude,
                "route_id": trip.route_id,
                "pattern_id": trip.pattern_id,
                "vehicle_id": trip.vehicle_id,
                "operator_id": ping.bus.operator_id,
                "run": ping.bus.run_number,
                "block_id": ping.bus.block_id,
                "trip_id": trip.trip_id,
                "scheduled_trip_id": trip.scheduled_trip_id,
                "service_date": trip.service_date.isoformat() if trip.service_date else None,
                "trip_start_seconds": trip.trip_start_seconds,
            })
    return rows
