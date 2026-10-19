"""Map of the paths each route's buses have traced."""

from collections import defaultdict

import folium

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


def _route_sort_key(route_id: str) -> tuple[int, str]:
    return (int(route_id), route_id) if route_id.isdigit() else (10**9, route_id)


def _describe(path: BusPath) -> str:
    destinations = " / ".join(dict.fromkeys(bus.destination_sign for bus in path.sightings))
    return (
        f"<b>Route {path.route_id}</b> &middot; bus {path.vehicle_id}<br>"
        f"{path.start:%b %d %H:%M} – {path.end:%H:%M} ({len(path.sightings)} sightings)<br>"
        f"{destinations}"
    )


def plot_route_paths(
    paths: list[BusPath],
    show_points: bool = True,
) -> folium.Map:
    """Draw each bus path as a polyline, colored and layered by route.

    Args:
        paths: Paths from `trace_bus_paths`.
        show_points: Also mark each individual sighting along the line.

    Returns:
        A folium map with one toggleable layer per route.
    """
    fmap = folium.Map(location=ANN_ARBOR, zoom_start=13, tiles="OpenStreetMap")

    by_route: dict[str, list[BusPath]] = defaultdict(list)
    for path in paths:
        by_route[path.route_id].append(path)

    for i, route_id in enumerate(sorted(by_route, key=_route_sort_key)):
        color = ROUTE_COLORS[i % len(ROUTE_COLORS)]
        layer = folium.FeatureGroup(name=f"Route {route_id}")
        for path in by_route[route_id]:
            description = _describe(path)
            if len(path.sightings) > 1:
                folium.PolyLine(
                    path.coordinates,
                    color=color,
                    weight=3,
                    opacity=0.8,
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
                        tooltip=(
                            f"Route {path.route_id} &middot; bus {bus.vehicle_id}<br>"
                            f"{bus.timestamp:%b %d %H:%M} &middot; {bus.direction}"
                        ),
                    ).add_to(layer)
        layer.add_to(fmap)

    if paths:
        coords = [c for path in paths for c in path.coordinates]
        lats, lons = zip(*coords)
        fmap.fit_bounds([(min(lats), min(lons)), (max(lats), max(lons))], padding=(20, 20))
    folium.LayerControl(collapsed=False).add_to(fmap)
    return fmap
