"""
Writes an HTML map of the paths traced by each route's buses.
"""
import argparse
import random
import time
from datetime import timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import httpx

from aaata_bus import DATA_PATH, Route, get_route
from aaata_bus.visualization import load_route_data, plot_route_paths, trace_bus_paths
from aaata_bus.visualization.data import DEFAULT_MAX_GAP, DEFAULT_ROUTE_DATA_FILE

DEFAULT_SERVE_PORT = 8000
#: Seconds to wait between route requests, to be polite to the API.
COURTESY_WAIT_TIME_RANGE_S = (0.5, 1.5)


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
        help="Don't fetch and draw each route's scheduled path (works offline)",
    )
    parser.add_argument(
        "--no-stops",
        action="store_true",
        help="Draw route paths without marking their stops",
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

    routes = [] if args.no_route_shapes else fetch_routes(sorted({s.route_id for s in snapshots}))

    plot_route_paths(
        paths, show_points=not args.no_points, routes=routes, show_stops=not args.no_stops
    ).save(args.output)
    print(f"Wrote {len(paths)} paths to {args.output}")

    if args.serve is not None:
        serve(args.output, args.serve)


def fetch_routes(route_ids: list[str]) -> list[Route]:
    """Fetch each route's description, skipping (with a warning) any that fail."""
    routes = []
    for i, route_id in enumerate(route_ids):
        if i:
            time.sleep(random.uniform(*COURTESY_WAIT_TIME_RANGE_S))
        try:
            routes.append(get_route(route_id))
        except httpx.HTTPError as e:
            print(f"Warning: couldn't fetch route {route_id}, drawing without its path: {e}")
    return routes


def serve(path: Path, port: int) -> None:
    """Serve the directory containing `path` until interrupted."""
    handler = partial(SimpleHTTPRequestHandler, directory=str(path.resolve().parent))
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        print(f"Serving map at http://localhost:{port}/{quote(path.name)} (Ctrl+C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
