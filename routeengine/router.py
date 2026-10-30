"""Least-cost corridor search and ranked alternatives.

Uses Dijkstra over the cost grid with 8-connectivity and geometric (true
distance) step costs (scikit-image MCP_Geometric). Alternatives come from the
penalty method: after each route, cells near it get more expensive and the
search runs again, so each alternative is genuinely different, not a copy that
wiggles a few metres. All candidates are then scored on the original surface.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from shapely.geometry import LineString, Point
from scipy.ndimage import distance_transform_edt
from skimage.graph import MCP_Geometric

from .costsurface import Grid


class RoutingError(Exception):
    pass


@dataclass
class Route:
    rank: int
    line: LineString  # BNG, from start to end
    cost: float  # on the original cost surface
    raw_cells: list[tuple[int, int]]

    @property
    def length_m(self) -> float:
        return self.line.length


def snap_to_passable(grid: Grid, xy: tuple[float, float], max_snap_m: float = 250) -> tuple[tuple[int, int], float]:
    """Nearest passable cell to a point (end points often sit just inside a building footprint)."""
    row, col = grid.to_cell(*xy)
    h, w = grid.shape
    if not (0 <= row < h and 0 <= col < w):
        raise RoutingError(f"point {xy} is outside the study area")
    if np.isfinite(grid.cost[row, col]):
        return (row, col), 0.0
    radius = int(max_snap_m / grid.resolution)
    r0, r1 = max(0, row - radius), min(h, row + radius + 1)
    c0, c1 = max(0, col - radius), min(w, col + radius + 1)
    window = np.isfinite(grid.cost[r0:r1, c0:c1])
    if not window.any():
        raise RoutingError(f"no passable ground within {max_snap_m:.0f} m of {xy}")
    rr, cc = np.nonzero(window)
    d2 = (rr + r0 - row) ** 2 + (cc + c0 - col) ** 2
    i = int(np.argmin(d2))
    return (int(rr[i] + r0), int(cc[i] + c0)), float(np.sqrt(d2[i]) * grid.resolution)


def _path(cost: np.ndarray, start: tuple[int, int], end: tuple[int, int]) -> tuple[list[tuple[int, int]], float]:
    mcp = MCP_Geometric(cost, fully_connected=True)
    cumulative, _ = mcp.find_costs([start], [end])
    total = cumulative[end]
    if not np.isfinite(total):
        raise RoutingError("no route exists: the end points are separated by excluded areas")
    return mcp.traceback(end), float(total)


def path_cost(cost: np.ndarray, cells: list[tuple[int, int]], resolution: float) -> float:
    """Cost of a cell path on a given surface, matching MCP_Geometric's accounting."""
    total = 0.0
    for (r0, c0), (r1, c1) in zip(cells, cells[1:]):
        step = np.hypot(r1 - r0, c1 - c0)
        total += step * (cost[r0, c0] + cost[r1, c1]) / 2
    return total * resolution


def cells_to_line(grid: Grid, cells: list[tuple[int, int]], start_xy, end_xy) -> LineString:
    pts = [grid.to_xy(r, c) for r, c in cells]
    pts[0], pts[-1] = start_xy, end_xy
    return LineString(pts)


def simplify_safely(line: LineString, obstacles, tolerance: float) -> LineString:
    """Straighten the staircase cell path without letting it cut into excluded areas."""
    for tol in (tolerance, tolerance / 2, tolerance / 4):
        candidate = line.simplify(tol, preserve_topology=True)
        if obstacles is None or not candidate.intersects(obstacles):
            return candidate
    return line


def find_routes(grid: Grid, start_xy, end_xy, alternatives: int, penalty: float,
                buffer_m: float, obstacles=None) -> list[Route]:
    start, _ = snap_to_passable(grid, start_xy)
    end, _ = snap_to_passable(grid, end_xy)
    working = grid.cost.copy()
    radius_cells = buffer_m / grid.resolution
    routes: list[Route] = []
    for _ in range(max(1, alternatives)):
        try:
            cells, _ = _path(working, start, end)
        except RoutingError:
            if routes:
                break
            raise
        line = simplify_safely(cells_to_line(grid, cells, grid.to_xy(*start), grid.to_xy(*end)),
                               obstacles, grid.resolution * 1.5)
        if all(line.hausdorff_distance(r.line) > buffer_m / 2 for r in routes):
            routes.append(Route(0, line, path_cost(grid.cost, cells, grid.resolution), cells))
        # Make the corridor around this route more expensive for the next search.
        on_path = np.ones(grid.shape, dtype=bool)
        on_path[tuple(np.array(cells).T)] = False
        near = distance_transform_edt(on_path) <= radius_cells
        working = np.where(near & np.isfinite(working), working * penalty, working)
    routes.sort(key=lambda r: r.cost)
    for i, r in enumerate(routes, start=1):
        r.rank = i
    return routes


def endpoint_offsets(grid: Grid, start_xy, end_xy) -> tuple[float, float]:
    return snap_to_passable(grid, start_xy)[1], snap_to_passable(grid, end_xy)[1]


def as_point(xy) -> Point:
    return Point(xy)
