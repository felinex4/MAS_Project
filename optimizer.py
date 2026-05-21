import numpy as np
import pandas as pd
import requests
import math
from functools import lru_cache

def get_osrm_distance_matrix(lats, lons):
    """Fetches real road distance matrix from OSRM public API (max 100 coords)."""
    coords_str = ";".join([f"{lon},{lat}" for lat, lon in zip(lats, lons)])
    url = f"https://router.project-osrm.org/table/v1/driving/{coords_str}?annotations=distance"
    response = requests.get(url, timeout=30)
    if response.status_code == 200:
        data = response.json()
        distances = np.array(data['distances']) / 1000.0  # metres → km
        return distances
    else:
        raise Exception(f"OSRM API error: {response.status_code}")

@lru_cache(maxsize=32)
def fetch_matrix_cached(lats, lons):
    return get_osrm_distance_matrix(lats, lons)

def run_optimization(df, shift_column,
                     vehicle_capacity=50,
                     max_oneway_km=30,        # ← one-way road limit per leg
                     num_vehicles=40,
                     fuel_cost_per_km=300):
    from ortools.constraint_solver import routing_enums_pb2, pywrapcp

    total_demand = df[shift_column].sum()
    if total_demand == 0:
        return {"status": "Failed", "message": "No demand for this shift."}

    lats = df['Latitude'].values
    lons  = df['Longitude'].values

    # ── 1. Fetch real road distance matrix ──────────────────────────────────
    try:
        full_matrix = fetch_matrix_cached(tuple(lats), tuple(lons))
    except Exception as e:
        return {"status": "Failed", "message": f"OSRM API error: {e}"}

    # ── 2. Pre-filter: drop stops where road distance depot→stop > one-way limit
    #    A stop 30 km away by road is reachable; the bus then picks up others
    #    along the way and returns. Total route budget = max_oneway_km * 3
    #    (generous: go out 30 km, serve a cluster, return ~30 km + ~30 km detour)
    active_indices        = [0]   # depot always included
    dropped_nodes         = []
    dropped_demand        = 0
    distance_dropped_count = 0   # dropped because too far from depot

    for i in range(1, len(df)):
        if df.iloc[i][shift_column] > 0:
            one_way = full_matrix[0][i]   # depot → this stop by road
            if one_way <= max_oneway_km:
                active_indices.append(i)
            else:
                dropped_nodes.append(i)
                dropped_demand += int(df.iloc[i][shift_column])
                distance_dropped_count += 1

    if len(active_indices) <= 1:
        return {
            "status": "Failed",
            "message": (f"No stops are within {max_oneway_km} km road distance "
                        f"of the factory. Increase Max One-Way Distance.")
        }

    # ── 3. Build reduced distance matrix for active nodes ────────────────────
    n = len(active_indices)
    dist_km  = np.array([[full_matrix[i][j] for j in active_indices]
                          for i in active_indices])
    # Total route budget: a bus should be able to serve a cluster and return.
    # We allow the full round-trip to be up to 3× the one-way limit.
    total_budget_km = max_oneway_km * 3
    dist_int = [[int(dist_km[i][j] * 1000) for j in range(n)] for i in range(n)]

    # ── 4. Demands ────────────────────────────────────────────────────────────
    demands = [int(df.iloc[idx][shift_column]) for idx in active_indices]
    demands[0] = 0

    # ── 5. Respect the user's fleet size strictly ─────────────────────────
    effective_fleet = num_vehicles          # never exceed what the user set
    capacities      = [vehicle_capacity] * effective_fleet

    # ── 6. OR-Tools model ─────────────────────────────────────────────────────
    manager = pywrapcp.RoutingIndexManager(n, effective_fleet, 0)
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index, to_index):
        return dist_int[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]

    transit_cb = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb)

    # Capacity constraint
    def demand_callback(from_index):
        return demands[manager.IndexToNode(from_index)]
    demand_cb = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(demand_cb, 0, capacities, True, 'Capacity')

    # Total route distance constraint
    routing.AddDimension(
        transit_cb, 0,
        int(total_budget_km * 1000),   # total route ≤ 3 × one-way limit
        True, 'Distance'
    )

    # ── 7. Make every non-depot stop optional (droppable) ────────────────────
    # This lets OR-Tools drop stops when the fleet can't serve everyone,
    # rather than exceeding the user-specified fleet size.
    drop_penalty = int(total_budget_km * 1000 * 20)  # large enough to avoid dropping unless necessary
    for node_idx in range(1, n):
        routing.AddDisjunction([manager.NodeToIndex(node_idx)], drop_penalty)

    # ── 7. Solve ──────────────────────────────────────────────────────────────
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC)
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
    params.time_limit.FromSeconds(4)

    solution = routing.SolveWithParameters(params)

    # Baseline: direct round-trip from depot to every active stop individually
    baseline_km = sum(
        dist_km[0][ri] + dist_km[ri][0]
        for ri in range(1, n) if demands[ri] > 0
    )

    if not solution:
        return {
            "status": "Failed",
            "message": ("OR-Tools could not find a solution within the time limit. "
                        "Try increasing Max One-Way Distance or Fleet Size.")
        }

    # ── 8. Extract routes ─────────────────────────────────────────────────────
    routes        = []
    buses_used    = 0
    total_dist    = 0
    visited_nodes = set()

    for vid in range(effective_fleet):
        idx   = routing.Start(vid)
        path  = []
        rdist = 0
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            path.append(active_indices[node])
            visited_nodes.add(node)
            prev = idx
            idx  = solution.Value(routing.NextVar(idx))
            rdist += routing.GetArcCostForVehicle(prev, idx, vid)
        path.append(active_indices[manager.IndexToNode(idx)])
        if len(path) > 2:
            routes.append(path)
            total_dist += rdist
            buses_used += 1

    # Detect stops OR-Tools dropped due to fleet / distance constraints
    fleet_dropped_count = 0
    for node_idx in range(1, n):
        if node_idx not in visited_nodes and demands[node_idx] > 0:
            dropped_nodes.append(active_indices[node_idx])
            dropped_demand += demands[node_idx]
            fleet_dropped_count += 1

    optimized_km   = total_dist / 1000.0
    distance_saved = baseline_km - optimized_km
    cost_saved     = distance_saved * fuel_cost_per_km
    emissions_kg   = distance_saved * 0.67

    return {
        "status":                  "Success",
        "baseline_distance_km":    round(baseline_km, 2),
        "optimized_distance_km":   round(optimized_km, 2),
        "distance_saved_km":       round(distance_saved, 2),
        "cost_saved_lkr":          round(cost_saved, 2),
        "emissions_saved_kg":      round(emissions_kg, 2),
        "routes":                  routes,
        "buses_used":              buses_used,
        "effective_fleet":         effective_fleet,
        "dropped_nodes":           dropped_nodes,
        "dropped_demand":          dropped_demand,
        "distance_dropped_count":  distance_dropped_count,
        "fleet_dropped_count":     fleet_dropped_count,
        "active_stops":            len(active_indices) - 1,
        "total_budget_km":         round(total_budget_km, 1),
    }
