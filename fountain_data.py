"""
Data loading and processing for Zurich water fountain dataset.
Fetches data from Open Data Zurich and processes it into a GeoDataFrame.
"""

import os
import json
import io
from datetime import datetime, timedelta
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

# Try multiple known endpoints for the Brunnen (water fountain) dataset
# Primary: WFS endpoint from ogd.stadt-zuerich.ch
DATA_URLS = [
    "https://www.ogd.stadt-zuerich.ch/wfs/geoportal/Brunnen?SERVICE=WFS&VERSION=1.1.0&REQUEST=GetFeature&TYPENAME=wvz_brunnen&SRSNAME=EPSG:4326&MAXFEATURES=5000",
]

CACHE_DIR = Path(__file__).parent / "data"
CACHE_FILE = CACHE_DIR / "brunnen.geojson"
CACHE_TTL = timedelta(hours=24)  # Re-fetch after 24 hours


def _is_ckan_api_response(data: dict) -> bool:
    """Check if response is a CKAN API response (has help/success/result keys)."""
    return "success" in data and "result" in data


def _extract_geojson_url_from_ckan(data: dict) -> str | None:
    """Extract the GeoJSON resource URL from a CKAN package_show response."""
    result = data.get("result", {})
    resources = result.get("resources", [])
    for res in resources:
        fmt = (res.get("format") or "").lower()
        if fmt in ["geojson", "json", "geopackage", "shapefile", "csv"]:
            url = res.get("url") or res.get("downloadUrl") or res.get("resource_url")
            if url:
                return url
    # Fallback: return first resource URL
    if resources:
        return resources[0].get("url") or resources[0].get("downloadUrl")
    return None


def _is_gml_response(text: str) -> bool:
    """Check if response is GML (WFS GetFeature)."""
    return "<wfs:FeatureCollection" in text or "<gml:featureMember" in text


def _lv95_to_wgs84(h, v):
    """Convert Swiss LV95 coordinates (EPSG:2056) to WGS84 (lat, lon) using pyproj."""
    from pyproj import Transformer
    transformer = Transformer.from_crs("EPSG:2056", "EPSG:4326", always_xy=True)
    lons, lats = transformer.transform(h, v)
    return lats, lons


def _parse_gml_to_dataframe(gml_text: str) -> pd.DataFrame:
    """Parse GML WFS response into a DataFrame."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(gml_text)
    except ET.ParseError:
        root = ET.fromstring(
            gml_text.replace('xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"', '')
        )

    records = []
    # Find all feature members
    for feature in root.iter():
        tag = feature.tag.split("}")[-1] if "}" in feature.tag else feature.tag
        if tag == "wvz_brunnen":
            record = {}
            for child in feature:
                child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if child_tag in ("geometry", "geometrie_gdo"):
                    continue
                text = (child.text or "").strip()
                record[child_tag] = text
            records.append(record)

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    # Clean up duplicate columns - keep first occurrence
    seen = set()
    dupes = [c for c in df.columns if c in seen or seen.add(c)]
    if dupes:
        df = df.drop(columns=dupes)

    # Rename columns to match expected format
    column_map = {}
    for col in df.columns:
        lower = col.lower()
        if "standort" in lower:
            column_map[col] = "name"
        elif "quartier" in lower:
            column_map[col] = "gemeinde"
        elif "ortsbezeichnung" in lower:
            column_map[col] = "strasse"
        elif "wasserart" in lower:
            column_map[col] = "type"
        elif "brunnennummer" in lower:
            column_map[col] = "brunnennummer"
        elif "art" in lower and "eigent" not in lower:
            column_map[col] = "art"
        elif "art_eigentuemer" in lower:
            column_map[col] = "status"
        elif "baujahr" in lower:
            column_map[col] = "baujahr"
        elif "historisches_baujahr" in lower:
            column_map[col] = "historisches_baujahr"
        elif "architekt_bildhauer" in lower:
            column_map[col] = "architekt"
        elif "bemerkung" in lower or "notiz" in lower:
            column_map[col] = "bemerkung"
        elif "trinkwasser" in lower or "trink" in lower:
            column_map[col] = "trinkwasser"
        elif "abgestellt" in lower:
            column_map[col] = "trinkwasser"  # abgestellt = ja/neu, relates to availability

    df = df.rename(columns=column_map)

    # Remove any remaining duplicate columns after rename
    seen = set()
    dupes = [c for c in df.columns if c in seen or seen.add(c)]
    if dupes:
        df = df.drop(columns=dupes)

    # Convert Swiss coordinates (hkoord/vkoord) to lat/lon
    if "hkoord" in df.columns and "vkoord" in df.columns:
        valid = df["hkoord"].notna() & df["vkoord"].notna()
        h_vals = df.loc[valid, "hkoord"].astype(float)
        v_vals = df.loc[valid, "vkoord"].astype(float)
        lats, lons = _lv95_to_wgs84(h_vals.values, v_vals.values)
        df.loc[valid, "lat"] = lats
        df.loc[valid, "lon"] = lons

    # Drop coordinate columns we don't need
    for col in ["hkoord", "vkoord", "boundedBy", "objectid", "eigentuemer", "stadtkreis",
                "brunnennummer", "steinhauer", "material_trog", "material_saeule",
                "material_figur", "datum_abstellung", "grund_abstellung",
                "datum_wiederinbetriebnahme", "datum_aenderung", "druckzone",
                "u_aks_nummer", "foto"]:
        if col in df.columns:
            df = df.drop(columns=[col])

    return df


def _fetch_geojson(url: str) -> dict | None:
    """Try to fetch GeoJSON from a URL. Handles CKAN API, WFS GML, and direct GeoJSON."""
    try:
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()

        # Check if it's GML (WFS response) - check text first before json()
        if _is_gml_response(resp.text):
            print(f"  ✓ Received GML (WFS) response")
            return {"gml": resp.text}

        # Try to parse as JSON
        try:
            data = resp.json()
        except Exception:
            print(f"  Response is not JSON: {resp.headers.get('Content-Type', 'unknown')}")
            return None

        # Check if it's a CKAN API response
        if _is_ckan_api_response(data):
            resource_url = _extract_geojson_url_from_ckan(data)
            if resource_url:
                print(f"  Found resource URL: {resource_url}")
                resp2 = requests.get(resource_url, timeout=120, allow_redirects=True)
                resp2.raise_for_status()
                if _is_gml_response(resp2.text):
                    return {"gml": resp2.text}
                try:
                    return resp2.json()
                except Exception:
                    return None
            return None

        return data
    except Exception as e:
        print(f"  Failed to fetch {url}: {e}")
        return None


def _parse_features(features: list) -> pd.DataFrame:
    """Parse GeoJSON features into a DataFrame with useful columns."""
    records = []
    for feat in features:
        props = feat.get("properties", {})
        geom = feat.get("geometry")
        if not geom or geom.get("type") != "Point":
            continue

        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue

        lat, lon = coords[1], coords[0]  # GeoJSON is lon/lat

        record = {
            "lat": lat,
            "lon": lon,
            "name": props.get("Name", props.get("name", props.get("Bezeichnung", "Unbekannt"))),
            "type": props.get("Art", props.get("art", props.get("Typ", "Unbekannt"))),
            "status": props.get("Status", props.get("status", props.get("Zustand", ""))),
            "trinkwasser": props.get("Trinkwasser", props.get("trinkwasser", props.get("drinking_water", None))),
            "armbrunnen": props.get("Armbrunnen", props.get("armbrunnen", None)),
            "schmutzwasser": props.get("Schmutzwasser", props.get("schmutzwasser", None)),
            "kanton": props.get("Kanton", props.get("kanton", "ZH")),
            "gemeinde": props.get("Gemeinde", props.get("gemeinde", "Zürich")),
            "strasse": props.get("Strasse", props.get("strasse", "")),
            "bemerkung": props.get("Bemerkung", props.get("bemerkung", "")),
            "geodata_source": props.get("Geodata_source", ""),
        }
        records.append(record)

    df = pd.DataFrame(records)
    return df


def load_fountains(force_refresh: bool = False) -> pd.DataFrame:
    """
    Load water fountain data from Open Data Zurich.
    Uses cached data if available and fresh (within TTL).

    Returns:
        DataFrame with columns: lat, lon, name, type, status, trinkwasser, etc.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Check cache
    if not force_refresh and CACHE_FILE.exists():
        cache_age = datetime.now() - datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
        if cache_age < CACHE_TTL:
            print(f"Loading cached fountain data from {CACHE_FILE}")
            return _load_cached()

    # Try fetching from known URLs
    print("Fetching fountain data from Open Data Zurich...")
    data = None
    for url in DATA_URLS:
        print(f"  Trying {url}...")
        data = _fetch_geojson(url)
        if data:
            print(f"  ✓ Success from {url}")
            break

    if not data:
        # Last resort: try a broader search
        print("  Direct URLs failed, trying broader search...")
        data = _try_broad_search()

    if not data:
        raise RuntimeError(
            "Could not fetch fountain data. Please check the Open Data Zurich portal "
            "for the current dataset URL and add it to DATA_URLS in fountain_data.py"
        )

    # Parse data - could be GeoJSON features or GML
    if "gml" in data:
        print("  Parsing GML/WFS data...")
        df = _parse_gml_to_dataframe(data["gml"])
    else:
        features = data.get("features", [])
        if not features:
            features = data.get("data", [])
        if not features:
            raise RuntimeError(f"Data has no features. Keys: {list(data.keys())}")
        df = _parse_features(features)
    print(f"Loaded {len(df)} fountains")

    # Save cache
    gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat), crs="EPSG:4326")
    gdf.to_file(CACHE_FILE, driver="GeoJSON")
    print(f"Cached to {CACHE_FILE}")

    return df


def _load_cached() -> pd.DataFrame:
    """Load from cached GeoJSON file."""
    gdf = gpd.read_file(CACHE_FILE)
    df = gdf.copy()

    # Ensure we have lat/lon columns
    if "lat" not in df.columns or "lon" not in df.columns:
        df["lat"] = df.geometry.y
        df["lon"] = df.geometry.x

    return df


def _try_broad_search() -> dict | None:
    """Try alternative endpoints and data formats."""
    # Try CSV format from data.stadt-zuerich.ch
    csv_url = "https://data.stadt-zuerich.ch/dataset/geo_brunnen/resource/brunnen/download/brunnen.csv"
    try:
        resp = requests.get(csv_url, timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        print(f"  Fetched CSV with columns: {list(df.columns)}")
        # Try to find lat/lon columns
        lat_col = next((c for c in df.columns if "lat" in c.lower()), None)
        lon_col = next((c for c in df.columns if "lon" in c.lower() and "lat" not in c.lower()), None)
        if lat_col and lon_col:
            df = df.rename(columns={lat_col: "lat", lon_col: "lon"})
            # Keep useful columns
            name_col = next((c for c in df.columns if "name" in c.lower() or "bezeich" in c.lower()), None)
            if name_col:
                df = df.rename(columns={name_col: "name"})
            return df
    except Exception as e:
        print(f"  CSV fetch failed: {e}")

    # Try the Open Data Zürich CKAN API search
    try:
        search_url = "https://opendata.stadt-zuerich.ch/api/3/action/package_search?q=brunnen&rows=5"
        resp = requests.get(search_url, timeout=30)
        result = resp.json()
        if result.get("success"):
            packages = result["result"]["results"]
            for pkg in packages:
                print(f"  Found package: {pkg.get('title', 'N/A')}")
                for res in pkg.get("resources", []):
                    if res.get("format") in ["GEOJSON", "geojson", "JSON"]:
                        geo_url = res.get("url")
                        if geo_url:
                            return _fetch_geojson(geo_url)
    except Exception as e:
        print(f"  API search failed: {e}")

    return None


def get_fountain_statistics(df: pd.DataFrame) -> dict:
    """Compute summary statistics about the fountains."""
    stats = {
        "total": len(df),
        "with_name": int(df["name"].notna().sum()),
        "with_type": int(df["type"].notna().sum()),
        "drinking_water_count": int(df["trinkwasser"].sum()) if df["trinkwasser"].dtype == "bool" else 0,
        "drinking_water_pct": 0.0,
        "types": df["type"].value_counts().head(10).to_dict() if "type" in df.columns else {},
        "statuses": df["status"].value_counts().head(10).to_dict() if "status" in df.columns else {},
        "lat_min": float(df["lat"].min()),
        "lat_max": float(df["lat"].max()),
        "lon_min": float(df["lon"].min()),
        "lon_max": float(df["lon"].max()),
        "lat_center": float(df["lat"].mean()),
        "lon_center": float(df["lon"].mean()),
    }

    # Calculate drinking water percentage
    if "trinkwasser" in df.columns:
        if df["trinkwasser"].dtype == "bool":
            stats["drinking_water_pct"] = float(df["trinkwasser"].mean() * 100)
        else:
            # Try to parse string values
            true_values = ["true", "ja", "1", "yes"]
            stats["drinking_water_count"] = int(df["trinkwasser"].str.lower().isin(true_values).sum())
            stats["drinking_water_pct"] = stats["drinking_water_count"] / max(len(df), 1) * 100

    return stats


def filter_fountains(
    df: pd.DataFrame,
    drinking_water: bool = False,
    fountain_type: str = "all",
    status: str = "all",
) -> pd.DataFrame:
    """Apply filters to the fountain dataset."""
    filtered = df.copy()

    if drinking_water:
        if "trinkwasser" in filtered.columns:
            if filtered["trinkwasser"].dtype == "bool":
                filtered = filtered[filtered["trinkwasser"] == True]  # noqa: E712
            else:
                true_values = ["true", "ja", "1", "yes"]
                filtered = filtered[filtered["trinkwasser"].str.lower().isin(true_values)]

    if fountain_type != "all":
        if "type" in filtered.columns:
            filtered = filtered[filtered["type"].str.lower().str.contains(fountain_type.lower(), na=False)]

    if status != "all":
        if "status" in filtered.columns:
            filtered = filtered[filtered["status"].str.lower().str.contains(status.lower(), na=False)]

    return filtered
