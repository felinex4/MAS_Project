const DEPOT_LAT = 7.2842;
const DEPOT_LON = 80.7061;

let currentData = [];
let deckgl;
let sidebarOpen = true;
let _currentLayers = []; // keeps the last rendered route/dot layers

// DOM refs
const shiftSelect  = document.getElementById('shiftSelect');
const fleetSlider  = document.getElementById('fleetSlider');
const capSlider    = document.getElementById('capSlider');
const distSlider   = document.getElementById('distSlider');
const fuelSlider   = document.getElementById('fuelSlider');
const radiusSlider = document.getElementById('radiusSlider');

const fleetVal  = document.getElementById('fleetVal');
const capVal    = document.getElementById('capVal');
const distVal   = document.getElementById('distVal');
const fuelVal   = document.getElementById('fuelVal');
const radiusVal = document.getElementById('radiusVal');

const generateBtn  = document.getElementById('generateBtn');
const sidebarEl    = document.getElementById('sidebar');
const sidebarClose = document.getElementById('sidebarClose');
const sidebarToggle= document.getElementById('sidebarToggle');
const mainPage     = document.getElementById('mainPage');

const mBaseline    = document.getElementById('m-baseline');
const mOptimized   = document.getElementById('m-optimized');
const mSavings     = document.getElementById('m-savings');
const mBuses       = document.getElementById('m-buses');
const mBusesSub    = document.getElementById('m-buses-sub');
const mCo2         = document.getElementById('m-co2');
const statusAlert  = document.getElementById('statusAlert');
const shiftBadge   = document.getElementById('shiftBadge');
const mapMode      = document.getElementById('mapMode');
const tooltip      = document.getElementById('tooltip');
const workforceEst = document.getElementById('workforceEst');

const tableBody    = document.getElementById('tableBody');
const tableWrapper = document.getElementById('tableWrapper');
const loadingTable = document.getElementById('loadingTable');
const stepper      = document.getElementById('loadingStepper');
const steps        = [1,2,3,4].map(n => document.getElementById(`step-${n}`));

// ── Stepper helpers ──
function showStepper() {
    stepper.classList.remove('hidden');
    steps.forEach(s => s.className = 'stepper-step');
}
function setStep(n) {
    steps.forEach((s, i) => {
        if (i < n - 1)  s.className = 'stepper-step done';
        else if (i === n - 1) s.className = 'stepper-step active';
        else            s.className = 'stepper-step';
    });
    // Do NOT scroll — user should stay at their current scroll position
}
function hideStepper() {
    stepper.classList.add('hidden');
    steps.forEach(s => s.className = 'stepper-step');
}

// ── Sidebar toggle ──
function setSidebar(open) {
    sidebarOpen = open;
    sidebarEl.classList.toggle('sidebar-hidden', !open);
    mainPage.classList.toggle('sidebar-collapsed', !open);
}
sidebarClose.onclick  = () => setSidebar(false);
sidebarToggle.onclick = () => setSidebar(!sidebarOpen);

// ── Slider labels ──
fleetSlider.oninput = () => fleetVal.textContent = fleetSlider.value;
capSlider.oninput   = () => capVal.textContent   = capSlider.value;
distSlider.oninput  = () => distVal.textContent  = distSlider.value;
fuelSlider.oninput  = () => fuelVal.textContent  = fuelSlider.value;

// ── Radius: show circle while dragging, regenerate on release ──
let showRadiusCircle = false;
radiusSlider.oninput = () => {
    radiusVal.textContent = radiusSlider.value;
    showRadiusCircle = true;
    drawRadiusCircle(parseInt(radiusSlider.value));
};
radiusSlider.onchange = () => {
    showRadiusCircle = false;
    clearRadiusCircle();
    // Regenerate data inside new radius
    generateData(true, parseInt(radiusSlider.value));
};

// ── Trigger optimize on slider release / select change ──
[shiftSelect, fleetSlider, capSlider, distSlider, fuelSlider].forEach(el =>
    el.addEventListener('change', runOptimization)
);

// ── Shift badge label update ──
shiftSelect.addEventListener('change', () => {
    shiftBadge.textContent = shiftSelect.options[shiftSelect.selectedIndex].text;
});

// ── Utility ──
const fmtMoney = n => 'Rs. ' + n.toLocaleString('en-US', {minimumFractionDigits: 0, maximumFractionDigits: 0});

// ── Init map with MapLibre GL + deck.gl overlay ──
let _map;
function initMap() {
    _map = new maplibregl.Map({
        container: 'map-container',
        style: 'https://tiles.openfreemap.org/styles/dark',
        center: [DEPOT_LON, DEPOT_LAT],
        zoom: 10.5,
        pitch: 45,
        bearing: 15
    });

    deckgl = new deck.MapboxOverlay({
        interleaved: false,
        layers: [],
        getTooltip: ({object}) => object && {
            html: `<div style="font-weight:600">${object.name || ''}</div>${object.demand ? `<div style="color:#94a3b8;margin-top:4px">${object.demand}</div>` : ''}`,
            style: {
                background: 'rgba(15,20,35,0.95)',
                color: '#e2e8f0',
                fontSize: '13px',
                padding: '10px 14px',
                borderRadius: '8px',
                border: '1px solid rgba(99,179,237,0.4)',
                boxShadow: '0 8px 24px rgba(0,0,0,0.6)'
            }
        }
    });

    _map.addControl(deckgl);
}

// ── Build the radius circle layer ──
function buildRadiusCircleLayer(radius_km) {
    const points = 120;
    const radiusMeters = radius_km * 1000;
    const latPerM = 1 / 111320;
    const lonPerM = 1 / (111320 * Math.cos(DEPOT_LAT * Math.PI / 180));
    const ring = [];
    for (let i = 0; i <= points; i++) {
        const angle = (i / points) * 2 * Math.PI;
        ring.push([
            DEPOT_LON + Math.cos(angle) * radiusMeters * lonPerM,
            DEPOT_LAT + Math.sin(angle) * radiusMeters * latPerM
        ]);
    }
    return new deck.PolygonLayer({
        id: 'radius-circle',
        data: [{ polygon: ring }],
        getPolygon: d => d.polygon,
        getFillColor: [62, 207, 207, 20],
        getLineColor: [62, 207, 207, 230],
        getLineWidth: 3,
        lineWidthMinPixels: 2,
        stroked: true,
        filled: true,
        pickable: false
    });
}

// ── Show radius ring ON TOP of existing route layers ──
function drawRadiusCircle(radius_km) {
    // Filter out any previous radius-circle layer, keep everything else
    const baseLayers = _currentLayers.filter(l => l.id !== 'radius-circle');
    deckgl.setProps({ layers: [...baseLayers, buildRadiusCircleLayer(radius_km)] });
}

// ── Remove radius ring, restore route layers ──
function clearRadiusCircle() {
    const baseLayers = _currentLayers.filter(l => l.id !== 'radius-circle');
    deckgl.setProps({ layers: baseLayers });
}

// ── Fetch dataset ──
async function generateData(fresh = false, radius_km = parseInt(radiusSlider.value)) {
    generateBtn.textContent = 'Generating...';
    generateBtn.disabled    = true;
    loadingTable.style.display = 'block';
    tableWrapper.style.display = 'none';
    statusAlert.className = 'alert hidden';

    try {
        const url = fresh
            ? `/api/generate?radius_km=${radius_km}&fresh=true`
            : `/api/generate?radius_km=${radius_km}`;
        const res  = await fetch(url);
        currentData = await res.json();
        populateTable();
        updateWorkforceEstimate();
        await runOptimization();
    } catch(e) {
        hideStepper();
        alert('Failed to load dataset from server.');
    }

    generateBtn.textContent = 'Regenerate Dataset';
    generateBtn.disabled    = false;
}

generateBtn.onclick = () => generateData(true, parseInt(radiusSlider.value));

// ── Populate manifest table ──
function populateTable() {
    tableBody.innerHTML = '';
    currentData.forEach((row, i) => {
        if (i === 0) return;
        // Unique workers = morning shift + afternoon shift (drops are same people returning)
        const uniqueWorkers = (row.Demand_10AM_Collect || 0) + (row.Demand_2PM_Collect || 0);
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${row.Route_ID}</td>
            <td>${row.Destination_Name}</td>
            <td class="num">${row.Demand_10AM_Collect}</td>
            <td class="num muted">${row.Demand_2PM_Drop}</td>
            <td class="num">${row.Demand_2PM_Collect}</td>
            <td class="num muted">${row.Demand_10PM_Drop}</td>
            <td class="num total">${uniqueWorkers}</td>
        `;
        tableBody.appendChild(tr);
    });
    loadingTable.style.display = 'none';
    tableWrapper.style.display = 'block';
}

function updateWorkforceEstimate() {
    if (!currentData.length || !workforceEst) return;
    const total = currentData.reduce((s, r) =>
        s + (r.Demand_10AM_Collect || 0) + (r.Demand_2PM_Collect || 0), 0);
    workforceEst.textContent = '~' + total.toLocaleString();
}

// ── Run optimization ──
async function runOptimization() {
    if (!currentData.length) return;

    // Reset metrics and show stepper
    showStepper();
    setStep(1); // Step 1: Fetching OSRM distances
    mBaseline.textContent  = '—';
    mOptimized.textContent = '—';
    mSavings.textContent   = '';
    mBuses.textContent     = '—';
    mCo2.textContent       = '—';
    mapMode.textContent    = 'Optimising...';
    statusAlert.className  = 'alert hidden';

    const body = {
        shift_col:        shiftSelect.value,
        vehicle_capacity: parseInt(capSlider.value),
        max_oneway_km:    parseInt(distSlider.value),
        fleet_size:       parseInt(fleetSlider.value),
        fuel_cost:        parseInt(fuelSlider.value),
        data:             currentData
    };

    // Step 2 fires right as we POST (server is now running OR-Tools)
    // Small delay so the user sees Step 1 flash before Step 2
    await new Promise(r => setTimeout(r, 300));
    setStep(2); // Step 2: OR-Tools solving

    try {
        const res    = await fetch('/api/optimize', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });
        const result = await res.json();

        if (result.status === 'Success') {
            setStep(3); // Step 3: Tracing road geometries
            updateMetrics(result, body.fuel_cost, body.fleet_size);
            mapMode.textContent = `${result.routes.length} routes`;
            await drawMap(result); // Step 4 fires inside drawMap
        } else {
            showError(result.message);
            mapMode.textContent = 'No solution found';
            await drawMap(null);
            hideStepper();
        }
    } catch(e) {
        showError('Server connection error.');
        hideStepper();
    }
}

function updateMetrics(res, fuelCost, fleetSize) {
    const baseCost = res.baseline_distance_km * fuelCost;
    const optCost  = res.optimized_distance_km * fuelCost;
    const pct      = baseCost > 0 ? (res.cost_saved_lkr / baseCost * 100).toFixed(1) : 0;

    mBaseline.textContent  = fmtMoney(baseCost);
    mOptimized.textContent = fmtMoney(optCost);
    mSavings.textContent   = `Saving ${fmtMoney(res.cost_saved_lkr)} (${pct}% less)`;
    mBuses.textContent     = `${res.buses_used}`;
    mBusesSub.textContent  = `out of ${fleetSize} available`;
    mCo2.textContent       = `${res.emissions_saved_kg.toLocaleString()} kg`;

    if (res.dropped_demand > 0) {
        const oneway = parseInt(distSlider.value);
        const parts = [];
        if (res.distance_dropped_count > 0)
            parts.push(`<b>${res.distance_dropped_count} stops</b> are beyond your <b>${oneway} km one-way road limit</b> (too far from factory)`);
        if (res.fleet_dropped_count > 0)
            parts.push(`<b>${res.fleet_dropped_count} stops</b> could not be served with only <b>${fleetSize} buses</b> (fleet too small for demand)`);
        statusAlert.innerHTML = `⚠️ <b>${res.dropped_nodes.length} stops excluded (${res.dropped_demand} passengers unserved):</b> ${parts.join(' — and ')}. Adjust the relevant parameters to serve them.`;
        statusAlert.className = 'alert warning';
    }
}

function showError(msg) {
    mBaseline.textContent  = 'N/A';
    mOptimized.textContent = 'N/A';
    mSavings.textContent   = '';
    mBuses.textContent     = '-';
    mCo2.textContent       = '-';
    statusAlert.innerHTML  = `<b>Optimisation Error:</b> ${msg}`;
    statusAlert.className  = 'alert error';
}

// ── OSRM route geometry ──
async function getOsrmRoute(lats, lons) {
    const coords = lons.map((lon, i) => `${lon},${lats[i]}`).join(';');
    try {
        const res  = await fetch(`https://router.project-osrm.org/route/v1/driving/${coords}?geometries=geojson&overview=full`);
        const data = await res.json();
        if (data.routes && data.routes[0]) return data.routes[0].geometry.coordinates;
    } catch(e) {}
    return lons.map((lon, i) => [lon, lats[i]]);
}

// ── Draw DeckGL layers ──
async function drawMap(optResult) {
    const shift = shiftSelect.value;

    const depotLayer = new deck.ScatterplotLayer({
        id: 'depot',
        data: [{name: 'MAS Controline Pallekele (Depot)', coordinates: [DEPOT_LON, DEPOT_LAT]}],
        getPosition: d => d.coordinates,
        getFillColor: [255, 140, 0, 255],
        getRadius: 700,
        pickable: true
    });

    const destData = currentData
        .filter((d, i) => i > 0 && d[shift] > 0)
        .map(d => ({
            name: d.Destination_Name,
            demand: `Passengers: ${d[shift]}`,
            coordinates: [d.Longitude, d.Latitude]
        }));

    const destLayer = new deck.ScatterplotLayer({
        id: 'destinations',
        data: destData,
        getPosition: d => d.coordinates,
        getFillColor: [30, 144, 255, 210],
        getRadius: 350,
        pickable: true,
        autoHighlight: true
    });

    let layers = [depotLayer, destLayer];

    if (optResult && optResult.routes && optResult.routes.length > 0) {
        const colors = [
            [46,204,113,230],[52,152,219,230],[155,89,182,230],
            [231,76,60,230],[241,196,15,230],[26,188,156,230],
            [230,126,34,230],[189,195,199,230]
        ];

        const promises = optResult.routes.map(async (routePath, i) => {
            const lats = routePath.map(idx => currentData[idx].Latitude);
            const lons = routePath.map(idx => currentData[idx].Longitude);
            const road = await getOsrmRoute(lats, lons);
            return {
                path: road,
                color: colors[i % colors.length],
                name: `Bus Route ${i + 1}`,
                demand: ''
            };
        });

        const pathData = await Promise.all(promises);

        layers.push(new deck.PathLayer({
            id: 'routes',
            data: pathData,
            getPath: d => d.path,
            getColor: d => d.color,
            widthScale: 20,
            widthMinPixels: 4,
            getWidth: 3,
            pickable: true,
            autoHighlight: true
        }));
    }

    setStep(4); // Step 4: Rendering map
    _currentLayers = layers; // save so radius circle can overlay without wiping
    deckgl.setProps({layers});
    // Small delay then hide stepper so user sees Step 4 complete
    await new Promise(r => setTimeout(r, 600));
    hideStepper();
}

// ── Boot ──
initMap();
generateData(false, parseInt(radiusSlider.value));
