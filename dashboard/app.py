"""Philadelphia crime incidents dashboard (2020-2025).

Reads only the small aggregate files in dashboard/data, which the ETL writes
after every full run, so it deploys to Streamlit Community Cloud without the
warehouse database. Run locally with:  streamlit run dashboard/app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

DATA = Path(__file__).parent / "data"

# Reference palette (validated for color-vision deficiency): three categorical
# slots for the three offense categories, and a single-hue blue ramp for magnitude.
CATEGORY_COLORS = {"Violent": "#2a78d6", "Property": "#eb6834", "Other": "#1baf7a"}
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
INK, MUTED, GRID = "#0b0b0b", "#898781", "#e1e0d9"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

st.set_page_config(page_title="Philadelphia Crime 2020-2025", layout="wide")


@st.cache_data
def load() -> dict[str, pd.DataFrame]:
    frames = {p.stem: pd.read_parquet(p) for p in DATA.glob("*.parquet")}
    for name in ("monthly", "heatmap", "grid", "districts", "hotspots"):
        df = frames[name]
        for col in df.columns:
            if isinstance(df[col].dtype, pd.CategoricalDtype):
                df[col] = df[col].astype(str)
    frames["monthly"]["month_start"] = pd.to_datetime(frames["monthly"]["month_start"])
    return frames


@st.cache_data
def load_geojson() -> dict:
    return json.loads((DATA / "police_districts.geojson").read_text(encoding="utf-8"))


def summary() -> dict:
    path = DATA / "pipeline_summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def style(fig: go.Figure, height: int = 360) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=INK, size=13),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, title=None),
        hoverlabel=dict(bgcolor="white"),
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, tickfont=dict(color=MUTED))
    return fig


data = load()
monthly, heatmap, grid = data["monthly"], data["heatmap"], data["grid"]
districts, hotspots = data["districts"], data["hotspots"]
district_names = dict(zip(districts["district_code"], districts["district_name"], strict=False))

# ---------------------------------------------------------------------------
# Filters (one row, above every chart)
# ---------------------------------------------------------------------------
st.title("Philadelphia crime incidents, 2020-2025")
st.caption(
    "Source: OpenDataPhilly / Philadelphia Police Department, incidents_part1_part2 "
    "(Carto SQL API). Built by a Python ETL into a SQLite star schema."
)

years = sorted(monthly["year"].unique().tolist())
groups = sorted(monthly["ucr_group"].unique().tolist())
codes = sorted(monthly["district_code"].unique().tolist())

f1, f2, f3, f4 = st.columns([1.2, 1, 2, 1.6])
year_range = f1.select_slider("Years", options=years, value=(years[0], years[-1]))
cats = f2.multiselect("Category", list(CATEGORY_COLORS), default=list(CATEGORY_COLORS))
picked_groups = f3.multiselect("Offense group", groups, placeholder="All offense groups")
picked_codes = f4.multiselect(
    "Police district",
    codes,
    format_func=lambda c: district_names.get(c, c),
    placeholder="All districts",
)


def apply(df: pd.DataFrame, with_years: bool = True) -> pd.DataFrame:
    m = pd.Series(True, index=df.index)
    if with_years:
        m &= df["year"].between(*year_range)
    if "category" in df:
        m &= df["category"].isin(cats)
    else:
        allowed = monthly.loc[monthly["category"].isin(cats), "ucr_group"].unique()
        m &= df["ucr_group"].isin(allowed)
    if picked_groups:
        m &= df["ucr_group"].isin(picked_groups)
    if picked_codes:
        m &= df["district_code"].isin(picked_codes)
    return df[m]


sel = apply(monthly)
if sel.empty:
    st.warning("No incidents match these filters.")
    st.stop()

# ---------------------------------------------------------------------------
# KPI cards
# ---------------------------------------------------------------------------
last_year = year_range[1]
all_years = apply(monthly, with_years=False)
cur = all_years.loc[all_years["year"] == last_year, "incidents"].sum()
prev = all_years.loc[all_years["year"] == last_year - 1, "incidents"].sum()
total = int(sel["incidents"].sum())
violent = int(sel.loc[sel["category"] == "Violent", "incidents"].sum())
firearm = int(sel["firearm_incidents"].sum())

k1, k2, k3, k4 = st.columns(4)
k1.metric("Incidents in selection", f"{total:,}")
k2.metric(
    f"{last_year} vs {last_year - 1}",
    f"{int(cur):,}",
    f"{(cur - prev) / prev * 100:+.1f}%" if prev else "n/a",
    delta_color="inverse",
)
k3.metric("Violent share", f"{violent / total * 100:.1f}%" if total else "n/a")
k4.metric("Offenses involving a firearm", f"{firearm:,}")

# ---------------------------------------------------------------------------
# Trend + district comparison
# ---------------------------------------------------------------------------
left, right = st.columns([3, 2])
with left:
    st.subheader("Monthly incidents by category")
    trend = sel.groupby(["month_start", "category"], as_index=False)["incidents"].sum()
    fig = px.line(
        trend,
        x="month_start",
        y="incidents",
        color="category",
        color_discrete_map=CATEGORY_COLORS,
        category_orders={"category": list(CATEGORY_COLORS)},
        labels={"month_start": "", "incidents": "Incidents per month"},
    )
    fig.update_traces(line=dict(width=2), hovertemplate="%{x|%b %Y}: %{y:,}<extra></extra>")
    fig.update_layout(hovermode="x unified")
    st.plotly_chart(style(fig), use_container_width=True)

with right:
    st.subheader("Incidents by district")
    per_area = st.toggle("Per square mile", value=False)
    by_d = sel.groupby("district_code", as_index=False)["incidents"].sum()
    by_d = by_d.merge(districts, on="district_code", how="left")
    by_d["value"] = by_d["incidents"] / by_d["area_sq_mi"] if per_area else by_d["incidents"]
    by_d = by_d.dropna(subset=["value"]).sort_values("value")
    fig = px.bar(
        by_d,
        x="value",
        y="district_name",
        orientation="h",
        labels={"value": "Incidents per sq mi" if per_area else "Incidents", "district_name": ""},
    )
    fig.update_traces(
        marker_color=CATEGORY_COLORS["Violent"], hovertemplate="%{y}: %{x:,.0f}<extra></extra>"
    )
    st.plotly_chart(style(fig, 420), use_container_width=True)
    if per_area:
        st.caption("District 06 has no boundary in the current map, so it has no area.")

# ---------------------------------------------------------------------------
# Heatmap + hotspot map
# ---------------------------------------------------------------------------
left, right = st.columns([2, 3])
with left:
    st.subheader("When incidents happen")
    h = apply(heatmap).groupby(["iso_dow", "hour"], as_index=False)["incidents"].sum()
    pivot = (
        h.pivot(index="iso_dow", columns="hour", values="incidents")
        .reindex(index=range(1, 8), columns=range(24))
        .fillna(0)
    )
    fig = go.Figure(
        go.Heatmap(
            z=pivot.values,
            x=[f"{x:02d}" for x in pivot.columns],
            y=DAYS,
            colorscale=BLUE_RAMP,
            xgap=2,
            ygap=2,
            hovertemplate="%{y} %{x}:00<br>%{z:,} incidents<extra></extra>",
            colorbar=dict(title="", thickness=10),
        )
    )
    fig.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)")
    fig.update_xaxes(title="Hour of day", dtick=3)
    st.plotly_chart(style(fig, 380), use_container_width=True)

with right:
    st.subheader("Hotspot map (550 m grid cells)")
    g = apply(grid).groupby(["grid_id", "lat", "lon"], as_index=False)["incidents"].sum()
    g = g.sort_values("incidents")
    fig = px.scatter_map(
        g,
        lat="lat",
        lon="lon",
        color="incidents",
        size="incidents",
        size_max=14,
        color_continuous_scale=BLUE_RAMP,
        zoom=9.9,
        center={"lat": 40.0, "lon": -75.13},
        map_style="carto-positron",
        hover_data={"lat": False, "lon": False, "incidents": ":,"},
    )
    fig.update_layout(
        map_layers=[
            {"source": load_geojson(), "type": "line", "color": "#52514e", "line": {"width": 1}}
        ],
        coloraxis_colorbar=dict(title="", thickness=10),
    )
    st.plotly_chart(style(fig, 380), use_container_width=True)

# ---------------------------------------------------------------------------
# Repeat-location hotspots
# ---------------------------------------------------------------------------
st.subheader("Blocks with incidents in every year, 2020-2025")
st.caption("Top 50 street blocks by total incidents (all offenses, not affected by filters).")
st.dataframe(
    hotspots[
        [
            "hotspot_rank",
            "location_block",
            "district_code",
            "incidents",
            "months_present",
            "most_common_offense",
        ]
    ].rename(
        columns={
            "hotspot_rank": "Rank",
            "location_block": "Block",
            "district_code": "District",
            "incidents": "Incidents",
            "months_present": "Months with incidents (of 72)",
            "most_common_offense": "Most common offense",
        }
    ),
    hide_index=True,
    use_container_width=True,
    height=300,
)

# ---------------------------------------------------------------------------
# Pipeline and data quality
# ---------------------------------------------------------------------------
s = summary()
if s:
    with st.expander("Pipeline and data-quality summary"):
        c1, c2, c3 = st.columns(3)
        c1.metric("Rows extracted", f"{s['rows_extracted']:,}")
        c2.metric("Rows rejected", f"{s['rows_rejected']:,}")
        c3.metric("Rows loaded", f"{s['rows_loaded']:,}")
        st.write("Rejected by reason:", s.get("rejected_by_reason", {}))
        st.write(
            f"Coordinates nulled (kept, but left off the map): {s.get('coords_nulled', 0):,}. "
            f"All data-quality checks passed: {s.get('all_checks_passed')}."
        )
        st.json(s, expanded=False)
