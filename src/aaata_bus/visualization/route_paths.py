"""Map of the paths each route's buses have traced."""

from collections import defaultdict
from typing import Literal

import folium
from branca.element import MacroElement, Template
from xyzservices import providers

from aaata_bus.geometry import DEFAULT_MAX_OFFSET_FT, Geometries, pattern_geometries, snap_sightings
from aaata_bus.schema import Bus, Route, RoutePattern, RoutePoint
from aaata_bus.visualization.data import BusPath

PingView = Literal["raw", "snapped"]

#: Categorical hues, assigned to routes in this fixed order. Only the first three
#: stay distinguishable for colorblind viewers when lines cross, so past that
#: rely on the layer toggles and tooltips to tell routes apart.
ROUTE_COLORS = [
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#6250d6",  # violet
    "#e34948",  # red
]

ANN_ARBOR = (42.2808, -83.7430)

#: CSS classes on the drawn shapes, so the overlay toggle can hide each kind at once.
ROUTE_SHAPE_CLASS = "route-shape"
BUS_PING_CLASS = "bus-ping"
#: On detoured patterns, the detour path and the regular path it replaces. Only
#: one is shown at a time, picked by the overlay toggle's "Detours" checkbox.
DETOUR_PATH_CLASS = "detour-path"
DETOUR_REGULAR_CLASS = "detour-regular"
#: Bus pings are drawn twice: as raw sightings joined by lines, and snapped onto
#: their pattern's path (no lines, since they'd only retrace the path). The
#: overlay toggle's "Bus pings" radio shows one set at a time.
PING_RAW_CLASS = "ping-raw"
PING_SNAPPED_CLASS = "ping-snapped"


class _OverlayToggle(MacroElement):
    """Map control with checkboxes that show or hide route shapes and bus pings.

    Works across all route layers (which stay individually toggleable) by
    toggling an `off-<name>` class on the map container while a box is unchecked;
    the CSS hides the matching shapes. With `has_detours`, a "Detours" box swaps
    detoured patterns between their detour path and their regular path. With
    `has_snapped`, a raw/snapped radio picks which version of the bus pings is
    shown, starting at `ping_view`, via a `snapped-pings` class on the container.
    """

    def __init__(self, has_detours: bool, has_snapped: bool = False, ping_view: PingView = "raw") -> None:
        super().__init__()
        self.has_detours = has_detours
        self.has_snapped = has_snapped
        self.ping_view = ping_view

    _template = Template(
        """
        {% macro header(this, kwargs) %}
        <style>
            .off-route-shape .route-shape,
            .off-bus-ping .bus-ping,
            .off-detour .detour-path,
            .leaflet-container:not(.off-detour) .detour-regular,
            .snapped-pings .ping-raw,
            .leaflet-container:not(.snapped-pings) .ping-snapped { display: none; }
            .overlay-toggle label { display: block; }
            .overlay-toggle .ping-view { margin-left: 1.2em; }
        </style>
        {% endmacro %}
        {% macro script(this, kwargs) %}
        (function () {
            var map = {{ this._parent.get_name() }};
            var control = L.control({position: "topright"});
            control.onAdd = function () {
                var div = L.DomUtil.create(
                    "div", "leaflet-control-layers leaflet-control-layers-expanded overlay-toggle");
                L.DomEvent.disableClickPropagation(div);
                var items = [["route-shape", "Route paths & stops"], ["bus-ping", "Bus pings"]];
                {% if this.has_detours %}
                items.push(["detour", "Detours (uncheck for regular paths)"]);
                {% endif %}
                items.forEach(function (item) {
                    var label = L.DomUtil.create("label", "", div);
                    var box = L.DomUtil.create("input", "", label);
                    box.type = "checkbox";
                    box.checked = true;
                    label.appendChild(document.createTextNode(" " + item[1]));
                    L.DomEvent.on(box, "change", function () {
                        map.getContainer().classList.toggle("off-" + item[0], !box.checked);
                    });
                });
                {% if this.has_snapped %}
                var container = map.getContainer();
                container.classList.toggle("snapped-pings", {{ (this.ping_view == "snapped") | tojson }});
                [["raw", "raw, joined in time order"], ["snapped", "snapped to route path"]].forEach(
                    function (view) {
                        var label = L.DomUtil.create("label", "ping-view", div);
                        var radio = L.DomUtil.create("input", "", label);
                        radio.type = "radio";
                        radio.name = "{{ this.get_name() }}-ping-view";
                        radio.checked = view[0] === {{ this.ping_view | tojson }};
                        label.appendChild(document.createTextNode(" " + view[1]));
                        L.DomEvent.on(radio, "change", function () {
                            container.classList.toggle("snapped-pings", view[0] === "snapped");
                        });
                    });
                {% endif %}
                return div;
            };
            control.addTo(map);
        })();
        {% endmacro %}
        """
    )


def _route_sort_key(route_id: str) -> tuple[int, str]:
    return (int(route_id), route_id) if route_id.isdigit() else (10**9, route_id)


def _describe(path: BusPath) -> str:
    destinations = " / ".join(dict.fromkeys(bus.destination_sign for bus in path.sightings))
    return (
        f"<b>Route {path.route_id}</b> &middot; bus {path.vehicle_id}<br>"
        f"{path.start:%b %d %H:%M} – {path.end:%H:%M} ({len(path.sightings)} sightings)<br>"
        f"{destinations}"
    )


def _ping_tooltip(path: BusPath, bus: Bus) -> str:
    return (
        f"Route {path.route_id} &middot; bus {bus.vehicle_id}<br>"
        f"{bus.timestamp:%b %d %H:%M} &middot; {bus.direction}"
    )


def _add_ping(
    layer: folium.FeatureGroup,
    location: tuple[float, float],
    color: str,
    class_name: str,
    tooltip: str,
    hollow: bool = False,
) -> None:
    folium.CircleMarker(
        location,
        radius=4,
        color=color if hollow else "white",
        weight=2,
        fill=True,
        fill_color="white" if hollow else color,
        fill_opacity=1.0,
        className=class_name,
        tooltip=tooltip,
    ).add_to(layer)


def _add_snapped_pings(
    path: BusPath,
    geometries: Geometries,
    color: str,
    layer: folium.FeatureGroup,
    max_offset_ft: float,
) -> None:
    """Draw a path's pings snapped onto their patterns; pings that don't snap
    are drawn hollow where they were seen."""
    class_name = f"{BUS_PING_CLASS} {PING_SNAPPED_CLASS}"
    for ping in snap_sightings(path.sightings, geometries, max_offset_ft):
        tooltip = _ping_tooltip(path, ping.bus)
        projection = ping.projection
        if projection is None:
            tooltip += f"<br><i>not snapped: no path for pattern {ping.bus.pattern_id}</i>"
        else:
            tooltip += (
                f"<br>{projection.dist_along_ft:,.0f} ft along pattern {ping.bus.pattern_id}"
                f" &middot; {projection.offset_ft:,.0f} ft off it"
            )
        if projection is not None and ping.on_route:
            location = (projection.latitude, projection.longitude)
        else:
            location = (ping.bus.latitude, ping.bus.longitude)
            if projection is not None:
                tooltip += " <i>(too far to snap)</i>"
        _add_ping(layer, location, color, class_name, tooltip, hollow=not ping.on_route)


def _detour_runs(points: list[RoutePoint]) -> list[tuple[bool, list[tuple[float, float]]]]:
    """Split a pattern's path into contiguous (is_bypassed, coordinates) runs.

    A detoured pattern lists the full detour path and then the full regular path
    it bypasses, each running start to end, so runs are drawn separately rather
    than joined.
    """
    runs: list[tuple[bool, list[tuple[float, float]]]] = []
    for point in points:
        coord = (point.latitude, point.longitude)
        if runs and runs[-1][0] == point.is_bypassed:
            runs[-1][1].append(coord)
        else:
            runs.append((point.is_bypassed, [coord]))
    return runs


def _detour_class(is_bypassed: bool, pattern_detoured: bool) -> str:
    """Extra CSS class for a point on a pattern, so the detour toggle can swap paths."""
    if not pattern_detoured:
        return ""
    return DETOUR_REGULAR_CLASS if is_bypassed else DETOUR_PATH_CLASS


def _is_detoured(pattern: RoutePattern) -> bool:
    return any(point.is_bypassed for point in pattern.points)


def _add_route_shape(route: Route, color: str, layer: folium.FeatureGroup, show_stops: bool) -> None:
    """Draw a route's scheduled patterns (detour paths dashed) and its stops."""
    title = f"Route {route.route_id}" + (f" &middot; {route.name}" if route.name else "")
    for pattern in route.patterns:
        tooltip = f"<b>{title}</b><br>{pattern.direction} (pattern {pattern.pattern_id})"
        detoured = _is_detoured(pattern)
        for is_bypassed, coords in _detour_runs(pattern.points):
            if len(coords) < 2:
                continue
            is_detour = detoured and not is_bypassed
            # Wide and faint so the bus traces drawn on top stay readable.
            folium.PolyLine(
                coords,
                color=color,
                weight=7,
                opacity=0.3,
                dash_array="8 8" if is_detour else None,
                className=f"{ROUTE_SHAPE_CLASS} {_detour_class(is_bypassed, detoured)}".strip(),
                tooltip=tooltip + (" &middot; <i>detour</i>" if is_detour else ""),
            ).add_to(layer)

    if not show_stops:
        return
    # Stops shared by several patterns are drawn once, listing every direction.
    # A stop takes a detour class only if every path it's on would be hidden with it.
    stops: dict[str, tuple[RoutePoint, list[RoutePattern], set[str]]] = {}
    for pattern in route.patterns:
        detoured = _is_detoured(pattern)
        for point in pattern.points:
            if point.stop is not None:
                entry = stops.setdefault(point.stop.stop_id, (point, [], set()))
                entry[1].append(pattern)
                entry[2].add(_detour_class(point.is_bypassed, detoured))
    for stop_id, (point, patterns, classes) in stops.items():
        assert point.stop is not None
        detour_class = classes.pop() if len(classes) == 1 else ""
        directions = " / ".join(dict.fromkeys(p.direction for p in patterns))
        folium.CircleMarker(
            (point.latitude, point.longitude),
            radius=3,
            color=color,
            weight=2,
            fill=True,
            fill_color="white",
            fill_opacity=1.0,
            className=f"{ROUTE_SHAPE_CLASS} {detour_class}".strip(),
            tooltip=f"<b>{point.stop.stop_name}</b><br>Stop {stop_id} &middot; {title}<br>{directions}",
        ).add_to(layer)


def plot_route_paths(
    paths: list[BusPath],
    show_points: bool = True,
    routes: list[Route] | None = None,
    show_stops: bool = True,
    ping_view: PingView = "raw",
    max_offset_ft: float = DEFAULT_MAX_OFFSET_FT,
    geometries: Geometries | None = None,
) -> folium.Map:
    """Draw each bus path as a polyline, colored and layered by route.

    Args:
        paths: Paths from `trace_bus_paths`.
        show_points: Also mark each individual sighting along the line.
        routes: Route descriptions from `get_route`; each one's scheduled
            patterns are drawn beneath its buses' paths, in the same layer.
        show_stops: Mark each route's stops (only applies with `routes`).
        ping_view: Which bus pings the map opens showing: "raw" sightings
            joined by lines, or "snapped" onto their pattern's path (only
            applies with `routes`; the map's toggle switches between them).
            Pings further than `max_offset_ft` from their path, or on a pattern
            not in `routes`, are drawn hollow at their raw position when snapped.
        max_offset_ft: How far from its path a ping can be and still snap.
        geometries: What to snap pings onto, e.g. a `RouteHistory` so each is
            snapped to the route version in effect when it was seen. Defaults
            to the patterns of `routes`.

    Returns:
        A folium map with one toggleable layer per route, plus a control that
        shows or hides all route shapes or all bus pings at once, switches
        detoured patterns between their detour and regular paths, and switches
        bus pings between raw and snapped.
    """
    # Esri needs no API key (unlike CARTO) and no Referer header (unlike OSM), so
    # tiles load when the HTML is opened straight from disk.
    # Providers are attached dynamically, so look this one up by name to keep it typed.
    basemap = providers.query_name("Esri.WorldGrayCanvas")
    fmap = folium.Map(
        location=ANN_ARBOR,
        zoom_start=13,
        tiles=basemap.build_url(),
        attr=basemap.html_attribution,
        max_zoom=basemap.max_zoom,
    )

    by_route: dict[str, list[BusPath]] = defaultdict(list)
    for path in paths:
        by_route[path.route_id].append(path)

    shapes = {route.route_id: route for route in routes or []}
    if geometries is None and shapes:
        geometries = pattern_geometries(shapes.values())

    route_ids = sorted(by_route.keys() | shapes.keys(), key=_route_sort_key)
    for i, route_id in enumerate(route_ids):
        color = ROUTE_COLORS[i % len(ROUTE_COLORS)]
        layer = folium.FeatureGroup(name=f"Route {route_id}")
        if route_id in shapes:
            _add_route_shape(shapes[route_id], color, layer, show_stops)
        for path in by_route[route_id]:
            raw_class = f"{BUS_PING_CLASS} {PING_RAW_CLASS}" if geometries is not None else BUS_PING_CLASS
            if len(path.sightings) > 1:
                folium.PolyLine(
                    path.coordinates,
                    color=color,
                    weight=3,
                    opacity=0.8,
                    className=raw_class,
                    tooltip=_describe(path),
                ).add_to(layer)
            if show_points or len(path.sightings) == 1:
                for bus in path.sightings:
                    _add_ping(layer, (bus.latitude, bus.longitude), color, raw_class, _ping_tooltip(path, bus))
            if geometries is not None:
                _add_snapped_pings(path, geometries, color, layer, max_offset_ft)
        layer.add_to(fmap)

    coords = [c for path in paths for c in path.coordinates]
    coords += [
        (point.latitude, point.longitude)
        for route in shapes.values()
        for pattern in route.patterns
        for point in pattern.points
    ]
    if coords:
        lats, lons = zip(*coords)
        fmap.fit_bounds([(min(lats), min(lons)), (max(lats), max(lons))], padding=(20, 20))
    folium.LayerControl(collapsed=False).add_to(fmap)
    _OverlayToggle(
        has_detours=any(_is_detoured(p) for route in shapes.values() for p in route.patterns),
        has_snapped=geometries is not None and bool(paths),
        ping_view=ping_view,
    ).add_to(fmap)
    return fmap
