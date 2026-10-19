"""Loading collected data and turning it into plottable shapes."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from aaata_bus.definitions import DATA_PATH
from aaata_bus.schema import Bus, RouteData

DEFAULT_ROUTE_DATA_FILE = DATA_PATH / "route_prediction_data.jsonl"

#: Consecutive sightings of a bus further apart than this start a new path, so
#: a bus that leaves service and comes back later isn't joined by a straight line.
DEFAULT_MAX_GAP = timedelta(minutes=15)


@dataclass
class BusPath:
    """A single bus's track along a route, ordered by time."""

    route_id: str
    vehicle_id: str
    sightings: list[Bus] = field(default_factory=list)

    @property
    def coordinates(self) -> list[tuple[float, float]]:
        """(latitude, longitude) pairs in time order."""
        return [(bus.latitude, bus.longitude) for bus in self.sightings]

    @property
    def start(self) -> datetime:
        return self.sightings[0].timestamp

    @property
    def end(self) -> datetime:
        return self.sightings[-1].timestamp


def load_route_data(path: Path = DEFAULT_ROUTE_DATA_FILE) -> list[RouteData]:
    """Read every snapshot from a jsonl file written by scripts/collect_data.py."""
    with open(path) as f:
        return [RouteData.model_validate_json(line) for line in f if line.strip()]


def trace_bus_paths(
    snapshots: Iterable[RouteData],
    max_gap: timedelta = DEFAULT_MAX_GAP,
) -> list[BusPath]:
    """Group bus sightings into time-ordered paths, one per bus per stretch of service.

    Args:
        snapshots: Route snapshots, in any order.
        max_gap: Split a bus's path wherever two consecutive sightings are
            further apart than this.

    Returns:
        Paths with at least one sighting, sorted by route, vehicle, then start time.
    """
    sightings: dict[tuple[str, str], list[Bus]] = defaultdict(list)
    for snapshot in snapshots:
        for bus in snapshot.buses:
            sightings[(snapshot.route_id, bus.vehicle_id)].append(bus)

    paths: list[BusPath] = []
    for (route_id, vehicle_id), buses in sorted(sightings.items()):
        buses.sort(key=lambda bus: bus.timestamp)
        current = BusPath(route_id, vehicle_id)
        for bus in buses:
            if current.sightings and bus.timestamp - current.end > max_gap:
                paths.append(current)
                current = BusPath(route_id, vehicle_id)
            current.sightings.append(bus)
        paths.append(current)
    return paths
