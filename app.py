import streamlit as st
import pydeck as pdk
import pandas as pd
import requests
from data_generator import generate_network_data, DEPOT_LAT, DEPOT_LON
from optimizer import run_optimization

st.set_page_config(page_title="MAS Logistics Optimizer V4", layout="wide", page_icon="🚌")

st.title("🚌 MAS Active Controline: Advanced Bus Routing")
st.markdown("Optimize passenger pickups and drop-offs with real road routing, fleet capacity bounds, and strict time/distance constraints.")

# --- Sidebar Controls ---
st.sidebar.header("⚙️ Configuration Settings")
shift_choice = st.sidebar.selectbox("⏱️ Select Shift to Optimize", [
    "10:00 AM (Collect)", 
    "2:00 PM (Drop)", 
    "2:00 PM (Collect)", 
    "10:00 PM (Drop)"
])

shift_col_map = {
    "10:00 AM (Collect)": "Demand_10AM_Collect",
    "2:00 PM (Drop)": "Demand_2PM_Drop",
    "2:00 PM (Collect)": "Demand_2PM_Collect",
    "10:00 PM (Drop)": "Demand_10PM_Drop"
}
selected_shift_col = shift_col_map[shift_choice]

st.sidebar.subheader("Fleet Parameters")
fleet_size = st.sidebar.slider("🚌 Available Fleet Size (Buses)", min_value=10, max_value=100, value=40, step=1, help="The total number of buses available in the depot for this shift.")
vehicle_capacity = st.sidebar.slider("💺 Bus Capacity (Passengers)", min_value=20, max_value=80, value=50, step=5)
max_distance_km = st.sidebar.slider("⏱️ Max Route Distance (KM)", min_value=20, max_value=150, value=60, step=5, help="Limits the maximum distance a single bus can travel to ensure workers get home quickly.")
fuel_cost = st.sidebar.slider("⛽ Operating Cost per KM (LKR)", min_value=100, max_value=600, value=300, step=10)


st.sidebar.divider()
if st.sidebar.button("🔄 Generate New Kandy Dataset", use_container_width=True, type="primary"):
    if 'network_data' in st.session_state:
        del st.session_state['network_data']
    if 'opt_results' in st.session_state:
        del st.session_state['opt_results']
    st.experimental_rerun()

# --- Initialize Data ---
if 'network_data' not in st.session_state:
    with st.spinner("Generating network data (100 Kandy locations)..."):
        st.session_state['network_data'] = generate_network_data(num_destinations=99)

df = st.session_state['network_data']

# Cache the optimization so it doesn't rerun if we just switch map tabs
opt_cache_key = f"{selected_shift_col}_{vehicle_capacity}_{fuel_cost}_{max_distance_km}_{fleet_size}"
if 'opt_results' not in st.session_state or st.session_state.get('opt_cache_key') != opt_cache_key:
    with st.spinner("Fetching OSRM Road Distances & Running OR-Tools Optimizer..."):
        st.session_state['opt_results'] = run_optimization(
            df, 
            selected_shift_col, 
            vehicle_capacity=vehicle_capacity, 
            max_distance_km=max_distance_km,
            num_vehicles=fleet_size,
            fuel_cost_per_km=fuel_cost
        )
        st.session_state['opt_cache_key'] = opt_cache_key

opt_results = st.session_state['opt_results']
total_employees = df["Demand_10AM_Collect"].sum() + df["Demand_2PM_Collect"].sum()

# --- Main Layout ---
tab_dashboard, tab_dataset = st.tabs(["🗺️ Optimization Dashboard", "📋 Raw Dataset Manifest"])

with tab_dataset:
    st.subheader("📋 Complete Logistics Passenger Manifest")
    st.info(f"**Workforce Estimation:** ~{total_employees} employees across 2 shifts strictly within the **Kandy District**. Random coordinates and normal passenger distributions simulate daily flux.")
    display_df = df.drop(columns=["Latitude", "Longitude"])
    st.dataframe(display_df, use_container_width=True, height=600)

with tab_dashboard:
    st.subheader("🧠 Optimization Technique")
    st.info("""
    **Methodology:** Capacitated Vehicle Routing Problem (CVRP) with Distance Constraints.
    - **Engine:** Google OR-Tools Routing API.
    - **Heuristic:** *Path Cheapest Arc* (Initial Solution) refined via *Guided Local Search*.
    - **Cost Matrix:** True road geometries and distances generated via the Open Source Routing Machine (OSRM) API, discarding inaccurate linear paths.
    - **Constraints:** Guarantees no bus exceeds physical capacity, respects the available daily fleet size, and enforces a strict maximum route time/distance to prevent excessively long commutes.
    """)

    st.divider()

    # Metric Cards
    if opt_results["status"] == "Success":
        st.subheader(f"📈 Performance Comparison Summary: {shift_choice}")
        baseline_cost = opt_results['baseline_distance_km'] * fuel_cost
        optimized_cost = opt_results['optimized_distance_km'] * fuel_cost
        savings_percent = 0 if baseline_cost == 0 else (opt_results['cost_saved_lkr'] / baseline_cost) * 100
        buses_used = opt_results['buses_used']

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Unoptimized Baseline Distance", f"{opt_results['baseline_distance_km']:,.0f} KM", "Direct P2P Routing", delta_color="off")
        col2.metric("Optimized Fleet Distance", f"{opt_results['optimized_distance_km']:,.0f} KM", f"-{opt_results['distance_saved_km']:,.0f} KM saved")
        col3.metric("Financial Savings per Shift", f"Rs. {opt_results['cost_saved_lkr']:,.2f}", f"{savings_percent:.1f}% Cheaper")
        col4.metric("Active Buses Deployed", f"{buses_used} Buses", f"out of {fleet_size} available", delta_color="off")
        
        if opt_results.get('dropped_demand', 0) > 0:
            st.warning(f"⚠️ **Resource Bottleneck!** Due to strict constraints (Max Distance: {max_distance_km} KM or Fleet Size: {fleet_size} buses), the optimizer mathematically could not service **{len(opt_results['dropped_nodes'])} locations** (affecting {opt_results['dropped_demand']} passengers). They have been automatically dropped from this route plan. Increase limits to resolve.")
    else:
        st.error(f"**Optimization Failed:** {opt_results['message']}")

    st.divider()

    # Map Visualization
    st.subheader("🗺️ 3D Interactive Network Visualization (Real Road Tracing)")
    view_mode = st.radio("Select Map View:", ["Map View A (Unoptimized / Baseline)", "Map View B (Optimized / Routing)"], horizontal=True)

    @st.cache_data
    def get_osrm_route(lats, lons):
        coords_str = ";".join([f"{lon},{lat}" for lat, lon in zip(lats, lons)])
        url = f"http://router.project-osrm.org/route/v1/driving/{coords_str}?geometries=geojson&overview=full"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            if 'routes' in data and len(data['routes']) > 0:
                coords = data['routes'][0]['geometry']['coordinates']
                # OSRM returns [lon, lat] naturally, which is exactly what PyDeck wants!
                return coords
        return []

    # 1. Prepare scatter points for Destinations
    dest_data = []
    for idx, row in df.iterrows():
        if idx == 0: continue
        demand = row[selected_shift_col]
        if demand > 0:
            dest_data.append({
                "name": row['Destination_Name'],
                "demand": f"Passengers: {demand}",
                "coordinates": [row['Longitude'], row['Latitude']]
            })
            
    scatter_layer = pdk.Layer(
        "ScatterplotLayer",
        data=dest_data,
        get_position="coordinates",
        get_color=[30, 144, 255, 200], # Dodger blue
        get_radius=400, # Radius in meters
        pickable=True,
        auto_highlight=True
    )

    # 2. Prepare scatter point for Depot
    depot_layer = pdk.Layer(
        "ScatterplotLayer",
        data=[{"name": "MAS Controline Pallekele (Depot)", "demand": "Origin", "coordinates": [DEPOT_LON, DEPOT_LAT]}],
        get_position="coordinates",
        get_color=[255, 140, 0, 255], # Dark Orange
        get_radius=800,
        pickable=True
    )

    layers = [scatter_layer, depot_layer]

    if view_mode == "Map View A (Unoptimized / Baseline)":
        baseline_lines = []
        for idx, row in df.iterrows():
            if idx == 0: continue
            if row[selected_shift_col] > 0:
                baseline_lines.append({
                    "path": [[DEPOT_LON, DEPOT_LAT], [row['Longitude'], row['Latitude']]],
                    "color": [255, 0, 0, 150],
                    "name": f"Direct line to {row['Destination_Name']}",
                    "demand": ""
                })
        line_layer = pdk.Layer(
            "PathLayer",
            data=baseline_lines,
            get_path="path",
            get_color="color",
            width_scale=20,
            width_min_pixels=2,
            get_width=2,
            pickable=True,
            auto_highlight=True
        )
        layers.append(line_layer)

    elif view_mode == "Map View B (Optimized / Routing)":
        if opt_results["status"] == "Success":
            routes = opt_results["routes"]
            # Bright neon colors for dark theme map
            colors = [
                [46, 204, 113, 220], [52, 152, 219, 220], [155, 89, 182, 220], 
                [231, 76, 60, 220], [241, 196, 15, 220], [26, 188, 156, 220],
                [211, 84, 0, 220], [189, 195, 199, 220]
            ]
            route_data = []
            
            for i, route_path in enumerate(routes):
                color = colors[i % len(colors)]
                route_lats = []
                route_lons = []
                for node_index in route_path:
                    lat = df.iloc[node_index]['Latitude']
                    lon = df.iloc[node_index]['Longitude']
                    route_lats.append(lat)
                    route_lons.append(lon)
                
                road_path = get_osrm_route(route_lats, route_lons)
                
                if road_path:
                    route_data.append({
                        "path": road_path,
                        "color": color,
                        "name": f"Optimized Bus Route {i+1}",
                        "demand": ""
                    })
                else:
                    route_data.append({
                        "path": [[lon, lat] for lat, lon in zip(route_lats, route_lons)],
                        "color": color,
                        "name": f"Bus Route {i+1} (Fallback)",
                        "demand": ""
                    })
                    
            path_layer = pdk.Layer(
                "PathLayer",
                data=route_data,
                get_path="path",
                get_color="color",
                width_scale=20,
                width_min_pixels=3,
                get_width=3,
                pickable=True,
                auto_highlight=True
            )
            layers.append(path_layer)

    view_state = pdk.ViewState(
        latitude=DEPOT_LAT,
        longitude=DEPOT_LON,
        zoom=10.5,
        pitch=45, # 3D tilting!
        bearing=15
    )

    r = pdk.Deck(
        layers=layers,
        initial_view_state=view_state,
        map_style="mapbox://styles/mapbox/dark-v10",
        tooltip={"text": "{name}\n{demand}"}
    )

    st.pydeck_chart(r, use_container_width=True)
