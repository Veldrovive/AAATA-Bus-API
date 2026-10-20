"""
Splits collected bus pings into trips along their route patterns.

Writes the tidy trip_pings table (one row per ping: trip key, UTC time, distance
along the pattern before and after isotonic smoothing, vehicle, operator, run,
block, pattern, the route version it was snapped to, and the trip ids joined
from predictions) as CSV, and a
time–distance chart of every trip.
"""
import argparse
import csv
from collections import Counter
from datetime import timedelta
from pathlib import Path

from aaata_bus import DATA_PATH, ensure_dat
from aaata_bus.geometry import DEFAULT_MAX_OFFSET_FT
from aaata_bus.trips import (
    DEFAULT_MERGE_GAP,
    DEFAULT_RESET_FT,
    DEFAULT_STALE_SPEED_FTPS,
    DEFAULT_TRIP_GAP,
    TRIP_PING_COLUMNS,
    reconstruct_trips,
    trip_pings_rows,
)
from aaata_bus.times import observation_time
from aaata_bus.route_history import ROUTE_VERSIONS_FILE, load_route_history
from aaata_bus.visualization import load_route_data, plot_trips
from aaata_bus.visualization.data import DEFAULT_ROUTE_DATA_FILE
from aaata_bus.visualization.serve import DEFAULT_SERVE_PORT, serve


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_ROUTE_DATA_FILE,
        help=f"Collected jsonl file (default: {DEFAULT_ROUTE_DATA_FILE})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DATA_PATH / "trip_pings.csv",
        help="Where to write the trip_pings table (default: trip_pings.csv in the data directory)",
    )
    parser.add_argument(
        "--plot",
        type=Path,
        default=DATA_PATH / "trips.html",
        help="Where to write the time–distance chart (default: trips.html in the data directory)",
    )
    parser.add_argument("--no-plot", action="store_true", help="Don't write the chart")
    parser.add_argument("--routes", nargs="+", help="Only these route IDs")
    parser.add_argument(
        "--max-gap-minutes", type=float, default=DEFAULT_TRIP_GAP.total_seconds() / 60,
        help="Start a new trip after a vehicle goes unseen this long",
    )
    parser.add_argument(
        "--merge-gap-minutes", type=float, default=DEFAULT_MERGE_GAP.total_seconds() / 60,
        help="Rejoin trips split by a gap up to this long if the later one carries on forward",
    )
    parser.add_argument(
        "--reset-ft", type=float, default=DEFAULT_RESET_FT,
        help="Start a new trip when distance along the pattern drops this far",
    )
    parser.add_argument(
        "--max-offset-ft", type=float, default=DEFAULT_MAX_OFFSET_FT,
        help="Leave out pings further than this from their path",
    )
    parser.add_argument(
        "--stale-speed-ftps", type=float, default=DEFAULT_STALE_SPEED_FTPS,
        help="Drop repeated positions followed by a move faster than this",
    )
    parser.add_argument(
        "--serve", nargs="?", type=int, const=DEFAULT_SERVE_PORT, metavar="PORT",
        help=f"After writing, serve the chart over HTTP (default port: {DEFAULT_SERVE_PORT})",
    )
    parser.add_argument(
        "--route-versions",
        type=Path,
        default=ROUTE_VERSIONS_FILE,
        help=f"Saved route versions from the collector (default: {ROUTE_VERSIONS_FILE})",
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Don't fetch routes that have no saved version (their pings are left out)",
    )
    args = parser.parse_args()
    ensure_dat()

    snapshots = load_route_data(args.input)
    if args.routes:
        wanted = set(args.routes)
        snapshots = [s for s in snapshots if s.route_id in wanted]
    # Each ping is snapped to the saved version of its route in effect when it
    # was seen; routes with no saved version are fetched live.
    geometries = load_route_history(
        {s.route_id for s in snapshots}, args.route_versions, fetch_missing=not args.no_fetch
    )
    trips = reconstruct_trips(
        snapshots,
        geometries,
        max_gap=timedelta(minutes=args.max_gap_minutes),
        merge_gap=timedelta(minutes=args.merge_gap_minutes),
        reset_ft=args.reset_ft,
        max_offset_ft=args.max_offset_ft,
        stale_speed_ftps=args.stale_speed_ftps,
    )

    rows = trip_pings_rows(trips)
    with open(args.output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRIP_PING_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows):,} pings in {len(trips)} trips to {args.output}")

    sightings = sum(len(s.buses) for s in snapshots)
    unknown = sum(
        1 for s in snapshots for b in s.buses
        if geometries.geometry(s.route_id, b.pattern_id, observation_time(s, b)) is None
    )
    stale = sum(len(t.stale_pings) for t in trips)
    print(f"  {sightings:,} sightings: {unknown:,} on patterns with no path, "
          f"{sightings - unknown - len(rows) - stale:,} off the path, {stale:,} dropped as stale")
    with_id = [t for t in trips if t.trip_id is not None]
    print(f"  {len(with_id)} of {len(trips)} trips matched to a trip id; "
          f"{sum(len(t.trip_id_votes) > 1 for t in with_id)} had predictions pointing at more than one")
    per_route = Counter(t.route_id for t in trips)
    print("  trips per route: " + ", ".join(f"{r}: {n}" for r, n in sorted(per_route.items())))
    per_version = Counter(t.route_version for t in trips)
    print("  trips per route version: " + ", ".join(f"{v}: {n}" for v, n in sorted(per_version.items(), key=str)))

    if not args.no_plot:
        plot_trips(trips).write_html(args.plot, include_plotlyjs=True)
        print(f"Wrote chart to {args.plot}")
        if args.serve is not None:
            serve(args.plot, args.serve)


if __name__ == "__main__":
    main()
