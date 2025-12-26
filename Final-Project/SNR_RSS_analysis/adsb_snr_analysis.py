#!/usr/bin/env python3
"""
Analyze ADS-B MATLAB log with SNR(dB) column:
- Parse MATLAB .txt (CSV-like)
- Correct MATLAB time to real time using 2 anchors (linear map)
- Backfill missing lat/lon/alt/speed via OpenSky /api/states/all (optional)
- Compute horizontal + slant distance and elevation angle
- Analyze SNR:
  * CRC success vs failure
  * SNR by distance bins
  * SNR by elevation bins
  * Scatter: SNR vs distance and SNR vs elevation
Outputs:
- parsed.csv, enriched.csv
- histograms and binned boxplots as PNG
"""

from __future__ import annotations

import os
import math
import time
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple, Any

import pandas as pd
import requests
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 (needed to register 3D projection)
import numpy as np



# -----------------------------
# Fixed measurement location
# -----------------------------
MEAS_LAT = 25 + 1/60 + 4/3600          # 25°01'04.0"N
MEAS_LON = 121 + 32/60 + 38.9/3600     # 121°32'38.9"E
MEAS_ALT_M = 15.0                      # you are at 15 m above sea level

# -----------------------------
# Time mapping anchors (NEW)
# -----------------------------
# MATLAB time 16:00:53 -> realworld time same
# MATLAB time 16-Dec-2025 16:09:10 -> realworld 16:16:30
ANCHOR_1_MATLAB = "16:00:53"
ANCHOR_1_REAL   = "16:00:53"
ANCHOR_2_MATLAB = "16:09:10"
ANCHOR_2_REAL   = "16:16:30"
# ANCHOR_1_MATLAB = "17:40:11"
# ANCHOR_1_REAL   = "17:40:11"
# ANCHOR_2_MATLAB = "17:48:55"
# ANCHOR_2_REAL   = "17:56:30"


# -----------------------------
# Helpers: geo + parsing
# -----------------------------
def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance (meters)."""
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlmb/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def parse_matlab_timestamp(ts: str) -> datetime:
    """Parse MATLAB log time like '16-Dec-2025 16:00:53'."""
    ts = (ts or "").strip()
    return datetime.strptime(ts, "%d-%b-%Y %H:%M:%S")


def safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        s = str(x).strip()
        if s == "" or s.lower() in {"nan", "none"}:
            return None
        return float(s)
    except Exception:
        return None

def finite_series(x: pd.Series) -> pd.Series:
    x = pd.to_numeric(x, errors="coerce")
    return x.replace([float("inf"), float("-inf")], pd.NA).dropna()


def normalize_icao24(x: Any) -> Optional[str]:
    """Normalize ICAO24 to lowercase 6-hex if possible."""
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    s = s.replace(" ", "").lower()
    if len(s) == 6 and all(c in "0123456789abcdef" for c in s):
        return s
    return None


def msg_len_class(message_hex: Any) -> str:
    """14 hex -> short (56-bit); 28 hex -> extended (112-bit)."""
    if message_hex is None:
        return "unknown"
    s = str(message_hex).strip().replace(" ", "")
    if len(s) == 14:
        return "short"
    if len(s) == 28:
        return "extended"
    return "unknown"


# -----------------------------
# Linear time mapper
# -----------------------------
@dataclass(frozen=True)
class LinearTimeMapper:
    """real_seconds = a * matlab_seconds + b"""
    a: float
    b: float

    @staticmethod
    def from_anchors(date_str: str) -> "LinearTimeMapper":
        m1 = datetime.strptime(f"{date_str} {ANCHOR_1_MATLAB}", "%d-%b-%Y %H:%M:%S")
        r1 = datetime.strptime(f"{date_str} {ANCHOR_1_REAL}",   "%d-%b-%Y %H:%M:%S")
        m2 = datetime.strptime(f"{date_str} {ANCHOR_2_MATLAB}", "%d-%b-%Y %H:%M:%S")
        r2 = datetime.strptime(f"{date_str} {ANCHOR_2_REAL}",   "%d-%b-%Y %H:%M:%S")

        m1s, r1s = m1.timestamp(), r1.timestamp()
        m2s, r2s = m2.timestamp(), r2.timestamp()

        if abs(m2s - m1s) < 1e-6:
            raise ValueError("Anchor MATLAB times identical; cannot build linear map.")

        a = (r2s - r1s) / (m2s - m1s)
        b = r1s - a * m1s
        return LinearTimeMapper(a=a, b=b)

    def map_dt(self, matlab_dt: datetime) -> datetime:
        rs = self.a * matlab_dt.timestamp() + self.b
        return datetime.fromtimestamp(rs)  # naive


# -----------------------------
# OpenSky client (same method)
# -----------------------------
class OpenSkyClient:
    """
    /api/states/all?time=...&icao24=...&extended=1
    NOTE: Unauthenticated users may ignore 'time'. For best results, use credentials.
    """
    BASE = "https://opensky-network.org/api"

    def __init__(self, username: Optional[str], password: Optional[str], timeout_s: int = 20):
        self.auth = (username, password) if (username and password) else None
        self.timeout_s = timeout_s
        self.session = requests.Session()
        self.cache: Dict[Tuple[str, int], Optional[Dict[str, Any]]] = {}

    @staticmethod
    def round_query_time(epoch_s: int, authenticated: bool) -> int:
        return epoch_s - (epoch_s % (5 if authenticated else 10))

    def get_state(self, icao24: str, epoch_s: int) -> Optional[Dict[str, Any]]:
        authenticated = self.auth is not None
        t = self.round_query_time(epoch_s, authenticated)
        key = (icao24, t)
        if key in self.cache:
            return self.cache[key]

        url = f"{self.BASE}/states/all"
        params = {"time": t, "icao24": icao24, "extended": 1}

        try:
            resp = self.session.get(url, params=params, auth=self.auth, timeout=self.timeout_s)
            if resp.status_code != 200:
                self.cache[key] = None
                return None
            data = resp.json()
            states = data.get("states") or []
            if not states:
                self.cache[key] = None
                return None
            row = states[0]
            # indices (OpenSky docs):
            # 5=lon, 6=lat, 7=baro_alt_m, 9=vel_mps, 10=true_track_deg, 11=vert_rate_mps
            out = {
                "lon": row[5] if len(row) > 5 else None,
                "lat": row[6] if len(row) > 6 else None,
                "baro_alt_m": row[7] if len(row) > 7 else None,
                "vel_mps": row[9] if len(row) > 9 else None,
                "true_track_deg": row[10] if len(row) > 10 else None,
                "vert_rate_mps": row[11] if len(row) > 11 else None,
            }
            self.cache[key] = out
            return out
        except Exception:
            self.cache[key] = None
            return None


def m_to_ft(m: Optional[float]) -> Optional[float]:
    if m is None:
        return None
    return float(m) * 3.280839895


def mps_to_kts(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    return float(v) * 1.9438444924406


# -----------------------------
# Main analysis
# -----------------------------
def analyze(
    input_txt: str,
    out_dir: str,
    use_opensky: bool,
    opensky_user: Optional[str],
    opensky_pass: Optional[str],
    max_api_calls: int = 5000,
) -> None:
    os.makedirs(out_dir, exist_ok=True)

    # Read CSV-like log (handles the spaces after commas)
    df = pd.read_csv(input_txt, skipinitialspace=True)

    # Standardize key columns
    df["Message_str"] = df["Message"].astype(str).str.strip().str.replace(" ", "", regex=False)
    df["SquitterType"] = df["Message_str"].apply(msg_len_class)

    df["CRC"] = pd.to_numeric(df["CRC"], errors="coerce")
    df["DF"]  = pd.to_numeric(df["DF"], errors="coerce")
    df["TC"]  = pd.to_numeric(df["TC"], errors="coerce")

    df["ICAO24_norm"] = df["ICAO24"].apply(normalize_icao24)

    # Parse SNR(dB) (new)
    if "SNR(dB)" not in df.columns:
        raise ValueError("Column 'SNR(dB)' not found in this file.")
    df["SNR_dB"] = df["SNR(dB)"].apply(safe_float)

    # Parse MATLAB timestamps
    df["MatlabDT"] = df["Time"].apply(parse_matlab_timestamp)
    df["DateStr"] = df["MatlabDT"].dt.strftime("%d-%b-%Y")

    # Build per-date time mapper (in case file spans multiple days)
    mappers = {d: LinearTimeMapper.from_anchors(d) for d in df["DateStr"].unique()}

    def map_real(dt: datetime) -> datetime:
        d = dt.strftime("%d-%b-%Y")
        return mappers[d].map_dt(dt)

    df["RealDT"] = df["MatlabDT"].apply(map_real)

    # Epoch seconds for OpenSky (treated as UTC-like timestamp for querying)
    df["RealEpoch"] = df["RealDT"].apply(lambda x: int(x.replace(tzinfo=timezone.utc).timestamp()))

    # Pull numeric fields from log
    df["Lat_log"] = df["Latitude"].apply(safe_float)
    df["Lon_log"] = df["Longitude"].apply(safe_float)
    df["Alt_log"] = df["Altitude"].apply(safe_float)  # assumed feet in this log
    df["Spd_log"] = df["Speed"].apply(safe_float)     # assumed knots in this log

    # Initialize enriched values
    df["Lat"] = df["Lat_log"]
    df["Lon"] = df["Lon_log"]
    df["Alt_ft"] = df["Alt_log"]
    df["Spd_kts"] = df["Spd_log"]

    # Backfill missing lat/lon/alt/speed via OpenSky (same approach as old code)
    client = OpenSkyClient(opensky_user, opensky_pass) if use_opensky else None
    api_calls = 0

    def needs_fill(row) -> bool:
        if row["ICAO24_norm"] is None:
            return False
        lat, lon = row["Lat"], row["Lon"]
        alt, spd = row["Alt_ft"], row["Spd_kts"]
        missing_pos = (lat is None or lon is None or (abs(lat) < 1e-9 and abs(lon) < 1e-9))
        missing_alt = (alt is None or alt == 0)
        missing_spd = (spd is None or spd == 0)
        return missing_pos or missing_alt or missing_spd

    if use_opensky and client is not None:
        for idx, row in df.iterrows():
            if api_calls >= max_api_calls:
                break
            if not needs_fill(row):
                continue

            icao = row["ICAO24_norm"]
            t = int(row["RealEpoch"])
            st = client.get_state(icao, t)
            api_calls += 1
            if not st:
                continue

            # Fill lat/lon
            if row["Lat"] is None or row["Lon"] is None or (abs(row["Lat"]) < 1e-9 and abs(row["Lon"]) < 1e-9):
                if st.get("lat") is not None and st.get("lon") is not None:
                    df.at[idx, "Lat"] = float(st["lat"])
                    df.at[idx, "Lon"] = float(st["lon"])

            # Fill altitude (m -> ft)
            if row["Alt_ft"] is None or row["Alt_ft"] == 0:
                alt_ft = m_to_ft(st.get("baro_alt_m"))
                if alt_ft is not None:
                    df.at[idx, "Alt_ft"] = float(alt_ft)

            # Fill speed (m/s -> kts)
            if row["Spd_kts"] is None or row["Spd_kts"] == 0:
                spd_kts = mps_to_kts(st.get("vel_mps"))
                if spd_kts is not None:
                    df.at[idx, "Spd_kts"] = float(spd_kts)

            time.sleep(0.05)

    # Compute horizontal distance, slant distance, elevation angle
    def compute_geometry(row) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        lat, lon = row["Lat"], row["Lon"]
        if lat is None or lon is None or (abs(lat) < 1e-9 and abs(lon) < 1e-9):
            return None, None, None, None

        horiz_m = haversine_m(MEAS_LAT, MEAS_LON, float(lat), float(lon))

        alt_ft = row["Alt_ft"]
        if alt_ft is None or alt_ft == 0:
            return horiz_m, None, None, None

        aircraft_alt_m = float(alt_ft) / 3.280839895
        dh_m = aircraft_alt_m - MEAS_ALT_M
        slant_m = math.sqrt(horiz_m*horiz_m + dh_m*dh_m)
        elev_deg = math.degrees(math.atan2(dh_m, horiz_m)) if horiz_m > 0 else (90.0 if dh_m > 0 else -90.0)

        return horiz_m, slant_m, elev_deg, aircraft_alt_m

    geom = df.apply(compute_geometry, axis=1, result_type="expand")
    df["DistHoriz_m"] = geom[0]
    df["DistSlant_m"] = geom[1]
    df["Elev_deg"] = geom[2]
    df["Alt_m"] = geom[3]
    df["HeightAboveRx_m"] = df["Alt_m"] - MEAS_ALT_M
    df["Alt_km"] = df["Alt_m"] / 1000.0
    df["DistSlant_km"] = df["DistSlant_m"] / 1000.0

    # Save outputs
    parsed_csv = os.path.join(out_dir, "parsed.csv")
    enriched_csv = os.path.join(out_dir, "enriched.csv")
    df.to_csv(parsed_csv, index=False)
    df.to_csv(enriched_csv, index=False)

    # -----------------------------
    # Plotting helpers
    # -----------------------------
    def plot_hist(series: pd.Series, title: str, xlabel: str, out_name: str, bins: int = 60):
      # Keep only finite numbers (drop NaN, inf, -inf)
      s = pd.to_numeric(series, errors="coerce")
      s = s.replace([float("inf"), float("-inf")], pd.NA).dropna()
      if len(s) == 0:
          return
      plt.figure()
      plt.hist(s, bins=bins)
      plt.title(title)
      plt.xlabel(xlabel)
      plt.ylabel("Count")
      plt.tight_layout()
      plt.savefig(os.path.join(out_dir, out_name), dpi=160)
      plt.close()


    def plot_scatter(x: pd.Series, y: pd.Series, title: str, xlabel: str, ylabel: str, out_name: str):
        d = pd.DataFrame({"x": x, "y": y}).dropna()
        if len(d) == 0:
            return
        plt.figure()
        plt.scatter(d["x"], d["y"], s=6)
        plt.title(title)
        plt.xlabel(xlabel)
        plt.ylabel(ylabel)
        plt.tight_layout()
        plt.savefig(os.path.join(out_dir, out_name), dpi=160)
        plt.close()

    def plot_binned_box(df_in: pd.DataFrame, value_col: str, bin_col: str, title: str, xlabel: str, ylabel: str, out_name: str):
        tmp = df_in[[value_col, bin_col]].dropna()
        if len(tmp) == 0:
            return
        groups = []
        labels = []
        for k, g in tmp.groupby(bin_col):
            vals = g[value_col].dropna().values
            if len(vals) < 5:
                continue
            groups.append(vals)
            labels.append(str(k))
        if not groups:
            return
        plt.figure(figsize=(max(8, 0.7 * len(groups)), 5))
        plt.boxplot(groups, labels=labels, showfliers=False)
        plt.title(title)
        plt.xlabel(xlabel)
        plt.ylabel(ylabel)
        plt.xticks(rotation=30, ha="right")
        plt.tight_layout()
        plt.savefig(os.path.join(out_dir, out_name), dpi=160)
        plt.close()
    
    def plot_3d_scatter(x: pd.Series, y: pd.Series, z: pd.Series,
                    title: str, xlabel: str, ylabel: str, zlabel: str,
                    out_name: str):
        d = pd.DataFrame({"x": x, "y": y, "z": z})
        # keep only finite numeric values
        d["x"] = pd.to_numeric(d["x"], errors="coerce")
        d["y"] = pd.to_numeric(d["y"], errors="coerce")
        d["z"] = pd.to_numeric(d["z"], errors="coerce")
        d = d.replace([float("inf"), float("-inf")], pd.NA).dropna()
        if len(d) == 0:
            return

        fig = plt.figure()
        ax = fig.add_subplot(111, projection="3d")
        ax.scatter(d["x"], d["y"], d["z"], s=6)
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_zlabel(zlabel)
        plt.tight_layout()
        plt.savefig(os.path.join(out_dir, out_name), dpi=180)
        plt.close()

    def plot_binned_surface_3d(
        df_in: pd.DataFrame,
        x_col: str,
        y_col: str,
        z_col: str,
        x_bins: list,
        y_bins: list,
        agg: str,
        title: str,
        xlabel: str,
        ylabel: str,
        zlabel: str,
        out_name_3d: str,
        out_name_2d: str,
        min_count_per_cell: int = 5,
    ):
        """
        Bin (x_col, y_col) into x_bins and y_bins; aggregate z_col per cell (median/mean).
        Plot as:
        - 3D surface (cell centers)
        - 2D heatmap (imshow)
        Cells with < min_count_per_cell samples are masked as NaN.
        """
        tmp = df_in[[x_col, y_col, z_col]].copy()
        tmp[x_col] = pd.to_numeric(tmp[x_col], errors="coerce")
        tmp[y_col] = pd.to_numeric(tmp[y_col], errors="coerce")
        tmp[z_col] = pd.to_numeric(tmp[z_col], errors="coerce")
        tmp = tmp.replace([float("inf"), float("-inf")], pd.NA).dropna()

        if len(tmp) == 0:
            return

        tmp["x_bin"] = pd.cut(tmp[x_col], bins=x_bins, include_lowest=True)
        tmp["y_bin"] = pd.cut(tmp[y_col], bins=y_bins, include_lowest=True)

        # aggregate
        if agg not in {"median", "mean"}:
            raise ValueError("agg must be 'median' or 'mean'")

        gb = tmp.groupby(["x_bin", "y_bin"])[z_col]
        if agg == "median":
            z_agg = gb.median()
        else:
            z_agg = gb.mean()
        counts = gb.size()

        # Build grid (rows: y bins, cols: x bins)
        x_intervals = pd.IntervalIndex(pd.cut(pd.Series([0]), bins=x_bins, include_lowest=True).cat.categories)
        y_intervals = pd.IntervalIndex(pd.cut(pd.Series([0]), bins=y_bins, include_lowest=True).cat.categories)

        # cell centers
        x_centers = np.array([(iv.left + iv.right) / 2.0 for iv in x_intervals], dtype=float)
        y_centers = np.array([(iv.left + iv.right) / 2.0 for iv in y_intervals], dtype=float)

        Z = np.full((len(y_intervals), len(x_intervals)), np.nan, dtype=float)

        # Fill grid
        z_agg = z_agg.reindex(pd.MultiIndex.from_product([x_intervals, y_intervals]), fill_value=np.nan)
        counts = counts.reindex(pd.MultiIndex.from_product([x_intervals, y_intervals]), fill_value=0)

        for xi, x_iv in enumerate(x_intervals):
            for yi, y_iv in enumerate(y_intervals):
                key = (x_iv, y_iv)
                if int(counts.loc[key]) >= min_count_per_cell:
                    # note: Z indexed [y, x]
                    Z[yi, xi] = float(z_agg.loc[key])

        # --- 3D surface ---
        X, Y = np.meshgrid(x_centers, y_centers)  # shapes: (ny, nx)
        fig = plt.figure()
        ax = fig.add_subplot(111, projection="3d")

        # mask NaNs so surface has holes instead of huge spikes
        Zm = np.ma.masked_invalid(Z)
        ax.plot_surface(X, Y, Zm, linewidth=0, antialiased=True)

        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_zlabel(zlabel)
        plt.tight_layout()
        plt.savefig(os.path.join(out_dir, out_name_3d), dpi=200)
        plt.close()

        # --- 2D heatmap (recommended for readability) ---
        plt.figure(figsize=(10, 6))
        # imshow expects [rows, cols] -> [y bins, x bins]
        im = plt.imshow(
            Z,
            origin="lower",
            aspect="auto",
            extent=[x_bins[0], x_bins[-1], y_bins[0], y_bins[-1]],
            interpolation="nearest",
        )
        plt.title(title + f" ({agg}, cells< {min_count_per_cell} masked)")
        plt.xlabel(xlabel)
        plt.ylabel(ylabel)
        plt.colorbar(im, label=zlabel)
        plt.tight_layout()
        plt.savefig(os.path.join(out_dir, out_name_2d), dpi=200)
        plt.close()

    # -----------------------------
    # SNR analyses
    # -----------------------------
    ok = df[df["CRC"] == 0].copy()
    bad = df[df["CRC"] == 1].copy()

    # 1) SNR for success vs unsuccessful
    plot_hist(ok["SNR_dB"],  "SNR(dB) — CRC OK (0)",   "SNR (dB)", "snr_crc0_hist.png", bins=80)
    plot_hist(bad["SNR_dB"], "SNR(dB) — CRC Fail (1)", "SNR (dB)", "snr_crc1_hist.png", bins=80)

    # Compare on same axes (optional)
    plt.figure()
    s0 = finite_series(ok["SNR_dB"])
    s1 = finite_series(bad["SNR_dB"])
    if len(s1) > 0:
        plt.hist(s1, bins=80, alpha=0.4, label="CRC=1")
    if len(s0) > 0:
        plt.hist(s0, bins=24, alpha=0.8, label="CRC=0")
    
    plt.title("SNR(dB) — CRC OK vs Fail")
    plt.xlabel("SNR (dB)")
    plt.ylabel("Count")
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "snr_crc_compare_hist.png"), dpi=160)
    plt.close()

    # 2) SNR vs distance / elevation conditions
    # Use only rows with geometry available (distance/elevation) and SNR present
    geom_rows = df.dropna(subset=["SNR_dB", "DistSlant_km"]).copy()

    # Distance bins (km)
    dist_bins = [0, 5, 10, 20, 40, 80, 120, 200, 400, 800]
    geom_rows["DistBin_km"] = pd.cut(geom_rows["DistSlant_km"], bins=dist_bins, include_lowest=True)

    # Elevation bins (deg)
    elev_bins = [-10, 0, 5, 10, 20, 30, 40, 60, 90]
    geom_rows["ElevBin_deg"] = pd.cut(geom_rows["Elev_deg"], bins=elev_bins, include_lowest=True)

    # Boxplots for CRC OK only (cleaner “received successfully”)
    ok_geom = ok.dropna(subset=["SNR_dB", "DistSlant_km"]).copy()
    ok_geom["DistBin_km"] = pd.cut(ok_geom["DistSlant_km"], bins=dist_bins, include_lowest=True)
    ok_geom["ElevBin_deg"] = pd.cut(ok_geom["Elev_deg"], bins=elev_bins, include_lowest=True)

    plot_binned_box(
        ok_geom, "SNR_dB", "DistBin_km",
        "SNR(dB) vs 3D Distance (CRC OK)",
        "3D distance bin (km)", "SNR (dB)",
        "snr_vs_3ddist_bins_crc0_box.png"
    )
    plot_binned_box(
        ok_geom, "SNR_dB", "ElevBin_deg",
        "SNR(dB) vs Elevation (CRC OK)",
        "Elevation bin (deg)", "SNR (dB)",
        "snr_vs_elev_bins_crc0_box.png"
    )

    # Scatter plots (CRC OK and CRC fail separately)
    plot_scatter(ok["DistSlant_km"], ok["SNR_dB"],
             "SNR vs 3D Distance (CRC OK)", "3D distance / slant range (km)", "SNR (dB)",
             "snr_vs_3ddist_crc0_scatter.png")
    plot_scatter(bad["DistSlant_km"], bad["SNR_dB"],
                "SNR vs 3D Distance (CRC Fail)", "3D distance / slant range (km)", "SNR (dB)",
                "snr_vs_3ddist_crc1_scatter.png")

    plot_scatter(ok["Elev_deg"], ok["SNR_dB"],
                 "SNR vs Elevation (CRC OK)", "Elevation (deg)", "SNR (dB)",
                 "snr_vs_elev_crc0_scatter.png")
    plot_scatter(bad["Elev_deg"], bad["SNR_dB"],
                 "SNR vs Elevation (CRC Fail)", "Elevation (deg)", "SNR (dB)",
                 "snr_vs_elev_crc1_scatter.png")

    # -----------------------------
    # NEW: SNR vs Altitude (height)
    # -----------------------------
    # Use only CRC OK rows with altitude available
    ok_alt = ok.dropna(subset=["SNR_dB", "Alt_ft"]).copy()
    ok_alt = ok_alt[(ok_alt["Alt_ft"] > 0)]

    # Scatter: SNR vs altitude (ft)
    plot_scatter(ok_alt["Alt_ft"], ok_alt["SNR_dB"],
                "SNR vs Altitude (CRC OK)", "Altitude (ft)", "SNR (dB)",
                "snr_vs_alt_ft_crc0_scatter.png")

    # Scatter: SNR vs altitude (m) if you prefer metric
    ok_alt_m = ok.dropna(subset=["SNR_dB", "Alt_m"]).copy()
    ok_alt_m = ok_alt_m[(ok_alt_m["Alt_m"] > 0)]
    plot_scatter(ok_alt_m["Alt_m"], ok_alt_m["SNR_dB"],
                "SNR vs Altitude (CRC OK)", "Altitude (m)", "SNR (dB)",
                "snr_vs_alt_m_crc0_scatter.png")

    # Altitude bins (ft)
    alt_bins_ft = [0, 2000, 5000, 10000, 20000, 30000, 40000, 60000]
    ok_alt["AltBin_ft"] = pd.cut(ok_alt["Alt_ft"], bins=alt_bins_ft, include_lowest=True)

    plot_binned_box(
        ok_alt, "SNR_dB", "AltBin_ft",
        "SNR(dB) vs Altitude (CRC OK)",
        "Altitude bin (ft)", "SNR (dB)",
        "snr_vs_alt_bins_crc0_box.png"
    )

    # Also: separate short vs extended (optional but often useful)
    for typ in ["short", "extended"]:
        sub_ok = ok[ok["SquitterType"] == typ]
        sub_bad = bad[bad["SquitterType"] == typ]
        plot_hist(sub_ok["SNR_dB"],  f"SNR(dB) — {typ} — CRC OK",   "SNR (dB)", f"snr_{typ}_crc0_hist.png", bins=80)
        plot_hist(sub_bad["SNR_dB"], f"SNR(dB) — {typ} — CRC Fail", "SNR (dB)", f"snr_{typ}_crc1_hist.png", bins=80)


    # --- NEW: compare SNR histogram (CRC OK) short vs extended in one figure ---
    ok_short = ok[ok["SquitterType"] == "short"]
    ok_ext   = ok[ok["SquitterType"] == "extended"]

    s_short = finite_series(ok_short["SNR_dB"])
    s_ext   = finite_series(ok_ext["SNR_dB"])

    plt.figure()
    if len(s_ext) > 0:
        plt.hist(s_ext, bins=80, alpha=0.5, label="Extended (CRC OK)")
    if len(s_short) > 0:
        plt.hist(s_short, bins=80, alpha=0.8, label="Short (CRC OK)")

    plt.title("SNR(dB) — Short vs Extended (CRC OK)")
    plt.xlabel("SNR (dB)")
    plt.ylabel("Count")
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "snr_crc0_short_vs_extended_hist.png"), dpi=160)
    plt.close()
    
    ok_3d = ok.dropna(subset=["DistSlant_km", "Alt_m", "SNR_dB"]).copy()

    plot_3d_scatter(
        ok_3d["DistSlant_km"],
        ok_3d["Alt_m"],
        ok_3d["SNR_dB"],
        title="SNR vs 3D Distance & Altitude (CRC OK)",
        xlabel="3D distance / slant range (km)",
        ylabel="Altitude (m)",
        zlabel="SNR (dB)",
        out_name="snr_vs_dist_alt_3d_crc0.png"
    )
    
    # --- Binned surface: distance (km) x height (m) -> median SNR ---
    ok_surface = ok.dropna(subset=["DistSlant_km", "HeightAboveRx_m", "SNR_dB"]).copy()

    # Choose bins (tune as you like)
    dist_bins_km = [0, 10, 20, 40, 80, 120, 200, 400]          # km
    height_bins_m = [0, 500, 1000, 2000, 4000, 6000, 8000, 12000]  # meters above receiver

    plot_binned_surface_3d(
        df_in=ok_surface,
        x_col="DistSlant_km",
        y_col="HeightAboveRx_m",
        z_col="SNR_dB",
        x_bins=dist_bins_km,
        y_bins=height_bins_m,
        agg="median",
        title="SNR vs (3D Distance, Height Above Rx) — CRC OK",
        xlabel="3D distance / slant range (km)",
        ylabel="Height above receiver (m)",
        zlabel="Median SNR (dB)",
        out_name_3d="snr_surface_dist_height_crc0_3d.png",
        out_name_2d="snr_surface_dist_height_crc0_2d.png",
        min_count_per_cell=2,
    )


    
    # Print quick stats
    def stats(name: str, sub: pd.DataFrame):
        s = sub["SNR_dB"].dropna()
        if len(s) == 0:
            print(f"[{name}] no SNR samples")
            return
        print(f"[{name}] N={len(sub)}, SNR mean={s.mean():.2f} dB, median={s.median():.2f} dB, p10={s.quantile(0.1):.2f}, p90={s.quantile(0.9):.2f}")

    print("=== SNR Summary ===")
    stats("CRC=0", ok)
    stats("CRC=1", bad)
    stats("CRC=0 & short", ok[ok["SquitterType"] == "short"])
    stats("CRC=0 & extended", ok[ok["SquitterType"] == "extended"])
    if use_opensky:
        print(f"OpenSky API calls attempted: {api_calls} (cached by (icao24,time))")

    print(f"Saved CSVs: {parsed_csv}, {enriched_csv}")
    print(f"Plots saved in: {out_dir}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_txt", help="Path to MATLAB log .txt file (CSV-like).")
    ap.add_argument("--out", default="snr_out", help="Output directory.")
    ap.add_argument("--use-opensky", action="store_true", help="Backfill missing lat/lon/alt/speed via OpenSky.")
    ap.add_argument("--opensky-user", default=os.getenv("OPENSKY_USER"), help="OpenSky username (or env OPENSKY_USER).")
    ap.add_argument("--opensky-pass", default=os.getenv("OPENSKY_PASS"), help="OpenSky password (or env OPENSKY_PASS).")
    ap.add_argument("--max-api-calls", type=int, default=5000, help="Safety cap on API calls.")
    args = ap.parse_args()

    analyze(
        input_txt=args.input_txt,
        out_dir=args.out,
        use_opensky=args.use_opensky,
        opensky_user=args.opensky_user,
        opensky_pass=args.opensky_pass,
        max_api_calls=args.max_api_calls,
    )


if __name__ == "__main__":
    main()
