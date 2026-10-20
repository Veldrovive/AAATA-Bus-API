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
    sharing `_parse_*_response` helpers.
  - `route.py` — stub `BusRoute` class, not yet used.
  - `visualization/` — `data.py` loads `RouteData` and traces per-bus `BusPath`s;
    `route_paths.py` renders them as a folium map, optionally drawing `Route` shapes
    (from `get_route`) and stops beneath the bus paths in each route's layer. Shapes
    carry a `route-shape` or `bus-ping` CSS class; the `_OverlayToggle` control hides
    each kind across all layers by toggling an `off-<name>` class on the map container.
    Detoured patterns also tag their runs `detour-path` / `detour-regular`, and the
    "Detours" box (shown only when a detour exists) picks which one is visible.
  - `__init__.py` — re-exports the public API; update `__all__` when adding exports.
- `scripts/` — runnable entry points (`collect_data.py`, `plot_route_paths.py`), run
  with `uv run scripts/<name>.py`. The Docker entrypoint can run any script here by name.
  `plot_route_paths.py --serve [PORT]` serves the map on localhost (stdlib `http.server`)
  for viewing from a remote machine via port forwarding. It fetches each plotted route's shape
  via `get_route` (courtesy wait between requests); `--no-route-shapes` skips that.
- `tests/` — pytest suite. `test_api.py` tests the API layer offline against recorded
  responses in `tests/fixtures/` (`route_22.json` has an active detour, so it exercises
  the single-object quirk for detours and bulletins). `test_api_live.py` hits the real TheRide API (needs
  network); it warns instead of failing when no buses are running, e.g. overnight.
- `dat/` — collected data (gitignored). Main file: `dat/route_prediction_data.jsonl`.
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
- `Bus.timestamp` is set locally (`datetime.now()`) after the response arrives; the bus
  API provides no fix time.
- **Be polite to the API:** the collector uses a random courtesy wait between requests.
  Keep it; don't add tight polling loops.
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
