"""
Writes an HTML map of the paths traced by each route's buses.
"""
import argparse
from datetime import timedelta
from pathlib import Path

from aaata_bus import DATA_PATH
from aaata_bus.route_history import ROUTE_VERSIONS_FILE, load_route_history
from aaata_bus.geometry import DEFAULT_MAX_OFFSET_FT
from aaata_bus.visualization import load_route_data, plot_route_paths, trace_bus_paths
from aaata_bus.visualization.data import DEFAULT_MAX_GAP, DEFAULT_ROUTE_DATA_FILE
from aaata_bus.visualization.serve import DEFAULT_SERVE_PORT, serve


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
    parser.add_argument(
        "--no-route-shapes",
        action="store_true",
        help="Don't draw each route's scheduled path or snap pings to it",
    )
    parser.add_argument(
        "--no-stops",
        action="store_true",
        help="Draw route paths without marking their stops",
    )
    parser.add_argument(
        "--pings",
        choices=["raw", "snapped"],
        default="raw",
        help="Which bus pings the map opens showing: raw sightings joined by lines, or "
        "snapped onto their route's path, without lines (default: raw). The map has a "
        "toggle to switch. Snapping needs the route shapes.",
    )
    parser.add_argument(
        "--max-offset-ft",
        type=float,
        default=DEFAULT_MAX_OFFSET_FT,
        help="Pings further than this from their path aren't snapped "
        f"(default: {DEFAULT_MAX_OFFSET_FT:g})",
    )
    parser.add_argument(
        "--route-versions",
        type=Path,
        default=ROUTE_VERSIONS_FILE,
        help=f"Saved route versions from the collector (default: {ROUTE_VERSIONS_FILE})",
    )
    parser.add_argument(
        "--serve",
        nargs="?",
        type=int,
        const=DEFAULT_SERVE_PORT,
        metavar="PORT",
        help=f"After writing, serve the map over HTTP (default port: {DEFAULT_SERVE_PORT}). "
        "Handy on a remote machine: forward the port (VS Code does this automatically, "
        "or use `ssh -L PORT:localhost:PORT host`) and open the printed URL.",
    )
    args = parser.parse_args()

    snapshots = load_route_data(args.input)
    if args.routes:
        wanted = set(args.routes)
        snapshots = [s for s in snapshots if s.route_id in wanted]
    paths = trace_bus_paths(snapshots, max_gap=timedelta(minutes=args.max_gap_minutes))

    history = None
    routes = []
    if not args.no_route_shapes:
        # Each route is drawn as its latest saved version (or fetched live if it
        # has none), and pings snap to the version in effect when they were seen.
        route_ids = {s.route_id for s in snapshots}
        history = load_route_history(route_ids, args.route_versions)
        latest = (history.latest(route_id) for route_id in sorted(route_ids))
        routes = [version.route for version in latest if version is not None]

    plot_route_paths(
        paths,
        show_points=not args.no_points,
        routes=routes,
        show_stops=not args.no_stops,
        ping_view=args.pings,
        max_offset_ft=args.max_offset_ft,
        geometries=history,
    ).save(args.output)
    print(f"Wrote {len(paths)} paths to {args.output}")

    if args.serve is not None:
        serve(args.output, args.serve)


if __name__ == "__main__":
    main()
