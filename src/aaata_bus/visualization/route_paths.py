"""Map of the paths each route's buses have traced."""

from collections import defaultdict

import folium
from branca.element import MacroElement, Template
from xyzservices import providers

from aaata_bus.schema import Route, RoutePattern, RoutePoint
from aaata_bus.visualization.data import BusPath

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


class _OverlayToggle(MacroElement):
    """Map control with checkboxes that show or hide route shapes and bus pings.

    Works across all route layers (which stay individually toggleable) by
    toggling an `off-<name>` class on the map container while a box is unchecked;
    the CSS hides the matching shapes. With `has_detours`, a "Detours" box swaps
    detoured patterns between their detour path and their regular path.
    """

    def __init__(self, has_detours: bool) -> None:
        super().__init__()
        self.has_detours = has_detours

    _template = Template(
        """
        {% macro header(this, kwargs) %}
        <style>
            .off-route-shape .route-shape,
            .off-bus-ping .bus-ping,
            .off-detour .detour-path,
            .leaflet-container:not(.off-detour) .detour-regular { display: none; }
            .overlay-toggle label { display: block; }
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
) -> folium.Map:
    """Draw each bus path as a polyline, colored and layered by route.

    Args:
        paths: Paths from `trace_bus_paths`.
        show_points: Also mark each individual sighting along the line.
        routes: Route descriptions from `get_route`; each one's scheduled
            patterns are drawn beneath its buses' paths, in the same layer.
        show_stops: Mark each route's stops (only applies with `routes`).

    Returns:
        A folium map with one toggleable layer per route, plus a control that
        shows or hides all route shapes or all bus pings at once, and switches
        detoured patterns between their detour and regular paths.
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

    route_ids = sorted(by_route.keys() | shapes.keys(), key=_route_sort_key)
    for i, route_id in enumerate(route_ids):
        color = ROUTE_COLORS[i % len(ROUTE_COLORS)]
        layer = folium.FeatureGroup(name=f"Route {route_id}")
        if route_id in shapes:
            _add_route_shape(shapes[route_id], color, layer, show_stops)
        for path in by_route[route_id]:
            description = _describe(path)
            if len(path.sightings) > 1:
                folium.PolyLine(
                    path.coordinates,
                    color=color,
                    weight=3,
                    opacity=0.8,
                    className=BUS_PING_CLASS,
                    tooltip=description,
                ).add_to(layer)
            if show_points or len(path.sightings) == 1:
                for bus in path.sightings:
                    folium.CircleMarker(
                        (bus.latitude, bus.longitude),
                        radius=4,
                        color="white",
                        weight=2,
                        fill=True,
                        fill_color=color,
                        fill_opacity=1.0,
                        className=BUS_PING_CLASS,
                        tooltip=(
                            f"Route {path.route_id} &middot; bus {bus.vehicle_id}<br>"
                            f"{bus.timestamp:%b %d %H:%M} &middot; {bus.direction}"
                        ),
                    ).add_to(layer)
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
        has_detours=any(_is_detoured(p) for route in shapes.values() for p in route.patterns)
    ).add_to(fmap)
    return fmap
