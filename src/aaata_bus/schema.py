from datetime import date, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

#: TheRide reports times in Ann Arbor local time.
_LOCAL_TZ = ZoneInfo("America/Detroit")


def _parse_ride_timestamp(value: Any) -> datetime:
    """Parse 'YYYYMMDD HH:MM' stamps as America/Detroit datetimes.

    Also accepts ISO 8601 strings, which is how these models serialize, so that
    saved data can be read back in.
    """
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    try:
        return datetime.strptime(text, "%Y%m%d %H:%M").replace(tzinfo=_LOCAL_TZ)
    except ValueError:
        return datetime.fromisoformat(text)


def _ensure_list(value: Any) -> list[Any]:
    """The API returns a bare object instead of a 1-element list when
    there is only a single result."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


RideTimestamp = Annotated[datetime, BeforeValidator(_parse_ride_timestamp)]


class Bus(BaseModel):
    """One vehicle currently active on a route (method=busesforroute)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    vehicle_id: str = Field(alias="id")  # fleet number, e.g. "561"
    consist: list[Any] = Field(alias="consist")  # rail consist; always empty for buses
    cars: list[Any] = Field(alias="cars")  # rail cars; always empty for buses
    rtpi_feed_name: list[Any] = Field(alias="rtpiFeedName")  # realtime passenger info feed(s)
    mode: str = Field(alias="m")  # "1" = fixed-route bus
    route_number: str = Field(alias="rt")
    route_rtpi_feed_name: list[Any] = Field(alias="rtRtpiFeedName")
    route_display: str = Field(alias="rtdd")  # route number as shown to riders
    direction: str = Field(alias="d")  # e.g. "East Bound"
    direction_destination: str = Field(alias="dd")  # e.g. "TO BTC"
    direction_code: str = Field(alias="dn")  # compass code, e.g. "E", "WSW"
    latitude: float = Field(alias="lat")
    longitude: float = Field(alias="lon")
    pattern_id: str = Field(alias="pid")  # scheduled route pattern currently in effect
    pattern_destination: str = Field(alias="pd")
    pattern_destination_rtpi_feed_name: list[Any] = Field(alias="pdRtpiFeedName")
    run_number: str = Field(alias="run")  # driver run
    destination_sign: str = Field(alias="fs")  # headsign text shown on the bus
    operator_id: str = Field(alias="op")  # driver badge number
    dispatch_id: str = Field(alias="dip")  # internal dispatch record id (undocumented by the agency)
    block_id: str = Field(alias="bid")  # vehicle block number
    work_id_1: str = Field(alias="wid1")  # first piece of the driver's work ("N/A" if unsplit)
    work_id_2: str = Field(alias="wid2")  # second piece of the driver's work
    is_hidden: bool = Field(alias="hidden")
    timestamp: RideTimestamp


class BusesForRouteResponse(BaseModel):
    """Top-level payload: {"buses": {"bus": [...]}}."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    buses: Annotated[list[Bus], BeforeValidator(_ensure_list)] = Field(alias="bus")

    @classmethod
    def from_api(cls, payload: dict[str, Any], timestamp: RideTimestamp) -> "BusesForRouteResponse":
        payload_buses = payload["buses"]
        if payload_buses["bus"] is None:
            return cls(buses=[])
            
        for bus in payload_buses["bus"]:
            bus["timestamp"] = timestamp
        return cls.model_validate(payload["buses"])


class BusPrediction(BaseModel):
    """One predicted arrival of a vehicle at a stop (method=predictionsforbus)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    prediction_generated_at: RideTimestamp = Field(alias="tmstmp")
    prediction_type: str = Field(alias="typ")  # "A" = arrival, "D" = departure
    stop_name: str = Field(alias="stpnm")
    stop_id: str = Field(alias="stpid")
    vehicle_id: str = Field(alias="vid")
    distance_to_stop_ft: int = Field(alias="dstp")  # feet from the bus to the stop
    route_number: str = Field(alias="rt")
    route_display: str = Field(alias="rtdd")
    route_direction: str = Field(alias="rtdir")
    destination: str = Field(alias="des")  # headsign
    predicted_arrival_at: RideTimestamp = Field(alias="prdtm")
    schedule_block_id: str = Field(alias="tablockid")  # block id in the schedule tables
    scheduled_trip_id: str = Field(alias="tatripid")
    original_scheduled_trip_number: str = Field(alias="origtatripno")
    is_delayed: bool = Field(alias="dly")
    dynamic_code: int = Field(alias="dyn")  # internal trip-state code
    countdown: str = Field(alias="prdctdn")  # "DUE" or minutes remaining, e.g. "2"
    fare_zone: str = Field(alias="zone")
    run_id: str = Field(alias="rid")  # driver run; matches Bus.run_number
    trip_id: str = Field(alias="tripid")  # real-time trip id
    trip_dynamic_code: int = Field(alias="tripdyn")
    scheduled_time: RideTimestamp = Field(alias="schdtm")
    geo_id: str = Field(alias="geoid")  # internal stop-location id
    stop_sequence: int = Field(alias="seq")  # stop's order within the trip
    passenger_load: str = Field(alias="psgld")  # "L"/"M"/"H"; empty when unknown
    trip_start_seconds: int = Field(alias="stst")  # trip start, seconds after midnight
    service_date: date = Field(alias="stsd")
    is_flag_stop: bool = Field(alias="flagstop")  # served only on request


class PredictionsForBusResponse(BaseModel):
    """Top-level payload: {"bustime-response": {"prd": [...]}}."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    predictions: Annotated[list[BusPrediction], BeforeValidator(_ensure_list)] = Field(alias="prd")

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "PredictionsForBusResponse":
        return cls.model_validate(payload["bustime-response"])


class RouteData(BaseModel):
    """One polling snapshot of a route, as written to the collected jsonl file."""

    route_id: str
    buses: list[Bus]
    predictions: dict[str, list[BusPrediction]]  # vehicle_id -> predictions
    # False for location-only snapshots, where `predictions` is empty because
    # predictions were not requested (not because none were returned).
    # Defaults to True so data saved before this flag existed still loads.
    predictions_collected: bool = True
