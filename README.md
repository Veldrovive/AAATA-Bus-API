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

Run with default settings (routes 22, 63, 65 every 5 minutes):

```bash
uv run scripts/collect_data.py
```

Run with custom routes and polling interval:

```bash
uv run scripts/collect_data.py --routes 22 63 --interval-minutes 10
```

### Python API

You can use the `aaata_bus` package directly in Python:

```python
from aaata_bus import get_buses_for_route, get_predictions_for_bus

# Fetch buses currently running on a route
buses = get_buses_for_route("22")
if buses:
    for bus in buses:
        print(f"Bus {bus.vehicle_id}: {bus.direction} to {bus.destination} (lat: {bus.latitude}, lon: {bus.longitude})")

        # Fetch arrival predictions for this bus
        predictions = get_predictions_for_bus(bus.vehicle_id)
        if predictions:
            for pred in predictions:
                print(f"  Stop: {pred.stop_name} in {pred.estimate_time_minutes} min")
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
docker run --rm -v ./dat:/app/dat aaata-bus:latest collect_data.py --routes 4 --interval-minutes 2

# Run pytest inside the container
docker run --rm aaata-bus:latest pytest

# Open an interactive shell
docker run --rm -it aaata-bus:latest sh
```

## Configuration

- `AAATA_DATA_PATH`: Directory where output data files are written. Defaults to `./dat` relative to the repository root.
- `ensure_dat()`: Function exported by `aaata_bus` (and `aaata_bus.definitions`) that asserts the configured data directory exists before writing data.

## Testing

Run tests with pytest:

```bash
uv run pytest
```
