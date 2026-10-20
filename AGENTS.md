# AGENTS.md

Guidance for AI coding agents working in this repository.

## Project overview

`aaata-bus` is a Python library and set of scripts for collecting and analyzing
real-time bus data from the Ann Arbor Area Transportation Authority (AAATA / TheRide).
It polls TheRide's public `ServiceData` API for bus locations and arrival predictions,
stores snapshots as JSON Lines, and provides visualization helpers. The longer-term goal
(see `tasks/modelling.md`, which is gitignored and may not exist) is probabilistic
arrival-time modelling built on the collected data.

## Layout

- `src/aaata_bus/` — the installable package (uv build backend, `src` layout, `py.typed`).
  - `definitions.py` — API URL templates, `DATA_PATH` (from `AAATA_DATA_PATH`, defaults
    to `./dat` at the repo root), and `ensure_dat()`, which asserts the data dir exists.
  - `schema.py` — Pydantic models. `Bus` and `BusPrediction` map TheRide's terse field
    names to readable ones via `Field(alias=...)`; keep the inline comments explaining
    each field. `RouteData` is one polling snapshot, the line format of the jsonl file.
    `Route` (with `RoutePattern`, `RoutePoint`, `RouteStop`, `Detour`, `RouteBulletin`)
    models `method=getroutebyid`: route metadata, each pattern's path as ordered lat/lon
    points (some carrying a stop), active detours and bulletins. `RoutePattern.pattern_id`
    matches `Bus.pattern_id`. `ServiceBulletin` models `method=getservicebulletins`, a
    fuller bulletin format than the `RouteBulletin` embedded in `getroutebyid`.
  - `api.py` — sync (`httpx.get`) and async (shared `httpx.AsyncClient`) fetchers
    sharing `_parse_*_response` helpers. `get_routes` fetches several routes with a
    courtesy wait, skipping (with a warning) any that fail.
  - `geometry.py` — linear referencing (numpy). `PatternGeometry` turns a `RoutePattern`
    into a polyline in local feet (detoured patterns use the detour path, minus stop
    spurs; see gotchas) with stops located by distance along it. `project()` gives
    `(dist_along_ft, offset_ft)` plus the snapped point; `near_ft` picks between passes
    where the path goes by a point more than once. `snap_sightings` projects one bus's
    time-ordered pings, each near the last (`SnappedPing.on_route` is false past
    `max_offset_ft`, default 150). `check_stop_distances` compares our distances with
    each prediction's `distance_to_stop_ft`.
  - `route_history.py` — saved route versions. `RouteVersion` is one raw `getroutebyid`
    payload plus `route_version` (`<route_id>-<sha256[:12]>` of the canonical JSON, so
    identical content gets the same id) and an aware-UTC `fetched_at`.
    `save_route_version` appends to `dat/route_versions.jsonl` only if the content differs
    from that route's latest saved version. `RouteHistory` implements
    `geometry.GeometrySource`. A bus has no route-version field (`pid` is the only link),
    so `version_at` takes the versions of the route that contain the pattern, then the
    latest fetched at or before the sighting, else the earliest.
    `load_route_history` adds in-memory live fetches for routes with no saved version.
  - `times.py` — `to_utc` and `observation_time` for the collector's naive timestamps.
  - `trips.py` — trip reconstruction. `reconstruct_trips` splits each vehicle's pings
    (timed by `requested_at`, as aware UTC) into `Trip`s: new trip on a pattern change,
    a gap over `max_gap` (10 min), or distance falling `reset_ft` (1000 ft) below the
    trip's furthest. Then consecutive pieces on one pattern are rejoined across gaps up to
    `merge_gap` (30 min) if they carry on forward and their trip ids don't disagree.
    Off-path pings are left out. Runs of repeated positions are kept (`is_repeat`) unless
    the next move is faster than `stale_speed_ftps` (80 ft/s), in which case they go into
    `stale_pings`. `dist_ft` is the isotonic fit of `raw_dist_ft`. `trip_id` is the
    majority vote of the nearest-stop prediction's `trip_id` (only stops on the pattern
    count). Each trip records its `route_version` and `geometry`, and a trip never spans
    two versions. `trip_pings_rows` gives the tidy `trip_pings` table.
  - Anything that snaps (`reconstruct_trips`, `check_stop_distances`, `snap_sightings`,
    `plot_route_paths`) takes `Geometries`: either a fixed `{pattern_id: PatternGeometry}`
    from `pattern_geometries`, or a `GeometrySource` such as `RouteHistory`.
  - `route.py` — stub `BusRoute` class, not yet used.
  - `visualization/` — `data.py` loads `RouteData` and traces per-bus `BusPath`s;
    `route_paths.py` renders them as a folium map, optionally drawing `Route` shapes
    (from `get_route`) and stops beneath the bus paths in each route's layer. Shapes
    carry a `route-shape` or `bus-ping` CSS class; the `_OverlayToggle` control hides
    each kind across all layers by toggling an `off-<name>` class on the map container.
    Detoured patterns also tag their runs `detour-path` / `detour-regular`, and the
    "Detours" box (shown only when a detour exists) picks which one is visible.
    With route shapes, bus pings are drawn twice, `ping-raw` (joined by lines) and
    `ping-snapped` (markers on the path, no lines; unsnappable pings hollow at their raw
    position), and a radio toggles a `snapped-pings` class on the container.
    `trip_plots.py` draws reconstructed trips as plotly time–distance panels, one per
    pattern. Draw stop lines as one None-separated trace; `add_hline` per stop took 40 s.
    `serve.py` has the localhost server the scripts' `--serve` uses.
  - `__init__.py` — re-exports the public API; update `__all__` when adding exports.
- `scripts/` — runnable entry points (`collect_data.py`, `plot_route_paths.py`,
  `check_linear_referencing.py`, `reconstruct_trips.py`), run
  with `uv run scripts/<name>.py`. The Docker entrypoint can run any script here by name.
  `plot_route_paths.py --serve [PORT]` serves the map on localhost (stdlib `http.server`)
  for viewing from a remote machine via port forwarding. It fetches each plotted route's shape
  via `get_routes`; `--no-route-shapes` skips that. `--pings raw|snapped` sets which ping
  view the map opens on. `check_linear_referencing.py` reports ping offsets and how our
  distances to stops compare with TheRide's, split into a per-bus position term and a
  per-stop geometry term. `reconstruct_trips.py` writes `dat/trip_pings.csv` and the
  `dat/trips.html` chart, and prints how many pings were dropped and trips matched to ids.
- `tests/` — pytest suite. `test_api.py` tests the API layer offline against recorded
  responses in `tests/fixtures/` (`route_22.json` has an active detour, so it exercises
  the single-object quirk for detours and bulletins). `test_api_live.py` hits the real TheRide API (needs
  network); it warns instead of failing when no buses are running, e.g. overnight.
- `dat/` — collected data (gitignored). Main file: `dat/route_prediction_data.jsonl`;
  route versions in `dat/route_versions.jsonl`.
- `Dockerfile`, `docker-compose.yml`, `docker-entrypoint.sh` — containerized collector.

## Workflow

Dependencies are managed with **uv** (Python 3.12+). Install with `uv sync` (includes
the `dev` group, which provides pyrefly).

After making changes, always run both checks and fix anything you introduced:

```bash
uv run pyrefly check   # type checking (config in pyrefly.toml)
uv run pytest          # tests
```

Add or update tests in `tests/` when changing behavior. Prefer offline tests: monkeypatch
`aaata_bus.api.httpx.get`, or pass an `httpx.AsyncClient` with an `httpx.MockTransport`,
returning payloads from `tests/fixtures/`. Live tests must tolerate an empty system
(no buses) by warning rather than failing.

## Conventions and gotchas

- **Data compatibility:** `RouteData` is persisted to jsonl and reloaded later. New
  fields must have defaults so older saved lines still validate (see
  `predictions_collected` and `requested_at`). Don't rename or remove fields without a
  migration plan.
- **API quirks:** TheRide returns a bare object instead of a one-element list for single
  results (handled by `_ensure_list`), `{"bus": null}` for no buses, and
  `{"bustime-response": {"error": [...]}}` with no `prd` key when a vehicle has no
  predictions. `getroutebyid` wraps collections (`{"pas": {"pa": [...]}}`), sends a bare
  `[]` for empty collections and missing scalars (e.g. `"nm": []`), omits `dtrs` when there
  are no detours, HTML-escapes some text (`P&amp;R`, unescaped via `RideText`), and returns
  a nameless, pattern-less stub for unknown route ids rather than an error. A detoured
  pattern's `pt` list is the full detour path buses take (`dtr: false`) followed by the
  full regular path it bypasses (`dtr: true`, `RoutePoint.is_bypassed`), each from start
  to end, so never join the runs when drawing. (Confirmed against route 22's known paths.) Detour dates
  use `MM/DD/YY H:MM AM` (`DetourTimestamp`). `getservicebulletins` returns a bare list
  of `{"sb": {...}}` (`[]` for none or an unknown route); filtering by `routeid` still
  includes system-wide bulletins (empty `srvc`). Its `mod` stamps carry seconds, which
  `RideTimestamp` also accepts. Timestamps come as
  `YYYYMMDD HH:MM` in America/Detroit time; `RideTimestamp` parses these and also ISO
  strings so serialized data round-trips.
- **Route geometry:** many stops are drawn as a short spur off the street to the curb
  and back (`A, STOP, A`, or nested `A, B, STOP, B', A'`, with return vertices often ~1 ft
  off). `PatternGeometry` cuts spurs of at most 200 ft containing a stop; left in, they
  add ~70 ft per stop, which TheRide's `distance_to_stop_ft` doesn't count. Longer
  out-and-backs to a stop (into a side street or lot) are real travel the agency does
  count, so they stay. With spurs removed, most stop-to-stop links match the agency to
  within a few feet. Some of route 22's detour-path links still come out 40–70 ft longer
  than theirs, probably because their internal shape differs from the drawn one.
  Separately, the agency's distances often put the bus a few hundred feet ahead of its
  `busesforroute` position (the per-bus term in the check script).
- **Route versions are only as fine as the check interval:** a change happens at some
  point between two saved fetches. Data from before routes were saved (before
  2026-10-20) falls back to the earliest version, i.e. a live fetch. Patterns change with
  detours (route 22's detour pattern is `501321`).
- Predictions for a bus run on into its next trip (usually on another pattern), so
  filter by `trip_id` before comparing them with the current pattern.
- **Timezones:** `requested_at` and `Bus.timestamp` are naive on the collector's clock
  (UTC in the container); prediction times are America/Detroit-aware. `trips.to_utc`
  normalizes both (`naive_tz` overrides the UTC assumption).
- **AVL quirks:** positions are already snapped to the street (median offset ~1 ft).
  Sometimes a position jumps a few hundred feet *backward* for one poll and then
  recovers (not a snapping error), which the isotonic fit absorbs. Repeated positions are
  usually real stops; only implausible onward speeds mark them stale.
- `dat/` is written by the container as root, so for local runs pass `--output`
  (and `--plot`) to somewhere writable.
- `Bus.timestamp` is set locally (`datetime.now()`) after the response arrives; the bus
  API provides no fix time.
- **Be polite to the API:** the collector uses a random courtesy wait between requests.
  Keep it; don't add tight polling loops.
- The collector checks each route's description every `--route-interval-minutes` (60),
  starting at startup, and loads the existing versions file first so a restart doesn't
  re-save. The payload has no volatile fields, but **the order of patterns varies between
  fetches** (route 66 flips two), so `payload_hash` hashes `canonical_payload`: patterns
  and detours sorted by id, bulletins by content, points left in order. Any real change
  (path, stops, detour, bulletin text) still makes a new version. `save_route_version`
  recomputes the latest saved version's hash instead of trusting the stored
  `content_hash`, so lines saved under the old raw-order hash still compare correctly. Scripts snap to saved
  versions and only fetch live for routes with none saved (`--no-fetch` disables that).
- The prediction polling interval must be a whole multiple of the general (bus location)
  interval. Location-only snapshots have empty `predictions` and
  `predictions_collected: false`.
- Scripts that write data must call `ensure_dat()` first.
- Type annotations are expected throughout; pyrefly checks unannotated defs too.
- `xyzservices` providers are attached dynamically, so pyrefly can't see attribute
  access like `providers.Esri.WorldGrayCanvas`; use `providers.query_name("Esri.WorldGrayCanvas")`.

## Keep this file updated

When you change something documented here (layout, commands, data format, conventions)
or add a notable module, script, or gotcha, update this file as part of the same change.
Keep `README.md` in sync for user-facing changes.
