"""
Writes an HTML map of the paths traced by each route's buses.
"""
import argparse
from datetime import timedelta
from pathlib import Path

from aaata_bus import DATA_PATH
from aaata_bus.visualization import load_route_data, plot_route_paths, trace_bus_paths
from aaata_bus.visualization.data import DEFAULT_MAX_GAP, DEFAULT_ROUTE_DATA_FILE


def main():
    parser = argparse.ArgumentParser(description="Map the paths traced by each route's buses.")
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_ROUTE_DATA_FILE,
        help=f"Collected jsonl file (default: {DEFAULT_ROUTE_DATA_FILE})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DATA_PATH / "route_paths.html",
        help="Where to write the HTML map (default: route_paths.html in the data directory)",
    )
    parser.add_argument(
        "--routes",
        nargs="+",
        help="Only plot these route IDs (default: all routes in the data)",
    )
    parser.add_argument(
        "--max-gap-minutes",
        type=float,
        default=DEFAULT_MAX_GAP.total_seconds() / 60,
        help="Break a bus's line when sightings are further apart than this",
    )
    parser.add_argument(
        "--no-points",
        action="store_true",
        help="Draw only the lines, not each sighting",
    )
    args = parser.parse_args()

    snapshots = load_route_data(args.input)
    if args.routes:
        routes = set(args.routes)
        snapshots = [s for s in snapshots if s.route_id in routes]
    paths = trace_bus_paths(snapshots, max_gap=timedelta(minutes=args.max_gap_minutes))

    plot_route_paths(paths, show_points=not args.no_points).save(args.output)
    print(f"Wrote {len(paths)} paths to {args.output}")


if __name__ == "__main__":
    main()
