import html
from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

#: TheRide reports times in Ann Arbor local time.
_LOCAL_TZ = ZoneInfo("America/Detroit")


def _parse_ride_timestamp(value: Any) -> datetime:
    """Parse 'YYYYMMDD HH:MM[:SS]' stamps as America/Detroit datetimes.

    Also accepts ISO 8601 strings, which is how these models serialize, so that
    saved data can be read back in.
    """
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y%m%d %H:%M", "%Y%m%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=_LOCAL_TZ)
        except ValueError:
            pass
    return datetime.fromisoformat(text)


def _ensure_list(value: Any) -> list[Any]:
    """The API returns a bare object instead of a 1-element list when
    there is only a single result."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _parse_detour_timestamp(value: Any) -> datetime:
    """Parse detour 'MM/DD/YY H:MM AM' stamps as America/Detroit datetimes.

    Also accepts ISO 8601 strings so serialized data round-trips.
    """
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    try:
        return datetime.strptime(text, "%m/%d/%y %I:%M %p").replace(tzinfo=_LOCAL_TZ)
    except ValueError:
        return datetime.fromisoformat(text)


def _unwrap(key: str) -> Callable[[Any], list[Any]]:
    """Collections in getroutebyid come wrapped, e.g. {"pa": [...]}, but are a bare
    `[]` when empty, and the inner value may be a single object rather than a list."""

    def unwrap(value: Any) -> list[Any]:
        if isinstance(value, dict):
            return _ensure_list(value.get(key))
        return _ensure_list(value)

    return unwrap


def _empty_to_none(value: Any) -> Any:
    """getroutebyid returns `[]` in place of missing scalar values."""
    return None if value == [] else value


def _unescape(value: Any) -> Any:
    """Some text fields come HTML-escaped, e.g. "P&amp;R"."""
    return html.unescape(value) if isinstance(value, str) else value


RideTimestamp = Annotated[datetime, BeforeValidator(_parse_ride_timestamp)]
DetourTimestamp = Annotated[datetime, BeforeValidator(_parse_detour_timestamp)]
RideText = Annotated[str, BeforeValidator(_unescape)]


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
        buses = _ensure_list(payload["buses"]["bus"])
        for bus in buses:
            bus["timestamp"] = timestamp
        return cls.model_validate({"bus": buses})


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
    """Top-level payload: {"bustime-response": {"prd": [...]}}.

    When the vehicle has no predictions (e.g. it is out of service), the API
    returns {"bustime-response": {"error": [...]}} with no "prd" key instead.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    predictions: Annotated[list[BusPrediction], BeforeValidator(_ensure_list)] = Field(
        default_factory=list, alias="prd"
    )

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "PredictionsForBusResponse":
        return cls.model_validate(payload["bustime-response"])


class RouteStop(BaseModel):
    """A bus stop on a route pattern (method=getroutebyid)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    stop_id: str = Field(alias="id")  # matches BusPrediction.stop_id
    rtpi_feed_name: list[Any] = Field(default_factory=list, alias="rtpiFeedName")
    stop_name: RideText = Field(alias="st")


class RoutePoint(BaseModel):
    """One vertex of a pattern's path, optionally at a stop."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    latitude: float = Field(alias="lat")
    longitude: float = Field(alias="lon")
    # On a detoured pattern, true for the regular path the active detour bypasses;
    # the false points are the path buses actually take while it's in effect.
    is_bypassed: bool = Field(alias="dtr")
    stop: RouteStop | None = Field(default=None, alias="bs")  # set when a stop is here


class RoutePattern(BaseModel):
    """One variant of a route's path in one direction, as an ordered polyline."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    pattern_id: str = Field(alias="id")  # matches Bus.pattern_id
    direction: RideText = Field(alias="d")  # e.g. "TO BTC"
    direction_destination: RideText = Field(alias="dd")
    direction_rtpi_feed_name: list[Any] = Field(default_factory=list, alias="directionRtpiFeedName")
    points: Annotated[list[RoutePoint], BeforeValidator(_ensure_list)] = Field(alias="pt")

    @property
    def stops(self) -> list[RouteStop]:
        """Stops on this pattern, in travel order."""
        return [point.stop for point in self.points if point.stop is not None]


class DetourDirection(BaseModel):
    """A route direction affected by a detour."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    route_number: str = Field(alias="rt")
    direction: RideText = Field(alias="dir")  # matches RoutePattern.direction


class Detour(BaseModel):
    """A scheduled detour on a route."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    detour_id: str = Field(alias="id")  # GUID
    version: str = Field(alias="vrs")  # revision number of the detour definition
    description: RideText = Field(alias="desc")
    directions: Annotated[list[DetourDirection], BeforeValidator(_unwrap("rtdir"))] = Field(alias="rtdirs")
    starts_at: DetourTimestamp = Field(alias="sdate")
    ends_at: DetourTimestamp = Field(alias="edate")


class RouteBulletin(BaseModel):
    """A service bulletin as embedded in getroutebyid. A briefer format than
    ServiceBulletin from getservicebulletins."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: RideText = Field(alias="nm")
    affected_service: str = Field(alias="as")  # what the bulletin applies to, e.g. route "22"
    subject: RideText = Field(alias="sbj")
    detail: RideText = Field(alias="det")  # full notice text
    priority: str = Field(alias="pr")  # observed "10"; meaning undocumented
    feed: list[Any] = Field(default_factory=list, alias="sbFeed")
    route_number: str = Field(alias="rt")


class Route(BaseModel):
    """A route's description, paths, detours and bulletins (method=getroutebyid).

    Unknown route ids are not an error: the API returns a stub with no name
    and no patterns.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    route_id: str = Field(alias="id")
    rtpi_feed_name: list[Any] = Field(default_factory=list, alias="rtpiFeedName")
    short_name: str = Field(alias="sn")
    name: Annotated[RideText | None, BeforeValidator(_empty_to_none)] = Field(default=None, alias="nm")
    route_display: str = Field(alias="displayDesignator")  # route number as shown to riders
    mode: str = Field(alias="vmode")  # "1" = fixed-route bus
    color: str = Field(alias="c")  # hex colour, e.g. "#6666ff"
    # "dtrs" is absent when the route has no detours.
    detours: Annotated[list[Detour], BeforeValidator(_unwrap("dtr"))] = Field(default_factory=list, alias="dtrs")
    service_bulletins: Annotated[list[RouteBulletin], BeforeValidator(_unwrap("sb"))] = Field(
        default_factory=list, alias="sbs"
    )
    patterns: Annotated[list[RoutePattern], BeforeValidator(_unwrap("pa"))] = Field(
        default_factory=list, alias="pas"
    )

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "Route":
        """Parse the top-level payload: {"route": {...}}."""
        return cls.model_validate(payload["route"])

    def pattern(self, pattern_id: str) -> RoutePattern | None:
        """Look up a pattern by id, e.g. a Bus.pattern_id."""
        return next((p for p in self.patterns if p.pattern_id == pattern_id), None)


class BulletinService(BaseModel):
    """What a bulletin applies to. Unused parts are empty strings, e.g. a whole
    route has only `route_number` set."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    route_number: str = Field(alias="rt")
    route_direction: RideText = Field(alias="rtdir")
    stop_id: str = Field(alias="stpid")
    stop_name: RideText = Field(alias="stpnm")


class ServiceBulletin(BaseModel):
    """A rider-facing service notice (method=getservicebulletins)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: RideText = Field(alias="nm")
    subject: RideText = Field(alias="sbj")
    detail: str = Field(alias="dtl")  # full notice; may contain HTML markup such as links
    brief: RideText = Field(alias="brf")  # short summary, sometimes cut off mid-sentence
    priority: str = Field(alias="prty")  # e.g. "Low"
    cause: str = Field(alias="cse")  # e.g. "Construction", "Other cause"
    effect: str = Field(alias="efct")  # e.g. "Detour", "Other effect"
    # Empty for system-wide bulletins, which are included in every route's results.
    services: Annotated[list[BulletinService], BeforeValidator(_ensure_list)] = Field(alias="srvc")
    modified_at: RideTimestamp = Field(alias="mod")
    url: str = Field(alias="url")  # observed always ""

    @property
    def is_system_wide(self) -> bool:
        return not self.services

    @property
    def route_numbers(self) -> list[str]:
        """Distinct routes this bulletin names, in order."""
        return list(dict.fromkeys(s.route_number for s in self.services if s.route_number))


class ServiceBulletinsResponse(BaseModel):
    """Top-level payload: a bare list of {"sb": {...}}, `[]` when there are none."""

    bulletins: list[ServiceBulletin]

    @classmethod
    def from_api(cls, payload: Any) -> "ServiceBulletinsResponse":
        return cls.model_validate({"bulletins": [item["sb"] for item in _ensure_list(payload)]})


class RouteData(BaseModel):
    """One polling snapshot of a route, as written to the collected jsonl file."""

    route_id: str
    buses: list[Bus]
    predictions: dict[str, list[BusPrediction]]  # vehicle_id -> predictions
    # False for location-only snapshots, where `predictions` is empty because
    # predictions were not requested (not because none were returned).
    # Defaults to True so data saved before this flag existed still loads.
    predictions_collected: bool = True
    # When the bus request for this snapshot was made, so snapshots with no
    # buses are still timestamped. None for data saved before this field existed.
    requested_at: datetime | None = None
