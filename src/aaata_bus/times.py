"""Normalizing the collector's timestamps.

Snapshot times (`RouteData.requested_at`), `Bus.timestamp` and
`RouteVersion.fetched_at` are naive datetimes on the collector's clock, which is
UTC in the Docker container. Prediction times are America/Detroit-aware.
"""

from datetime import datetime, timezone, tzinfo

from aaata_bus.schema import Bus, RouteData


def to_utc(value: datetime, naive_tz: tzinfo = timezone.utc) -> datetime:
    """Make `value` aware UTC, reading naive datetimes as `naive_tz`."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=naive_tz)
    return value.astimezone(timezone.utc)


def observation_time(snapshot: RouteData, bus: Bus, naive_tz: tzinfo = timezone.utc) -> datetime:
    """When a sighting was made: the snapshot's request time, or for data saved
    before `requested_at` existed, the bus's (local, post-response) timestamp."""
    return to_utc(snapshot.requested_at or bus.timestamp, naive_tz)
