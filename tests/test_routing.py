from dataclasses import replace

import numpy as np
import pytest
from shapely.ops import unary_union

from routeengine.costsurface import build_grid
from routeengine.router import RoutingError, find_routes, snap_to_passable


def obstacles(layers):
    return unary_union(list(layers["sssi"].geometry) + list(layers["ancient_woodland"].geometry))


def test_exclusions_are_impassable_and_multipliers_apply(project, layers):
    grid = build_grid(project, layers)
    assert grid.shape == (300, 400)
    r, c = grid.to_cell(501800, 101500)  # inside the SSSI
    assert np.isinf(grid.cost[r, c])
    r, c = grid.to_cell(502900, 102500)  # inside Flood Zone 3, away from crossings
    assert grid.cost[r, c] == pytest.approx(3.0)
    r, c = grid.to_cell(500150, 100150)  # open ground
    assert grid.cost[r, c] == pytest.approx(1.0)
    r, c = grid.to_cell(503000, 100500)  # on the railway band
    assert grid.cost[r, c] >= 500


def test_route_avoids_hard_constraints_and_reaches_both_ends(project, layers):
    grid = build_grid(project, layers)
    routes = find_routes(grid, project.start, project.end, 1, 4.0, 300, obstacles(layers))
    line = routes[0].line
    assert not line.intersects(obstacles(layers))
    assert line.distance(layers["buildings"].geometry.iloc[1]) > 0
    start_gap = np.hypot(line.coords[0][0] - project.start[0], line.coords[0][1] - project.start[1])
    assert start_gap < 120  # start sat inside a building footprint and was snapped out
    assert np.hypot(line.coords[-1][0] - project.end[0], line.coords[-1][1] - project.end[1]) < 20


def test_alternatives_are_distinct_and_ranked_by_cost(project, layers):
    grid = build_grid(project, layers)
    routes = find_routes(grid, project.start, project.end, 3, 4.0, 300, obstacles(layers))
    assert len(routes) >= 2
    assert [r.rank for r in routes] == list(range(1, len(routes) + 1))
    assert routes[0].cost <= routes[1].cost
    assert routes[0].line.hausdorff_distance(routes[1].line) > 150


def test_snap_moves_end_points_out_of_exclusions(project, layers):
    grid = build_grid(project, layers)
    cell, moved = snap_to_passable(grid, (500300, 101500))
    assert moved > 0
    assert np.isfinite(grid.cost[cell])


def test_fully_blocked_corridor_raises(project, layers):
    from shapely.geometry import box
    import geopandas as gpd

    wall = gpd.GeoDataFrame({"cls": ["sssi"], "label": [""]}, geometry=[box(501900, 100000, 502000, 103000)], crs="EPSG:27700")
    blocked = dict(layers, sssi=wall)
    grid = build_grid(project, blocked)
    with pytest.raises(RoutingError):
        find_routes(grid, project.start, project.end, 1, 4.0, 300)


def test_start_outside_study_area_raises(project, layers):
    grid = build_grid(project, layers)
    with pytest.raises(RoutingError):
        snap_to_passable(grid, (400000, 100000))


def test_cost_penalties_are_configurable(project, layers):
    cheap = replace(project, crossing_rules=tuple(replace(r, penalties={"*": 0}) for r in project.crossing_rules))
    grid = build_grid(cheap, layers)
    r, c = grid.to_cell(503000, 100500)
    assert grid.cost[r, c] == pytest.approx(1.0)
