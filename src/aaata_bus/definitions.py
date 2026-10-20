from pathlib import Path
import os

BUSSES_FOR_ROUTE_URL = (
    "https://www.theride.org/api/ServiceData?method=busesforroute&routeid={route_id}"
)

PREDICTIONS_FOR_BUS_URL = (
    "https://www.theride.org/api/ServiceData?method=predictionsforbus&vid={vehicle_id}"
)

ROUTE_BY_ID_URL = (
    "https://www.theride.org/api/ServiceData?method=getroutebyid&routeid={route_id}"
)

#: Without a routeid this returns every bulletin; append "&routeid=..." to filter.
SERVICE_BULLETINS_URL = "https://www.theride.org/api/ServiceData?method=getservicebulletins"

#: Sent with every request so TheRide can tell who is polling and get in touch.
USER_AGENT = "aaata-bus (research data collector; contact: adempst.dev@gmail.com)"
REQUEST_HEADERS = {"User-Agent": USER_AGENT}

DEFAULT_DATA_PATH = Path(__file__).parent.parent.parent / "dat"
DATA_PATH = Path(os.getenv("AAATA_DATA_PATH", DEFAULT_DATA_PATH))
def ensure_dat() -> Path:
    """Ensure that the data directory exists.

    Raises:
        AssertionError: If the data directory does not exist.

    Returns:
        Path: The data directory path.
    """
    assert DATA_PATH.exists(), (
        f"No data directory found at {DATA_PATH}. Please create one before running or set the AAATA_DATA_PATH environment variable."
    )
    return DATA_PATH