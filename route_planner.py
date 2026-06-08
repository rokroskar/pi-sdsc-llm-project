"""
Route planner for Zurich fountain walking routes.
Uses OSRM for walking routing and geocoding.
Plans routes that visit fountains approximately every 15 minutes of walking.
"""

import math
import time
from typing import Optional

import numpy as np
import pandas as pd
import requests

# OSRM demo server (free, no API key needed)
OSRM_Routing_URL = "https://router.project-osrm.org/route/v1/walking"
OSRM_Geocode_URL = "https://nominatim.openstreetmap.org/search"

# Walking speed: ~5 km/h ≈ 83.3 m/min, so 15 min ≈ 1250m
WALKING_SPEED_M_PER_MIN = 83.3
TARGET_INTERVAL_MIN = 15
TARGET_INTERVAL_M = WALKING_SPEED_M_PER_MIN * TARGET_INTERVAL_MIN  # ~1250m


def geocode_address(address: str) -> Optional[tuple]:
    """
    Geocode an address using Nominatim (OpenStreetMap).
    Returns (lat, lon) or None.
    """
    try:
        params = {
            "q": f"{address}, Zurich, Switzerland",
            "format": "json",
            "limit": 1,
            "addressdetails": 0,
        }
        headers = {"User-Agent": "zuerich-fountain-dashboard/1.0"}
        resp = requests.get(OSRM_Geocode_URL, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        results = resp.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as e:
        print(f"Geocoding error for '{address}': {e}")
    return None


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two points in meters."""
    R = 6371000  # Earth radius in meters
    phi1, phi2 = np.radians([lat1, lat2])
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)

    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2) ** 2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
    return R * c


def osrm_distance_duration(coords: list) -> dict:
    """
    Get distance and duration from OSRM for a list of coordinates.
    coords: list of (lon, lat) tuples
    """
    coords_str = ";".join(f"{lon},{lat}" for lon, lat in coords)
    url = f"{OSRM_Routing_URL}/{coords_str}"

    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("routes"):
            route = data["routes"][0]
            return {
                "distance_m": route["distance"],
                "duration_s": route["duration"],
                "duration_min": route["duration"] / 60,
                "geometry": route.get("geometry"),
            }
    except Exception as e:
        print(f"OSRM routing error: {e}")
    return {"distance_m": 0, "duration_s": 0, "duration_min": 0, "geometry": None}


def nearest_fountains_from_point(
    df: pd.DataFrame,
    lat: float,
    lon: float,
    n: int = 5,
    max_distance_m: float = 500,
) -> pd.DataFrame:
    """Find the nearest fountains from a given point."""
    if len(df) == 0:
        return pd.DataFrame()

    df = df.copy()
    df["dist_m"] = df.apply(
        lambda row: haversine_distance(lat, lon, row["lat"], row["lon"]), axis=1
    )
    nearby = df[df["dist_m"] <= max_distance_m].sort_values("dist_m").head(n)
    return nearby


def _greedy_nearest_fountain(
    start: tuple,
    destination: tuple,
    available_fountains: pd.DataFrame,
    visited: set,
    max_detour_factor: float = 1.5,
) -> Optional[tuple]:
    """
    Greedy approach: pick the nearest unvisited fountain that doesn't
    add too much detour to the direct route.
    """
    if available_fountains.empty:
        return None

    direct_dist = haversine_distance(start[0], start[1], destination[0], destination[1])

    best_fountain = None
    best_score = float("inf")

    for _, fountain in available_fountains.iterrows():
        if fountain.name in visited:
            continue

        f_lon, f_lat = fountain.lon, fountain.lat

        # Distance from start to fountain
        dist_start_to_f = haversine_distance(start[0], start[1], f_lat, f_lon)
        # Distance from fountain to destination
        dist_f_to_dest = haversine_distance(f_lat, f_lon, destination[0], destination[1])

        # Total detour distance
        total_via_f = dist_start_to_f + dist_f_to_dest
        detour_ratio = total_via_f / max(direct_dist, 1)

        # Score: prefer fountains that are close and don't add too much detour
        score = dist_start_to_f + detour_ratio * 500
        if score < best_score:
            best_score = score
            best_fountain = (f_lon, f_lat, fountain)

    if best_fountain and best_score < direct_dist * max_detour_factor:
        return best_fountain
    return None


def plan_fountain_route(
    df: pd.DataFrame,
    start_address: str,
    destination_address: str,
    max_fountains: int = 5,
    min_fountains: int = 1,
) -> dict:
    """
    Plan a walking route from start to destination that visits fountains
    approximately every 15 minutes of walking.

    Returns:
        dict with keys:
            - segments: list of dicts with start, end, fountain (optional), distance, duration
            - total_distance_m: total distance in meters
            - total_duration_min: total walking time in minutes
            - fountains_visited: list of fountain names visited
            - start_coords: (lat, lon)
            - end_coords: (lat, lon)
            - route_coords: list of (lon, lat) for the full route
    """
    # Geocode addresses
    start_coords = geocode_address(start_address)
    end_coords = geocode_address(destination_address)

    if not start_coords or not end_coords:
        return {
            "error": "Could not geocode one or both addresses. Please try a more specific address in Zurich.",
            "segments": [],
        }

    start_lat, start_lon = start_coords
    end_lat, end_lon = end_coords

    # Filter to fountains within Zurich area (rough bounding box)
    zurich_bbox = df[
        (df["lat"] >= 47.3) & (df["lat"] <= 47.4) & (df["lon"] >= 8.45) & (df["lon"] <= 8.65)
    ]

    # Calculate direct route duration
    direct = osrm_distance_duration([(start_lon, start_lat), (end_lon, end_lat)])
    direct_duration_min = direct["duration_min"]

    if direct_duration_min < TARGET_INTERVAL_MIN:
        # Route is too short, just show direct route
        return {
            "segments": [
                {
                    "start": {"lat": start_lat, "lon": start_lon, "address": start_address},
                    "end": {"lat": end_lat, "lon": end_lon, "address": destination_address},
                    "fountain": None,
                    "distance_m": direct["distance_m"],
                    "duration_min": direct["duration_min"],
                }
            ],
            "total_distance_m": direct["distance_m"],
            "total_duration_min": direct["duration_min"],
            "fountains_visited": [],
            "start_coords": start_coords,
            "end_coords": end_coords,
            "route_coords": [(start_lon, start_lat), (end_lon, end_lat)],
            "note": "Route is shorter than 15 minutes. No fountain stops needed.",
        }

    # Target number of fountain stops
    target_stops = max(min_fountains, min(max_fountains, int(direct_duration_min / TARGET_INTERVAL_MIN)))

    # Find candidate fountains along the route
    # Sample points along the direct route and find nearby fountains
    candidate_fountains = []
    n_samples = max(target_stops * 3, 10)

    for i in range(n_samples + 1):
        t = i / n_samples
        sample_lat = start_lat + t * (end_lat - start_lat)
        sample_lon = start_lon + t * (end_lon - start_lon)

        nearby = nearest_fountains_from_point(zurich_bbox, sample_lat, sample_lon, n=3, max_distance_m=400)
        for _, fountain in nearby.iterrows():
            candidate_fountains.append(fountain)

    # Deduplicate by name
    candidate_fountains = candidate_fountains.drop_duplicates(subset=["name"]).reset_index(drop=True)

    if len(candidate_fountains) == 0:
        return {
            "segments": [
                {
                    "start": {"lat": start_lat, "lon": start_lon, "address": start_address},
                    "end": {"lat": end_lat, "lon": end_lon, "address": destination_address},
                    "fountain": None,
                    "distance_m": direct["distance_m"],
                    "duration_min": direct["duration_min"],
                }
            ],
            "total_distance_m": direct["distance_m"],
            "total_duration_min": direct["duration_min"],
            "fountains_visited": [],
            "start_coords": start_coords,
            "end_coords": end_coords,
            "route_coords": [(start_lon, start_lat), (end_lon, end_lat)],
            "note": "No fountains found near the route. Showing direct route.",
        }

    # Greedy route planning with fountain stops
    segments = []
    current_lat, current_lon = start_lat, start_lon
    visited = set()
    fountains_visited = []

    # Divide the route into target_stops + 1 segments
    n_legs = target_stops + 1
    leg_points = []
    for i in range(n_legs + 1):
        t = i / n_legs
        leg_points.append((
            start_lat + t * (end_lat - start_lat),
            start_lon + t * (end_lon - start_lon),
        ))

    for leg_idx in range(n_legs):
        leg_start = leg_points[leg_idx]
        leg_end = leg_points[min(leg_idx + 1, n_legs)]

        if leg_idx < target_stops:
            # Try to find a fountain for this leg
            # Search in the middle of this leg's area
            mid_lat = (leg_start[0] + leg_end[0]) / 2
            mid_lon = (leg_start[1] + leg_end[1]) / 2

            nearby = nearest_fountains_from_point(
                zurich_bbox, mid_lat, mid_lon, n=5, max_distance_m=500
            )

            best_fountain = None
            best_dist = float("inf")
            for _, fountain in nearby.iterrows():
                if fountain.name not in visited:
                    d = haversine_distance(current_lat, current_lon, fountain.lat, fountain.lon)
                    if d < best_dist:
                        best_dist = d
                        best_fountain = fountain

            if best_fountain is not None:
                # Go to fountain first
                fountain_coords = (best_fountain.lon, best_fountain.lat)
                seg_to_fountain = osrm_distance_duration([(current_lon, current_lat), fountain_coords])

                segments.append({
                    "start": {"lat": current_lat, "lon": current_lon, "address": start_address if leg_idx == 0 else "Fountain: " + best_fountain.name},
                    "end": {"lat": best_fountain.lat, "lon": best_fountain.lon, "address": best_fountain.name},
                    "fountain": {
                        "name": best_fountain.name,
                        "type": best_fountain.get("type", ""),
                        "lat": best_fountain.lat,
                        "lon": best_fountain.lon,
                    },
                    "distance_m": seg_to_fountain["distance_m"],
                    "duration_min": seg_to_fountain["duration_min"],
                })
                visited.add(best_fountain.name)
                fountains_visited.append(best_fountain.name)
                current_lat, current_lon = best_fountain.lat, best_fountain.lon

        # Go to next leg point (or destination)
        next_point = leg_end if leg_idx == n_legs - 1 else leg_points[leg_idx + 1]
        seg_coords = [(current_lon, current_lat)]
        if leg_idx == n_legs - 1:
            seg_coords.append((end_lon, end_lat))
        else:
            # Approximate: go to next waypoint
            seg_coords.append((next_point[1], next_point[0]))

        seg = osrm_distance_duration(seg_coords)

        segments.append({
            "start": {"lat": current_lat, "lon": current_lon, "address": fountains_visited[-1] if leg_idx == 0 and fountains_visited else (start_address if leg_idx == 0 else "Previous stop")},
            "end": {"lat": end_lat if leg_idx == n_legs - 1 else None, "lon": end_lon if leg_idx == n_legs - 1 else None, "address": destination_address if leg_idx == n_legs - 1 else "Next waypoint"},
            "fountain": None,
            "distance_m": seg["distance_m"],
            "duration_min": seg["duration_min"],
        })
        current_lat, current_lon = end_lat, end_lon if leg_idx == n_legs - 1 else (next_point[0], next_point[1])

    # Calculate total
    total_distance = sum(s["distance_m"] for s in segments)
    total_duration = sum(s["duration_min"] for s in segments)

    # Build route coordinates
    route_coords = [(start_lon, start_lat)]
    for seg in segments:
        if seg.get("fountain"):
            route_coords.append((seg["fountain"]["lon"], seg["fountain"]["lat"]))
    route_coords.append((end_lon, end_lat))

    return {
        "segments": segments,
        "total_distance_m": total_distance,
        "total_duration_min": total_duration,
        "fountains_visited": fountains_visited,
        "start_coords": start_coords,
        "end_coords": end_coords,
        "route_coords": route_coords,
        "target_stops": target_stops,
    }


def format_distance(meters: float) -> str:
    """Format distance in human-readable form."""
    if meters < 1000:
        return f"{meters:.0f} m"
    return f"{meters / 1000:.2f} km"


def format_duration(minutes: float) -> str:
    """Format duration in human-readable form."""
    if minutes < 60:
        return f"{minutes:.0f} min"
    hours = int(minutes // 60)
    mins = int(minutes % 60)
    return f"{hours}h {mins}min" if mins > 0 else f"{hours}h"
