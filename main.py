from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import pandas as pd
from data_generator import generate_network_data, DEFAULT_RADIUS_KM
from optimizer import run_optimization

app = FastAPI()

# Cache: keyed by radius so changing radius forces a fresh generation
_cache = {}

class OptimizeRequest(BaseModel):
    shift_col: str
    vehicle_capacity: int
    max_oneway_km: int
    fleet_size: int
    fuel_cost: int
    data: list

@app.get("/api/generate")
def generate_data(radius_km: int = Query(default=DEFAULT_RADIUS_KM), fresh: bool = False):
    global _cache
    cache_key = radius_km
    if cache_key not in _cache or fresh:
        print(f"Generating dataset for radius={radius_km} km...")
        df = generate_network_data(99, radius_km=radius_km)
        _cache[cache_key] = df.to_dict(orient="records")
        print("Done. Dataset cached.")
    return _cache[cache_key]

@app.post("/api/optimize")
def optimize(req: OptimizeRequest):
    df = pd.DataFrame(req.data)
    result = run_optimization(
        df,
        req.shift_col,
        vehicle_capacity=req.vehicle_capacity,
        max_oneway_km=req.max_oneway_km,
        num_vehicles=req.fleet_size,
        fuel_cost_per_km=req.fuel_cost
    )
    return result

# Serve static frontend
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
