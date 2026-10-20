"""Time–distance ("Marey") charts of reconstructed trips."""

from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from aaata_bus.trips import Trip, TripPing
from aaata_bus.visualization.route_paths import ROUTE_COLORS, _route_sort_key

#: Charts show Ann Arbor wall-clock time.
LOCAL_TZ = ZoneInfo("America/Detroit")

PANEL_HEIGHT_PX = 340
INK_MUTED = "#6b6b6b"
GRID = "#e6e6e6"


def _local(t: datetime) -> datetime:
    # Plotly draws aware datetimes in UTC, so hand it naive local times.
    return t.astimezone(LOCAL_TZ).replace(tzinfo=None)


def _hover(trip: Trip, ping: TripPing) -> list[str]:
    return [
        trip.trip_key,
        trip.trip_id or "–",
        f"{ping.raw_dist_ft:,.0f}",
        "yes" if ping.is_repeat else "no",
    ]


HOVER_TEMPLATE = (
    "%{x|%H:%M:%S} &middot; %{y:,.0f} ft<br>"
    "trip %{customdata[0]}<br>"
    "trip id %{customdata[1]} &middot; raw %{customdata[2]} ft &middot; repeat %{customdata[3]}"
    "<extra></extra>"
)


def plot_trips(trips: list[Trip]) -> go.Figure:
    """One panel per pattern (and route version, if a pattern has several):
    each trip's distance along the pattern over time.

    Lines are the isotonic (non-decreasing) fit, dots the raw projected pings it
    was fit to, and hollow circles the stale repeated pings that were dropped.
    Faint horizontal lines mark the pattern's stops. Colors follow the route, in
    the same order as the route map.

    Args:
        trips: From `reconstruct_trips`; each trip's geometry gives the
            pattern's length and stops.
    """
    by_pattern: dict[tuple[str, str | None], list[Trip]] = defaultdict(list)
    for trip in trips:
        by_pattern[(trip.pattern_id, trip.route_version)].append(trip)
    versions_per_pattern = Counter(pattern_id for pattern_id, _ in by_pattern)
    route_ids = sorted({trip.route_id for trip in trips}, key=_route_sort_key)
    colors = {route_id: ROUTE_COLORS[i % len(ROUTE_COLORS)] for i, route_id in enumerate(route_ids)}
    panels = sorted(
        by_pattern,
        key=lambda k: (_route_sort_key(by_pattern[k][0].route_id), k[0], by_pattern[k][0].start),
    )

    titles = []
    for key in panels:
        pattern_id, route_version = key
        first = by_pattern[key][0]
        direction = f" {first.geometry.direction}" if first.geometry else ""
        version = f" · version {route_version}" if versions_per_pattern[pattern_id] > 1 else ""
        titles.append(
            f"Route {first.route_id}{direction} · pattern {pattern_id}{version} · {len(by_pattern[key])} trips"
        )

    fig = make_subplots(
        rows=max(len(panels), 1), cols=1, shared_xaxes=True,
        vertical_spacing=min(0.25 / max(len(panels), 1), 0.06), subplot_titles=titles,
    )
    shown_legend: set[str] = set()

    def legend(name: str) -> dict:
        first = name not in shown_legend
        shown_legend.add(name)
        return {"name": name, "legendgroup": name, "showlegend": first}

    for row, key in enumerate(panels, start=1):
        pattern_trips = sorted(by_pattern[key], key=lambda t: t.start)
        color = colors[pattern_trips[0].route_id]

        # One trace per kind per panel; None breaks the line between trips.
        fit_x: list[datetime | None] = []
        fit_y: list[float | None] = []
        fit_data: list[list[str] | None] = []
        raw_x, raw_y, raw_data = [], [], []
        stale_x, stale_y, stale_data = [], [], []
        for trip in pattern_trips:
            for ping in trip.pings:
                fit_x.append(_local(ping.t))
                fit_y.append(ping.dist_ft)
                fit_data.append(_hover(trip, ping))
                raw_x.append(_local(ping.t))
                raw_y.append(ping.raw_dist_ft)
                raw_data.append(_hover(trip, ping))
            fit_x.append(None)
            fit_y.append(None)
            fit_data.append(None)
            for ping in trip.stale_pings:
                stale_x.append(_local(ping.t))
                stale_y.append(ping.raw_dist_ft)
                stale_data.append(_hover(trip, ping))

        geometry = pattern_trips[0].geometry
        if geometry is not None:
            # Added first so they draw beneath the trips. One trace of
            # None-separated segments, since add_hline per stop is very slow.
            start, end = min(raw_x + stale_x), max(raw_x + stale_x)
            stop_x: list[datetime | None] = []
            stop_y: list[float | None] = []
            for stop in geometry.stops:
                stop_x += [start, end, None]
                stop_y += [stop.dist_along_ft, stop.dist_along_ft, None]
            fig.add_trace(go.Scatter(
                x=stop_x, y=stop_y, mode="lines", line={"width": 1, "color": GRID},
                hoverinfo="skip", **legend("Stops"),
            ), row=row, col=1)
        fig.add_trace(go.Scatter(
            x=raw_x, y=raw_y, customdata=raw_data, mode="markers",
            marker={"size": 5, "color": color, "opacity": 0.45},
            hovertemplate=HOVER_TEMPLATE, **legend("Raw pings"),
        ), row=row, col=1)
        fig.add_trace(go.Scatter(
            x=fit_x, y=fit_y, customdata=fit_data, mode="lines",
            line={"width": 2, "color": color},
            hovertemplate=HOVER_TEMPLATE, **legend("Isotonic fit"),
        ), row=row, col=1)
        if stale_x:
            fig.add_trace(go.Scatter(
                x=stale_x, y=stale_y, customdata=stale_data, mode="markers",
                marker={"size": 9, "color": "white", "line": {"width": 2, "color": color}},
                hovertemplate=HOVER_TEMPLATE, **legend("Dropped stale pings"),
            ), row=row, col=1)

        geometry = pattern_trips[0].geometry
        if geometry is not None:
            fig.update_yaxes(range=[-0.02 * geometry.length_ft, 1.02 * geometry.length_ft], row=row, col=1)
        fig.update_yaxes(title_text="ft along pattern", row=row, col=1)

    fig.update_xaxes(title_text="Time (Ann Arbor)", row=max(len(panels), 1), col=1)
    fig.update_xaxes(showgrid=False, linecolor=INK_MUTED, tickfont={"color": INK_MUTED})
    fig.update_yaxes(showgrid=False, zeroline=False, tickformat=",", tickfont={"color": INK_MUTED})
    fig.update_layout(
        template="plotly_white",
        title="Reconstructed trips: distance along pattern over time",
        height=PANEL_HEIGHT_PX * max(len(panels), 1) + 160,
        hovermode="closest",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0, "x": 0, "yref": "container"},
        margin={"t": 110},
    )
    return fig
