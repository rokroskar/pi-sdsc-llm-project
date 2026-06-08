# 🚰 Zürich Water Fountain Dashboard

An interactive dashboard for exploring Zürich's water fountain locations, built with Streamlit.

## Features

- **🗺️ Fountain Map** — Interactive map showing all fountain locations with clickable markers
- **🔥 Density Map** — Kernel density heatmap showing fountain concentration across the city
- **📊 Statistics** — Summary statistics by water source, ownership, and location
- **🚶 Route Planner** — Plan walking routes with fountain stops every ~15 minutes

## Data Source

Fountain data from [Open Data Zürich](https://data.stadt-zuerich.ch/dataset/geo_brunnen) (Brunnen dataset), accessed via WFS.

## Quick Start

### Local Development

```bash
# Install dependencies
uv pip install streamlit folium geopandas requests pandas numpy scipy streamlit-folium osmnx

# Run the dashboard
streamlit run app.py
```

### With uv

```bash
uv sync
uv run streamlit run app.py
```

## Project Structure

```
├── app.py                  # Main Streamlit dashboard
├── fountain_data.py        # Data loading from Open Data Zürich WFS
├── route_planner.py        # Walking route planner with fountain stops
├── pyproject.toml          # Python dependencies
├── data/                   # Cached data files
│   └── README.md
└── .gitignore
```

## Route Planner

The route planner:
1. Geocodes start and destination addresses using Nominatim (OpenStreetMap)
2. Finds fountains near the route using OSRM for walking distances
3. Plans stops approximately every 15 minutes of walking
4. Displays the route on an interactive map

## Renku Deployment

This project is designed to work with Renku. No Dockerfile is needed — Renku will build the image from `pyproject.toml`.

### Launchers

- **Build** — `uv sync` installs dependencies from pyproject.toml
- **Run** — `streamlit run app.py --server.port $PORT --server.address 0.0.0.0`

## Technical Choices

- **Streamlit** — Best-in-class for interactive data dashboards, easy deployment
- **Folium** — Interactive Leaflet maps with heatmap support
- **OSRM** — Free walking routing (no API key needed)
- **Nominatim** — Free geocoding for address lookup
- **WFS (GML)** — Open Data Zürich's fountain data via WFS endpoint
