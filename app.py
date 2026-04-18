import streamlit as st
import pandas as pd
import numpy as np
from shapely.geometry import Point, Polygon
from lxml import etree
import zipfile
import io
import re

# ─── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Vendor Site Geo Filter",
    page_icon="📍",
    layout="wide",
)

# ─── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main { padding-top: 1.5rem; }
    .metric-card {
        background: #f8f9fa;
        border-radius: 10px;
        padding: 1rem 1.25rem;
        text-align: center;
    }
    .metric-num { font-size: 2rem; font-weight: 600; }
    .metric-lbl { font-size: 0.75rem; color: #888; margin-top: 2px; }
    .stDataFrame { border-radius: 8px; }
    div[data-testid="stSidebarContent"] { padding-top: 1.5rem; }
</style>
""", unsafe_allow_html=True)

# ─── KML / KMZ Parser ──────────────────────────────────────────────────────────
def parse_kml_bytes(raw: bytes, filename: str) -> list[dict]:
    """Extract named polygons from KML or KMZ bytes."""
    if filename.lower().endswith(".kmz"):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            kml_name = next(n for n in z.namelist() if n.endswith(".kml"))
            raw = z.read(kml_name)

    root = etree.fromstring(raw)
    ns = {"kml": "http://www.opengis.net/kml/2.2"}

    # Try with namespace first, fallback without
    placemarks = root.findall(".//kml:Placemark", ns)
    if not placemarks:
        placemarks = root.findall(".//Placemark")

    polygons = []
    for pm in placemarks:
        # Get name
        name_el = pm.find("kml:name", ns) or pm.find("name")
        name = name_el.text.strip() if name_el is not None and name_el.text else "Unnamed"

        # Find all coordinate blocks under Polygon
        coord_els = pm.findall(".//kml:Polygon//kml:coordinates", ns)
        if not coord_els:
            coord_els = pm.findall(".//Polygon//coordinates")
        if not coord_els:
            coord_els = pm.findall(".//kml:coordinates", ns)
        if not coord_els:
            coord_els = pm.findall(".//coordinates")

        for cel in coord_els:
            raw_coords = cel.text.strip() if cel.text else ""
            pts = []
            for token in re.split(r"\s+", raw_coords):
                parts = token.split(",")
                if len(parts) >= 2:
                    try:
                        lng, lat = float(parts[0]), float(parts[1])
                        pts.append((lng, lat))
                    except ValueError:
                        continue
            if len(pts) >= 3:
                polygons.append({"name": name, "polygon": Polygon(pts)})

    return polygons


# ─── Coordinate Parser ─────────────────────────────────────────────────────────
def parse_lat_lng(val) -> tuple[float, float] | None:
    """Parse 'lat, lng' string or return None."""
    if pd.isna(val) or str(val).strip() == "":
        return None
    parts = str(val).split(",")
    if len(parts) >= 2:
        try:
            return float(parts[0].strip()), float(parts[1].strip())
        except ValueError:
            return None
    return None


# ─── Geo Filter Core ───────────────────────────────────────────────────────────
def run_geo_filter(df: pd.DataFrame, geo_col: str, polygons: list[dict], buffer_m: float = 0) -> pd.DataFrame:
    """Tag each row with its NM match status."""
    # Approx degrees per meter at India latitudes (~28°N)
    buffer_deg = buffer_m / 111_000 if buffer_m > 0 else 0

    statuses, matched_nms = [], []

    for _, row in df.iterrows():
        coords = parse_lat_lng(row.get(geo_col, ""))
        if coords is None:
            statuses.append("No coordinates")
            matched_nms.append("—")
            continue

        lat, lng = coords
        pt = Point(lng, lat)  # shapely uses (x=lng, y=lat)

        match = None
        for poly_info in polygons:
            check_poly = poly_info["polygon"].buffer(buffer_deg) if buffer_deg else poly_info["polygon"]
            if check_poly.contains(pt):
                match = poly_info["name"]
                break

        if match:
            statuses.append("Inside NM")
            matched_nms.append(match)
        else:
            statuses.append("Outside NM")
            matched_nms.append("—")

    result = df.copy()
    result.insert(0, "Status", statuses)
    result.insert(1, "Matched NM", matched_nms)
    return result


# ─── UI ────────────────────────────────────────────────────────────────────────
st.title("📍 Vendor Site Geo Filter")
st.caption("Upload your NM boundary KML and vendor inventory to instantly tag sites as Inside / Outside Nano Markets.")

st.divider()

# Sidebar — settings
with st.sidebar:
    st.header("Settings")

    geo_col_input = st.text_input(
        "Geo location column name",
        value="Geo Location",
        help="Column in your vendor file that contains 'lat, lng' coordinates"
    )

    buffer_m = st.slider(
        "Buffer zone (meters)",
        min_value=0, max_value=1000, value=0, step=50,
        help="Expand NM boundaries by this distance. Use if sites just outside the boundary are still acceptable."
    )

    st.divider()
    st.caption("Built for Offline Media Operations · State Pilot v1")

# ─── File Upload ───────────────────────────────────────────────────────────────
col1, col2 = st.columns(2)

with col1:
    st.subheader("Step 1 — NM Boundaries")
    kml_file = st.file_uploader("Upload KML / KMZ", type=["kml", "kmz"], key="kml")

with col2:
    st.subheader("Step 2 — Vendor Inventory")
    vendor_file = st.file_uploader("Upload Excel / CSV", type=["xlsx", "csv"], key="vendor")

# ─── Processing ────────────────────────────────────────────────────────────────
if kml_file and vendor_file:
    # Parse KML
    with st.spinner("Parsing KML boundaries..."):
        try:
            polygons = parse_kml_bytes(kml_file.read(), kml_file.name)
        except Exception as e:
            st.error(f"Could not parse KML: {e}")
            st.stop()

    if not polygons:
        st.error("No polygons found in the KML file. Make sure it contains Placemark → Polygon elements.")
        st.stop()

    st.success(f"Loaded **{len(polygons)} NM boundaries** from KML: {', '.join(set(p['name'] for p in polygons[:5]))}{'...' if len(polygons) > 5 else ''}")

    # Parse vendor file
    with st.spinner("Reading vendor inventory..."):
        try:
            if vendor_file.name.endswith(".csv"):
                df = pd.read_csv(vendor_file)
            else:
                # Try to find the data sheet (skip first summary sheet if present)
                xls = pd.ExcelFile(vendor_file)
                sheet = xls.sheet_names[-1] if len(xls.sheet_names) > 1 else xls.sheet_names[0]
                df = pd.read_excel(vendor_file, sheet_name=sheet)
                # Drop rows where the geo col is missing / header rows
                if geo_col_input in df.columns:
                    df = df[df[geo_col_input].notna()].reset_index(drop=True)
        except Exception as e:
            st.error(f"Could not read vendor file: {e}")
            st.stop()

    # Validate geo column
    if geo_col_input not in df.columns:
        available = ", ".join(df.columns.tolist())
        st.error(f"Column **'{geo_col_input}'** not found. Available columns: {available}")
        st.stop()

    # Run filter
    with st.spinner("Running geo filter..."):
        result_df = run_geo_filter(df, geo_col_input, polygons, buffer_m)

    # ─── Summary Stats ─────────────────────────────────────────────────────────
    st.divider()
    total     = len(result_df)
    inside    = (result_df["Status"] == "Inside NM").sum()
    outside   = (result_df["Status"] == "Outside NM").sum()
    no_coords = (result_df["Status"] == "No coordinates").sum()
    pct_in    = round(inside / total * 100) if total else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total sites", total)
    m2.metric("Inside NM ✅", inside, f"{pct_in}%")
    m3.metric("Outside NM ❌", outside)
    m4.metric("Missing coords ⚠️", no_coords)

    # Households inside NM
    if "Household Count" in result_df.columns:
        hh_inside = result_df[result_df["Status"] == "Inside NM"]["Household Count"].sum()
        hh_total  = result_df["Household Count"].sum()
        st.caption(f"Households reachable inside NMs: **{int(hh_inside):,}** of {int(hh_total):,} total ({round(hh_inside/hh_total*100) if hh_total else 0}%)")

    st.divider()

    # ─── Table with filters ────────────────────────────────────────────────────
    st.subheader("Results")

    tab1, tab2, tab3 = st.tabs(["All sites", "Inside NM ✅", "Outside NM ❌"])

    display_cols = ["Status", "Matched NM"] + [
        c for c in ["Media Site ID", "RWA Name", "Areas", "Package Names",
                    "Geo Location", "Household Count", "No of impressions per month"]
        if c in result_df.columns
    ]

    def styled_df(data):
        def color_status(val):
            if val == "Inside NM":   return "background-color: #d4edda; color: #155724;"
            if val == "Outside NM":  return "background-color: #f8d7da; color: #721c24;"
            return "background-color: #fff3cd; color: #856404;"
        try:
            return data.style.map(color_status, subset=["Status"])
        except AttributeError:
            return data.style.applymap(color_status, subset=["Status"])

    with tab1:
        st.dataframe(styled_df(result_df[display_cols]), use_container_width=True, height=420)

    with tab2:
        inside_df = result_df[result_df["Status"] == "Inside NM"][display_cols]
        st.dataframe(styled_df(inside_df), use_container_width=True, height=420)

    with tab3:
        outside_df = result_df[result_df["Status"] == "Outside NM"][display_cols]
        st.dataframe(styled_df(outside_df), use_container_width=True, height=420)

    # ─── NM Breakdown ─────────────────────────────────────────────────────────
    if inside > 0:
        st.divider()
        st.subheader("Breakdown by Nano Market")
        nm_summary = (
            result_df[result_df["Status"] == "Inside NM"]
            .groupby("Matched NM")
            .agg(
                Sites=("Status", "count"),
                **({ "Households": ("Household Count", "sum") } if "Household Count" in result_df.columns else {}),
                **({ "Impressions/month": ("No of impressions per month", "sum") } if "No of impressions per month" in result_df.columns else {}),
            )
            .sort_values("Sites", ascending=False)
            .reset_index()
        )
        st.dataframe(nm_summary, use_container_width=True)

    # ─── Export ────────────────────────────────────────────────────────────────
    st.divider()
    col_a, col_b, col_c = st.columns([2, 2, 4])

    with col_a:
        csv_all = result_df.to_csv(index=False).encode("utf-8")
        st.download_button("⬇ Download all (CSV)", csv_all, "vendor_geo_filtered_all.csv", "text/csv")

    with col_b:
        csv_in = result_df[result_df["Status"] == "Inside NM"].to_csv(index=False).encode("utf-8")
        st.download_button("⬇ Download Inside NM only", csv_in, "vendor_inside_nm.csv", "text/csv")

else:
    st.info("Upload both files above to begin.")
    with st.expander("What does this tool do?"):
        st.markdown("""
        1. **KML / KMZ** — Your Nano Market boundary polygons drawn in Google Maps / My Maps
        2. **Vendor Excel / CSV** — The site list from your vendor containing a `Geo Location` column with `lat, lng` coordinates
        3. The tool checks every site coordinate against every NM polygon and tags it:
           - ✅ **Inside NM** — with the matched NM name
           - ❌ **Outside NM** — not useful for your campaigns
           - ⚠️ **No coordinates** — missing data, needs follow-up with vendor
        4. Export a clean, tagged CSV to share with your team or send back to the vendor
        """)
