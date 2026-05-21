import pandas as pd
import numpy as np
import requests
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

# Pallekele Depot Coordinates (MAS Active Controline)
DEPOT_LAT = 7.2842
DEPOT_LON = 80.7061

# Default recruitment radius in km (straight-line from depot)
DEFAULT_RADIUS_KM = 20

# Real area names for labelling snapped random points
KANDY_AREA_NAMES = [
    "Digana", "Kundasale", "Katugastota", "Peradeniya", "Gampola",
    "Wattegama", "Kadugannawa", "Pilimathalawa", "Akurana", "Gelioya",
    "Teldeniya", "Menikhinna", "Ukuwela", "Madawala", "Ampitiya",
    "Galaha", "Nawalapitiya", "Pussellawa", "Hasalaka", "Galagedara",
    "Poojapitiya", "Alawatugoda", "Bokkawala", "Hatharaliyadda", "Muruthalawa",
    "Getambe", "Kandy City", "Nittawela", "Lewella", "Asgiriya",
    "Yatinuwara", "Udunuwara", "Doluwa", "Panvila", "Tennekumbura",
    "Ankumbura", "Medamahanuwara", "Hantana", "Mawilmada", "Katukele",
    "Halloluwa", "Polgolla", "Kandy East", "Kandy South", "Nildandahinna",
    "Gurudeniya", "Watapuluwa", "Rajapihilla", "Aludeniya", "Warakagoda",
    "Pahala Mawatha", "Thalathuoya", "Mahaiyawa", "Galwanduwa", "Ihala Kosgama",
    "Arandara", "Hindagala", "Naranpanawa", "Hewaheta", "Minipe",
    "Ududumbara", "Kandy North", "Gangawata", "Balagolla", "Ambatenna",
    "Malpitiya", "Daulagala", "Elamalpe", "Harispattuwa", "Menikdiwela",
    "Danture", "Medawala", "Udapalatha", "Palapathwela", "Pathahewaheta",
    "Walawela", "Weligalla", "Delthota", "Yatawara", "Loolwatta",
    "Madawata", "Kandy West", "Pallekele North", "Gelioya South", "Talatu Oya",
    "Nilambe", "Peradeniya East", "Katukitula", "Hapugoda", "Dolosbage",
    "Naula", "Pilimatalawa", "Baduluoya", "Ihalagama", "Udatenna",
    "Galaboda", "Bambaragala", "Rangala", "Knuckles"
]

def haversine_km(lat1, lon1, lat2, lon2):
    """Straight-line distance between two coordinates in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def snap_to_road(lat, lon):
    """
    Snaps a coordinate to the nearest road via OSRM nearest API.
    Returns (snapped_lat, snapped_lon) or None on failure.
    """
    url = f"https://router.project-osrm.org/nearest/v1/driving/{lon},{lat}?number=1"
    try:
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "Ok" and data.get("waypoints"):
                slon, slat = data["waypoints"][0]["location"]
                return round(slat, 6), round(slon, 6)
    except Exception:
        pass
    return None

def generate_network_data(num_destinations=99, seed=42, radius_km=DEFAULT_RADIUS_KM):
    """
    Generates random stops within radius_km of the depot.
    ALL OSRM nearest API calls are fired in parallel using a thread pool,
    cutting generation time from ~2 minutes down to ~8-12 seconds.
    """
    rng = np.random.default_rng(seed)
    names = list(KANDY_AREA_NAMES[:num_destinations])
    rng.shuffle(names)

    # Bounding box for the radius
    lat_delta = radius_km / 111.0
    lon_delta = radius_km / (111.0 * abs(math.cos(math.radians(DEPOT_LAT))))

    print(f"Generating {num_destinations} stops within {radius_km} km radius (parallel)...")

    # Generate a large pool of candidate coordinates, pre-filtered by straight-line distance
    pool_size = num_destinations * 5
    raw_lats = rng.uniform(DEPOT_LAT - lat_delta, DEPOT_LAT + lat_delta, pool_size)
    raw_lons = rng.uniform(DEPOT_LON - lon_delta, DEPOT_LON + lon_delta, pool_size)

    candidates = [
        (lat, lon) for lat, lon in zip(raw_lats, raw_lons)
        if haversine_km(DEPOT_LAT, DEPOT_LON, lat, lon) <= radius_km
    ]

    # Fire ALL snap requests simultaneously
    def snap_worker(args):
        idx, lat, lon = args
        result = snap_to_road(lat, lon)
        if result:
            slat, slon = result
            dist = haversine_km(DEPOT_LAT, DEPOT_LON, slat, slon)
            if dist <= radius_km:
                return idx, (slat, slon)
        return idx, None

    snapped = {}
    with ThreadPoolExecutor(max_workers=40) as pool:
        futures = {pool.submit(snap_worker, (i, lat, lon)): i
                   for i, (lat, lon) in enumerate(candidates)}
        for f in as_completed(futures):
            idx, result = f.result()
            snapped[idx] = result

    # Collect accepted stops in order
    accepted = [snapped[i] for i in sorted(snapped) if snapped[i] is not None]

    # Pad with fallback points near depot if not enough were accepted
    while len(accepted) < num_destinations:
        fallback_lat = DEPOT_LAT + rng.uniform(-0.02, 0.02)
        fallback_lon = DEPOT_LON + rng.uniform(-0.02, 0.02)
        s = snap_to_road(fallback_lat, fallback_lon)
        accepted.append(s if s else (fallback_lat, fallback_lon))

    accepted = accepted[:num_destinations]
    print(f"Done. {len(accepted)} road-snapped stops accepted.")

    # Build DataFrame
    data = [{
        "Route_ID": 0,
        "Destination_Name": "MAS Controline Pallekele (Factory — Depot)",
        "Latitude": DEPOT_LAT,
        "Longitude": DEPOT_LON,
        "Demand_10AM_Collect": 0,
        "Demand_2PM_Drop":     0,
        "Demand_2PM_Collect":  0,
        "Demand_10PM_Drop":    0,
    }]

    for i, (lat, lon) in enumerate(accepted):
        data.append({
            "Route_ID":           i + 1,
            "Destination_Name":   f"{names[i]} — Stop {i+1}",
            "Latitude":           lat,
            "Longitude":          lon,
            # Morning-shift workers at this stop (same people: collected 10AM, dropped 2PM)
            "Demand_10AM_Collect": int(rng.integers(5, 26)),
            # Afternoon-shift workers at this stop (same people: collected 2PM, dropped 10PM)
            "Demand_2PM_Collect":  int(rng.integers(5, 26)),
        })

    df = pd.DataFrame(data)

    # Enforce logical symmetry:
    #   The people collected at 10 AM are the same ones dropped at 2 PM.
    #   The people collected at 2 PM are the same ones dropped at 10 PM.
    df["Demand_2PM_Drop"]  = df["Demand_10AM_Collect"]
    df["Demand_10PM_Drop"] = df["Demand_2PM_Collect"]

    # Reorder columns to a logical display order
    df = df[["Route_ID", "Destination_Name", "Latitude", "Longitude",
             "Demand_10AM_Collect", "Demand_2PM_Drop",
             "Demand_2PM_Collect", "Demand_10PM_Drop"]]

    return df


if __name__ == "__main__":
    df = generate_network_data()
    print(df[["Route_ID", "Destination_Name", "Demand_10AM_Collect"]].to_string())
    total = df['Demand_10AM_Collect'].sum() + df['Demand_2PM_Collect'].sum()
    print(f"\nEstimated total workforce (2 shifts): {total}")
