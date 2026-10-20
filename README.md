# aaata-bus

Python library and data collection utilities for Ann Arbor Area Transportation Authority (AAATA / TheRide) bus tracking and route predictions.

## Requirements

- Python 3.12+
- uv (package manager)
- Docker and Docker Compose (optional, for containerized execution)

## Setup

Clone the repository and install dependencies using uv:

```bash
uv sync
```

## Usage

### Data Collection Script

The repository includes a script to periodically poll and record real-time bus locations and predictions to a JSON Lines file (`dat/route_prediction_data.jsonl`).

Scripts that write data verify the target directory exists by calling `ensure_dat()`. When running locally, ensure the target folder exists (defaults to `./dat`, or the directory specified by `AAATA_DATA_PATH`):

```bash
mkdir -p dat
```

Run with default settings (routes 22, 63, 65; bus locations every 30 seconds, predictions every 5 minutes):

```bash
uv run scripts/collect_data.py
```

The collector also checks each route's description (pattern paths, stops, detours, bulletins) at startup and then hourly, and appends it to `dat/route_versions.jsonl` only when it changed since the last saved version. Each line has the raw `getroutebyid` response, its fetch time, and a `route_version` id derived from its content. The analysis scripts snap each ping to the version that was in effect when it was seen. The match uses the bus's route and pattern id, then time: the latest version fetched at or before the ping, or, for pings older than every saved version, the earliest. Routes with no saved version are fetched live (pass `--no-fetch` to skip them). Change the interval with `--route-interval-minutes`.

Run with custom routes and polling intervals. The prediction interval must be a whole multiple of the general interval; snapshots taken between prediction loops have empty `predictions` and `predictions_collected: false`:

```bash
uv run scripts/collect_data.py --routes 22 63 --general-interval-minutes 1 --prediction-interval-minutes 10
```

### Visualizations

Visualization helpers live in `aaata_bus.visualization`. To map the path each route takes, as traced by its buses' collected positions:

```bash
uv run scripts/plot_route_paths.py                    # writes dat/route_paths.html
uv run scripts/plot_route_paths.py --routes 22 --no-points --output route22.html
```

On a remote machine, add `--serve` to host the map at `http://localhost:8000/route_paths.html` (or `--serve PORT`). VS Code Remote forwards the port automatically; over plain SSH, connect with `ssh -L 8000:localhost:8000 <host>` and open that URL locally.

Each bus's sightings are joined into a polyline, colored by route, with one toggleable layer per route. A bus's line is broken when consecutive sightings are more than `--max-gap-minutes` apart (default 15).

Beneath the bus lines, each route's scheduled path is drawn as a wide, faint line (with any active detour's path dashed) with its stops as small white circles; hover a stop for its name and ID. Each route is drawn as its latest saved version, and is fetched live if it has none. Pass `--no-stops` to hide the stops, or `--no-route-shapes` to skip the fetch entirely (e.g. offline).

A second control on the map, next to the route layers, has "Route paths & stops" and "Bus pings" checkboxes. They show or hide that kind of overlay on every route at once, so you can look at just the scheduled routes or just the bus sightings. When a route has an active detour, a "Detours" checkbox also appears. Checked, detoured routes show their detour path (dashed); unchecked, they show their regular path instead. Stops that are only on the hidden path are hidden with it.

When route shapes are drawn, the "Bus pings" checkbox also has two views. "Raw" shows each sighting where it was reported, joined in time order. "Snapped" moves each sighting onto its pattern's path, drawn as markers only, because lines between snapped points would just retrace the path. Hover a snapped ping to see how far along its pattern it is and how far off the path it was. Pings more than `--max-offset-ft` (default 150) from their path, or on a pattern with no fetched shape, are drawn as hollow circles at their raw position. `--pings snapped` opens the map in the snapped view.

### Linear referencing and trips

`aaata_bus.geometry` locates positions as distance along a route pattern instead of by latitude and longitude:

```python
from aaata_bus import get_buses_for_route, get_route, pattern_geometries

route = get_route("22")
geometries = pattern_geometries([route])  # keyed by pattern_id, matching Bus.pattern_id
for bus in get_buses_for_route("22") or []:
    geometry = geometries[bus.pattern_id]
    where = geometry.project(bus.latitude, bus.longitude)
    print(f"Bus {bus.vehicle_id}: {where.dist_along_ft:,.0f} of {geometry.length_ft:,.0f} ft "
          f"({where.offset_ft:.0f} ft off the path)")

for stop in geometries[route.patterns[0].pattern_id].stops:
    print(f"{stop.stop_name}: {stop.dist_along_ft:,.0f} ft along")
```

To split the collected pings into trips (one vehicle's run along one pattern), smooth each trip's distances so they never go backwards, and join them to TheRide's trip ids from the predictions:

```bash
uv run scripts/reconstruct_trips.py            # writes dat/trip_pings.csv and dat/trips.html
uv run scripts/reconstruct_trips.py --routes 22 --serve
```

`trip_pings.csv` has one row per ping: `trip_key`, UTC time `t`, `dist_ft` (smoothed) and `raw_dist_ft`, `offset_ft`, `is_repeat` (same position as the previous poll), the vehicle, operator, run and block, the pattern and the `route_version` it was snapped to, and the joined `trip_id`, `scheduled_trip_id`, `service_date` and `trip_start_seconds`. `trips.html` is a time–distance chart with one panel per pattern. Each trip's smoothed curve is a line, raw pings are dots, dropped stale pings are hollow circles, and stops are faint horizontal lines. Hover for details. In Python, use `reconstruct_trips(snapshots, RouteHistory.load())` (or `load_route_history(route_ids)` to fill in missing routes live, or `pattern_geometries(routes)` for fixed shapes).

To check the linear referencing against TheRide's own `distance_to_stop_ft` in the collected predictions:

```bash
uv run scripts/check_linear_referencing.py
```

### Python API

You can use the `aaata_bus` package directly in Python:

```python
from aaata_bus import get_buses_for_route, get_predictions_for_bus, get_route, get_service_bulletins

# Fetch buses currently running on a route
buses = get_buses_for_route("22")
if buses:
    for bus in buses:
        print(f"Bus {bus.vehicle_id}: {bus.direction} {bus.direction_destination} (lat: {bus.latitude}, lon: {bus.longitude})")

        # Fetch arrival predictions for this bus
        predictions = get_predictions_for_bus(bus.vehicle_id)
        if predictions:
            for pred in predictions:
                print(f"  Stop: {pred.stop_name} in {pred.countdown} min")

# Fetch a route's description: name, colour, the path of each pattern with its
# stops, plus any active detours and service bulletins
route = get_route("22")
print(route.name, route.color)
for pattern in route.patterns:
    print(f"Pattern {pattern.pattern_id} {pattern.direction}: {len(pattern.points)} points, {len(pattern.stops)} stops")
for detour in route.detours:
    print(f"Detour: {detour.description} ({detour.starts_at:%b %d} - {detour.ends_at:%b %d})")

# A bus's pattern_id identifies the path it is currently following
if buses:
    current = route.pattern(buses[0].pattern_id)

# Rider-facing service bulletins for a route (plus system-wide ones), or omit
# the route id to get every bulletin
for bulletin in get_service_bulletins("22"):
    scope = "all routes" if bulletin.is_system_wide else ", ".join(bulletin.route_numbers)
    print(f"[{bulletin.effect}] {bulletin.subject} ({scope})")
```

### Docker

#### Using Docker Compose

Start the data collector service in the background:

```bash
docker compose up -d
```

View logs:

```bash
docker compose logs -f
```

Stop the service:

```bash
docker compose down
```

Data collected inside the container is stored in `/app/dat`, which is mounted to `./dat` on the host.

#### Running Custom Commands via Docker

The Docker image includes an entrypoint that can run any script from the `scripts/` directory or run ad-hoc commands:

```bash
# Build the image
docker compose build

# Run a script with arguments
docker run --rm -v ./dat:/app/dat aaata-bus:latest collect_data.py --routes 4 --prediction-interval-minutes 2

# Run pytest inside the container
docker run --rm aaata-bus:latest pytest

# Open an interactive shell
docker run --rm -it aaata-bus:latest sh
```

## Configuration

- `AAATA_DATA_PATH`: Directory where output data files are written. Defaults to `./dat` relative to the repository root.
- `ensure_dat()`: Function exported by `aaata_bus` (and `aaata_bus.definitions`) that asserts the configured data directory exists before writing data.

## Testing

Type check with pyrefly (installed by `uv sync` as a dev dependency) and run tests with pytest:

```bash
uv run pyrefly check
uv run pytest
```

`tests/test_api.py` runs offline against recorded responses in `tests/fixtures/`. `tests/test_api_live.py` calls the real TheRide API; when no buses are running (e.g. overnight) it emits a warning instead of failing.
