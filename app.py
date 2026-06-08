"""
Zürich Water Fountain Dashboard
Interactive map and route planner for Zurich's water fountains.
"""

import streamlit as st
import folium
from folium import plugins
import pandas as pd
import numpy as np
from streamlit_folium import st_folium

from fountain_data import load_fountains, get_fountain_statistics, filter_fountains
from route_planner import (
    plan_fountain_route,
    format_distance,
    format_duration,
)

# ─── Page Config ───────────────────────────────────────────────
st.set_page_config(
    page_title="🚰 Zürich Fountain Dashboard",
    page_icon="🚰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Sidebar Filters ───────────────────────────────────────────
st.sidebar.title("🔍 Filters")

force_refresh = st.sidebar.checkbox("🔄 Refresh data", value=False, help="Re-fetch fountain data from Open Data Zurich")

col_a, col_b = st.sidebar.columns(2)
with col_a:
    show_drinking = st.checkbox("💧 Drinking water only", value=False)
with col_b:
    show_non_drinking = st.checkbox("🚫 No drinking water", value=False)

fountain_type_filter = st.sidebar.selectbox(
    "Water source",
    options=["all"] + list(full_df["type"].unique()),
    format_func=lambda x: {"all": "All sources"}.get(x, x),
)

status_filter = st.sidebar.selectbox(
    "Ownership",
    options=["all"] + [s for s in full_df["status"].unique() if s],
    format_func=lambda x: {"all": "All owners"}.get(x, x),
)

# ─── Load Data ─────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def get_fountain_data(force: bool = False) -> pd.DataFrame:
    return load_fountains(force_refresh=force)


@st.cache_data(ttl=3600)
def get_filtered_data(df: pd.DataFrame, drinking: bool, non_drinking: bool, type_f: str, status_f: str) -> pd.DataFrame:
    filtered = filter_fountains(df, drinking_water=drinking, fountain_type=type_f, status=status_f)
    if non_drinking:
        if "trinkwasser" in filtered.columns:
            filtered = filtered[filtered["trinkwasser"].astype(str).str.lower().isin(["nein", "no", "false", "0"])]
    return filtered


with st.spinner("Loading fountain data..."):
    try:
        full_df = get_fountain_data(force_refresh)
        stats = get_fountain_statistics(full_df)
    except Exception as e:
        st.error(f"Failed to load fountain data: {e}")
        st.stop()

filtered_df = get_filtered_data(full_df, show_drinking, show_arm, fountain_type_filter, status_filter)

# ─── Title & Summary ──────────────────────────────────────────
st.title("🚰 Zürich Water Fountain Dashboard")
st.markdown(
    "Explore the locations of water fountains across Zürich. "
    "Use the map to find fountains, view density patterns, and plan walking routes with fountain stops."
)

# Summary cards
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Fountains", f"{stats['total']:,}")
col2.metric("Showing (filtered)", f"{len(filtered_df):,}")
col3.metric("Drinking Water", f"{stats.get('drinking_water_count', 'N/A')}")
col4.metric("Drinking Water %", f"{stats.get('drinking_water_pct', 0):.0f}%")

st.divider()

# ─── Tabs ──────────────────────────────────────────────────────
tab_map, tab_density, tab_stats, tab_route = st.tabs([
    "🗺️ Fountain Map",
    "🔥 Density Map",
    "📊 Statistics",
    "🚶 Route Planner",
])

# ─── Tab 1: Fountain Map ──────────────────────────────────────
with tab_map:
    st.subheader("Water Fountain Locations")
    st.caption(f"Showing {len(filtered_df):,} of {stats['total']:,} fountains")

    # Create base map centered on Zürich
    m = folium.Map(
        location=[stats["lat_center"], stats["lon_center"]],
        zoom_start=13,
        tiles="OpenStreetMap",
    )

    # Add fountain markers
    for _, row in filtered_df.iterrows():
        popup_html = f"""
        <div style="min-width: 200px;">
            <b>{row.get('name', 'Unknown')}</b><br>
            <i>{row.get('type', 'N/A')}</i><br>
            <hr style="margin: 4px 0;">
            <small>
            📍 {row.get('strasse', 'N/A')}<br>
            🏙️ {row.get('gemeinde', 'N/A')}<br>
            """
        if row.get("trinkwasser") is not None:
            tw = "💧 Yes" if str(row["trinkwasser"]).lower() in ("true", "ja", "1", "yes") else "❌ No"
            popup_html += f"💧 Drinking water: {tw}<br>"
        if row.get("status"):
            popup_html += f"🔧 Status: {row['status']}<br>"
        if row.get("bemerkung"):
            popup_html += f"📝 {row['bemerkung']}<br>"
        popup_html += "</small></div>"

        icon_color = "green"
        if row.get("trinkwasser") is not None:
            if str(row["trinkwasser"]).lower() not in ("true", "ja", "1", "yes"):
                icon_color = "red"
        if row.get("trinkwasser") is None or str(row["trinkwasser"]).strip() == "":
            icon_color = "gray"

        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=5,
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=row.get("name", ""),
            color=icon_color,
            fill=True,
            fill_color=icon_color,
        ).add_to(m)

    # Add legend
    legend_html = """
    <div style="
        position: fixed;
        bottom: 30px;
        left: 30px;
        z-index: 1000;
        background-color: white;
        padding: 10px 15px;
        border: 2px solid grey;
        border-radius: 5px;
        font-size: 12px;
    ">
        <b>Fountain Legend</b><br>
        <span style="color:green;">●</span> Drinking water available<br>
        <span style="color:red;">●</span> No drinking water<br>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    # Fit bounds
    if len(filtered_df) > 0:
        bounds = [
            [filtered_df["lat"].min(), filtered_df["lon"].min()],
            [filtered_df["lat"].max(), filtered_df["lon"].max()],
        ]
        m.fit_bounds(bounds, padding=[5, 5])

    # Display map
    map_data = st_folium(m, width=1200, height=600, key="fountain_map")

    # Show selected fountain info
    if map_data and map_data.get("last_clicked"):
        clicked = map_data["last_clicked"]
        clicked_lat, clicked_lon = clicked["lat"], clicked["lng"]
        nearby = filtered_df[
            (filtered_df["lat"] - clicked_lat).abs() < 0.002
        ]
        if len(nearby) > 0:
            st.markdown(f"**Clicked near:** {clicked_lat:.4f}, {clicked_lon:.4f}")
            st.dataframe(nearby[["name", "type", "strasse", "trinkwasser", "status"]].head(10), use_container_width=True)

# ─── Tab 2: Density Map ───────────────────────────────────────
with tab_density:
    st.subheader("Fountain Density Heatmap")
    st.caption("Kernel density estimate showing fountain concentration across Zürich")

    if len(filtered_df) > 0:
        # Create density map
        m_density = folium.Map(
            location=[stats["lat_center"], stats["lon_center"]],
            zoom_start=13,
            tiles="OpenStreetMap",
        )

        # Heatmap layer
        heat_data = filtered_df[["lat", "lon"]].values.tolist()
        plugins.HeatMap(
            heat_data,
            radius=25,
            blur=15,
            max_zoom=17,
            name="Fountain Density",
        ).add_to(m_density)

        # Also add markers for reference
        for _, row in filtered_df.head(200).iterrows():  # Limit for performance
            folium.CircleMarker(
                location=[row["lat"], row["lon"]],
                radius=2,
                color="gray",
                fill=True,
                fill_color="gray",
                opacity=0.3,
            ).add_to(m_density)

        # Add legend
        legend_html = """
        <div style="
            position: fixed;
            bottom: 30px;
            left: 30px;
            z-index: 1000;
            background-color: white;
            padding: 10px 15px;
            border: 2px solid grey;
            border-radius: 5px;
            font-size: 12px;
        ">
            <b>Density Legend</b><br>
            🟥 High density<br>
            🟧 Medium density<br>
            🟨 Low density<br>
        </div>
        """
        m_density.get_root().html.add_child(folium.Element(legend_html))

        density_data = st_folium(m_density, width=1200, height=600, key="density_map")

        # Show density statistics
        st.markdown("### Density by Area")
        # Create a simple grid-based density analysis
        lat_bins = pd.cut(filtered_df["lat"], bins=10)
        lon_bins = pd.cut(filtered_df["lon"], bins=10)
        density_grid = filtered_df.groupby([lat_bins, lon_bins]).size().unstack(fill_value=0)

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Density by Latitude Band**")
            lat_density = filtered_df.groupby(lat_bins).size().sort_index()
            st.bar_chart(lat_density)
        with col2:
            st.markdown("**Density by Longitude Band**")
            lon_density = filtered_df.groupby(lon_bins).size().sort_index()
            st.bar_chart(lon_density)
    else:
        st.info("No fountains match the current filters. Try adjusting the filters.")

# ─── Tab 3: Statistics ────────────────────────────────────────
with tab_stats:
    st.subheader("Fountain Statistics")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### Fountain Types")
        if "type" in filtered_df.columns and filtered_df["type"].notna().any():
            type_counts = filtered_df["type"].value_counts().head(15)
            st.bar_chart(type_counts)
            st.dataframe(type_counts.reset_index(), use_container_width=True)

    with col2:
        st.markdown("### Fountain Status")
        if "status" in filtered_df.columns and filtered_df["status"].notna().any():
            status_counts = filtered_df["status"].value_counts().head(15)
            st.bar_chart(status_counts)
            st.dataframe(status_counts.reset_index(), use_container_width=True)

    st.divider()

    # Drinking water breakdown
    st.markdown("### Drinking Water Availability")
    if "trinkwasser" in filtered_df.columns:
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            total = len(filtered_df)
            st.metric("Total fountains shown", total)
        with col_b:
            true_vals = ["ja", "yes", "true", "1"]
            drinking = int(filtered_df["trinkwasser"].astype(str).str.lower().isin(true_vals).sum())
            st.metric("With drinking water", f"{drinking:,}")
        with col_c:
            pct = (drinking / total * 100) if total > 0 else 0
            st.metric("Percentage", f"{pct:.1f}%")

        # Show top streets by fountain count
        st.markdown("### Top Streets by Fountain Count")
        if "strasse" in filtered_df.columns:
            street_counts = filtered_df["strasse"].value_counts().dropna().head(15)
            street_counts = street_counts[street_counts.index != ""]
            st.dataframe(
                street_counts.reset_index().rename(columns={"index": "Street", "strasse": "Count"}),
                use_container_width=True,
            )

    # Fountain name search
    st.divider()
    st.markdown("### Search Fountains")
    search_term = st.text_input("Search by name or street", placeholder="e.g., Bahnhof, Paradeplatz")
    if search_term:
        results = filtered_df[
            filtered_df["name"].str.contains(search_term, case=False, na=False)
            | filtered_df["strasse"].str.contains(search_term, case=False, na=False)
        ]
        if len(results) > 0:
            st.dataframe(
                results[["name", "type", "strasse", "lat", "lon", "trinkwasser", "status"]],
                use_container_width=True,
            )
        else:
            st.info("No fountains found matching your search.")

# ─── Tab 4: Route Planner ─────────────────────────────────────
with tab_route:
    st.subheader("🚶 Fountain Walking Route Planner")
    st.caption(
        "Plan a walking route between two addresses in Zürich with fountain stops "
        "approximately every 15 minutes. Routes shorter than ~15 minutes won't have fountain stops."
    )

    col1, col2 = st.columns(2)
    with col1:
        start_address = st.text_input(
            "Start address",
            value="Zürich Hauptbahnhof",
            help="Enter a starting address in Zürich",
        )
    with col2:
        end_address = st.text_input(
            "Destination address",
            value="Zürich Bellevue",
            help="Enter a destination address in Zürich",
        )

    col3, col4 = st.columns(2)
    with col3:
        max_fountains = st.slider("Max fountain stops", min_value=1, max_value=8, value=3)
    with col4:
        min_fountains = st.slider("Min fountain stops", min_value=0, max_value=max_fountains, value=1)

    if st.button("🗺️ Plan Route", type="primary", use_container_width=True):
        with st.spinner("Planning route... This may take a moment."):
            try:
                route = plan_fountain_route(
                    filtered_df,
                    start_address,
                    end_address,
                    max_fountains=max_fountains,
                    min_fountains=min_fountains,
                )

                if "error" in route:
                    st.error(route["error"])
                else:
                    # Summary
                    st.success(
                        f"🎉 Route planned! "
                        f"**{format_distance(route['total_distance_m'])}** in "
                        f"**{format_duration(route['total_duration_min'])}**"
                    )

                    if route.get("note"):
                        st.info(route["note"])

                    # Fountain stops
                    if route.get("fountains_visited"):
                        st.markdown(f"### 💧 Fountain Stops ({len(route['fountains_visited'])})")
                        for i, fountain_name in enumerate(route["fountains_visited"], 1):
                            fountain_info = filtered_df[filtered_df["name"] == fountain_name]
                            if len(fountain_info) > 0:
                                fi = fountain_info.iloc[0]
                                st.markdown(
                                    f"**{i}. {fi['name']}** "
                                    f"({fi.get('type', '')}) "
                                    f"💧{'Yes' if str(fi.get('trinkwasser', '')).lower() in ('ja', 'yes', 'true', '1') else 'No'}"
                                )

                    # Route segments
                    st.markdown("### Route Segments")
                    segments_data = []
                    for seg in route.get("segments", []):
                        seg_type = "🚶 Walk"
                        if seg.get("fountain"):
                            seg_type = "💧 Fountain Stop"
                        segments_data.append({
                            "segment": seg_type,
                            "from": seg.get("start", {}).get("address", ""),
                            "to": seg.get("end", {}).get("address", ""),
                            "distance": format_distance(seg.get("distance_m", 0)),
                            "duration": format_duration(seg.get("duration_min", 0)),
                        })

                    seg_df = pd.DataFrame(segments_data)
                    st.dataframe(seg_df, use_container_width=True)

                    # Visualize route on map
                    st.markdown("### Route Map")
                    if route.get("route_coords"):
                        route_map = folium.Map(
                            location=[stats["lat_center"], stats["lon_center"]],
                            zoom_start=14,
                            tiles="OpenStreetMap",
                        )

                        # Draw route line
                        if route.get("route_coords"):
                            folium.PolyLine(
                                locations=[(lat, lon) for lon, lat in route["route_coords"]],
                                color="blue",
                                weight=4,
                                opacity=0.8,
                                tooltip="Walking route",
                            ).add_to(route_map)

                        # Add start marker
                        if route.get("start_coords"):
                            folium.Marker(
                                location=list(route["start_coords"]),
                                popup=f"🏁 Start: {start_address}",
                                icon=folium.Icon(color="green", icon="play"),
                            ).add_to(route_map)

                        # Add end marker
                        if route.get("end_coords"):
                            folium.Marker(
                                location=list(route["end_coords"]),
                                popup=f"🏁 End: {end_address}",
                                icon=folium.Icon(color="red", icon="stop"),
                            ).add_to(route_map)

                        # Add fountain markers
                        for seg in route.get("segments", []):
                            if seg.get("fountain"):
                                folium.Marker(
                                    location=[seg["fountain"]["lat"], seg["fountain"]["lon"]],
                                    popup=f"💧 {seg['fountain']['name']}",
                                    icon=folium.Icon(color="orange", icon="tint"),
                                ).add_to(route_map)

                        route_map_data = st_folium(route_map, width=1200, height=500, key="route_map")

            except Exception as e:
                st.error(f"Error planning route: {e}")
                import traceback
                st.code(traceback.format_exc())

    # Pre-filled example routes
    st.divider()
    st.markdown("### 💡 Example Routes")
    examples = [
        ("Zürich Hauptbahnhof", "Zürich Bellevue", "Central station to Bellevue"),
        ("Zürich Hauptbahnhof", "Limmatquai 2", "Station to river promenade"),
        ("Bahnhofstrasse 1", "Zürich Enge", "Shopping street to lake district"),
        ("Zürich Bellevue", "Rathaus Zürich", "Bellevue to City Hall"),
    ]
    for start, end, desc in examples:
        if st.button(desc, key=f"example_{start}_{end}", use_container_width=False):
            st.session_state["start_addr"] = start
            st.session_state["end_addr"] = end
            st.rerun()
