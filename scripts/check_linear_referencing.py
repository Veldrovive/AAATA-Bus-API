"""
Checks our linear referencing against TheRide's own distances.

Every prediction carries `distance_to_stop_ft`, the agency's distance from the bus
to the stop. This compares it with the distance along the route pattern from the
bus's projected position to the stop's, as computed by `aaata_bus.geometry`.

The error splits into two parts, reported separately:
- a per-bus position term (the median error over a bus's predicted stops in one
  snapshot), the same for every stop: our ping and the agency's idea of where the
  bus is disagree;
- a per-stop geometry term (the rest): our path between stops and the agency's
  disagree.
"""
import argparse
from collections import defaultdict
from pathlib import Path
from statistics import median

from aaata_bus import check_stop_distances, get_routes, pattern_geometries
from aaata_bus.geometry import DEFAULT_MAX_OFFSET_FT, PatternGeometry, StopDistanceCheck, snap_sightings
from aaata_bus.visualization import load_route_data, trace_bus_paths
from aaata_bus.visualization.data import DEFAULT_ROUTE_DATA_FILE

PERCENTILES = (5, 25, 50, 75, 95)


def percentiles(values: list[float]) -> str:
    ordered = sorted(values)
    picks = [ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))] for p in PERCENTILES]
    return "  ".join(f"p{p}={v:7,.0f}" for p, v in zip(PERCENTILES, picks))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_ROUTE_DATA_FILE,
        help=f"Collected jsonl file (default: {DEFAULT_ROUTE_DATA_FILE})",
    )
    parser.add_argument("--routes", nargs="+", help="Only check these route IDs")
    parser.add_argument(
        "--max-offset-ft",
        type=float,
        default=DEFAULT_MAX_OFFSET_FT,
        help=f"Reject pings further than this from their path (default: {DEFAULT_MAX_OFFSET_FT:g})",
    )
    args = parser.parse_args()

    snapshots = load_route_data(args.input)
    if args.routes:
        wanted = set(args.routes)
        snapshots = [s for s in snapshots if s.route_id in wanted]
    # Shapes are fetched live, so data collected under a different detour or
    # service change won't line up.
    geometries = pattern_geometries(get_routes(sorted({s.route_id for s in snapshots})))

    report_offsets(snapshots, geometries, args.max_offset_ft)

    checks: list[StopDistanceCheck] = []
    groups: list[list[StopDistanceCheck]] = []
    for snapshot in snapshots:
        by_bus: dict[str, list[StopDistanceCheck]] = defaultdict(list)
        for check in check_stop_distances([snapshot], geometries, args.max_offset_ft):
            by_bus[check.vehicle_id].append(check)
        groups.extend(by_bus.values())
        checks.extend(c for group in by_bus.values() for c in group)
    if not checks:
        print("No predictions to check against.")
        return

    print(f"\nDistance to stop, ours minus TheRide's (ft), over {len(checks):,} predicted stops:")
    print(f"  {'all':>8}  {percentiles([c.error_ft for c in checks])}")
    by_pattern: dict[str, list[float]] = defaultdict(list)
    for check in checks:
        by_pattern[check.pattern_id].append(check.error_ft)
    for pattern_id, errors in sorted(by_pattern.items()):
        print(f"  {pattern_id:>8}  {percentiles(errors)}  n={len(errors):,}")

    position = [median(c.error_ft for c in group) for group in groups]
    geometry = [c.error_ft - p for group, p in zip(groups, position) for c in group]
    print(f"\nPer-bus position term ({len(position):,} bus snapshots):")
    print(f"  {'':>8}  {percentiles(position)}")
    print("Per-stop geometry term:")
    print(f"  {'':>8}  {percentiles(geometry)}")


def report_offsets(snapshots, geometries: dict[str, PatternGeometry], max_offset_ft: float) -> None:
    offsets: dict[str, list[float]] = defaultdict(list)
    unknown = 0
    for path in trace_bus_paths(snapshots):
        for ping in snap_sightings(path.sightings, geometries, max_offset_ft):
            if ping.projection is None:
                unknown += 1
            else:
                offsets[ping.bus.pattern_id].append(ping.projection.offset_ft)
    print("Ping offset from the pattern path (ft):")
    for pattern_id, values in sorted(offsets.items()):
        rejected = sum(v > max_offset_ft for v in values)
        print(f"  {pattern_id:>8}  {percentiles(values)}  n={len(values):,}  rejected={rejected}")
    if unknown:
        print(f"  {unknown:,} pings on patterns with no fetched path")


if __name__ == "__main__":
    main()
