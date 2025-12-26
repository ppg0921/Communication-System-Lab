#!/usr/bin/env python3
"""
Analyze ADS-B MATLAB log with RSS (Received Signal Strength) column:
- Parse MATLAB .txt (CSV-like)
- Correct MATLAB time to real time using 2 anchors (linear map)
- Backfill missing lat/lon/alt/speed via OpenSky /api/states/all (optional)
- Compute horizontal + slant distance and elevation angle
- Analyze RSS:
  * CRC success vs failure
  * RSS by distance bins
  * RSS by elevation bins
  * RSS by altitude bins
  * Scatter: RSS vs distance / elevation / altitude
Outputs:
- parsed.csv, enriched.csv
- histograms and binned boxplots as PNG

Notes:
- RSS column name is not standardized. The script will:
  (1) use --rss-col if provided, else
  (2) try to auto-detect from common names
"""

from __future__ import annotations

import os
import math
import time
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple, Any, List

import pandas as pd
import requests
import matplotlib.pyplot as plt
import numpy as np


# -----------------------------
# Fixed measurement location
# -----------------------------
MEAS_LAT = 25 + 1/60 + 4/3600          # 25°01'04.0"N
MEAS_LON = 121 + 32/60 + 38.9/3600     # 121°32'38.9"E
MEAS_ALT_M = 15.0                      # you are at 15 m above sea level

# -----------------------------
# Time mapping anchors
# -----------------------------
ANCHOR_1_MATLAB = "17:40:11"
ANCHOR_1_REAL   = "17:40:11"
ANCHOR_2_MATLAB = "17:48:55"
ANCHOR_2_REAL   = "17:56:30"


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


def plot_compare_hist_clipped(
    s_ok: pd.Series,
    s_bad: pd.Series,
    title: str,
    xlabel: str,
    out_path: str,
    bins: int = 80,
    q_hi: float = 0.95,
    x_min: float = 0.0,
):
    # Clean + finite
    okv = finite_series(s_ok)
    badv = finite_series(s_bad)

    # Combine to decide shared x-range
    allv = pd.concat([okv, badv], ignore_index=True).dropna()
    if len(allv) == 0:
        return

    # Right bound = percentile
    x_max = float(allv.quantile(q_hi))

    # Guard: if x_max <= x_min, expand a bit
    if not math.isfinite(x_max) or x_max <= x_min:
        x_max = x_min + 1.0

    # Use common bin edges so the two histograms align
    bin_edges = np.linspace(x_min, x_max, bins + 1)  # if pandas warns, see note below
    bin_edges_2 = np.linspace(x_min, x_max, bins//2 + 1)

    plt.figure()
    if len(badv) > 0:
        plt.hist(badv, bins=bin_edges, alpha=0.4, label="CRC=1", range=(x_min, x_max))
    if len(okv) > 0:
        plt.hist(okv, bins=bin_edges_2, alpha=0.8, label="CRC=0", range=(x_min, x_max))

    plt.title(f"{title} (x in [{x_min:.3g}, {x_max:.3g}] = {int(q_hi*100)}% of samples)")
    plt.xlabel(xlabel)
    plt.ylabel("Count")
    plt.xlim(x_min, x_max)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=160)
    plt.close()


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
# OpenSky client
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
# RSS column detection
# -----------------------------
def find_rss_column(df: pd.DataFrame, preferred: Optional[str]) -> str:
    if preferred:
        if preferred in df.columns:
            return preferred
        raise ValueError(f"--rss-col '{preferred}' not found. Available columns: {list(df.columns)}")

    # common variants (case-sensitive match first, then case-insensitive)
    candidates = [
        "RSS", "RSS(dB)", "RSS(dBm)", "RSSI", "RSSI(dB)", "RSSI(dBm)",
        "Received Signal Strength", "ReceivedSignalStrength",
        "SignalStrength", "Signal Strength", "RxPower", "Rx Power",
        "RcvdSignalStrength", "Rcvd Signal Strength",
    ]
    for c in candidates:
        if c in df.columns:
            return c

    # case-insensitive search
    lower_map = {str(c).lower(): c for c in df.columns}
    for c in candidates:
        key = c.lower()
        if key in lower_map:
            return lower_map[key]

    # heuristic: any column containing "rss" or "rssi"
    for col in df.columns:
        cl = str(col).lower()
        if "rssi" in cl or (("rss" in cl) and ("snr" not in cl)):
            return col

    raise ValueError(
        "Could not auto-detect RSS column. Please pass --rss-col.\n"
        f"Available columns: {list(df.columns)}"
    )


# -----------------------------
# Main analysis
# -----------------------------
def analyze(
    input_txt: str,
    out_dir: str,
    use_opensky: bool,
    opensky_user: Optional[str],
    opensky_pass: Optional[str],
    rss_col: Optional[str],
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

    # RSS column
    rss_colname = find_rss_column(df, rss_col)
    df["RSS"] = df[rss_colname].apply(safe_float)

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

    # Backfill missing lat/lon/alt/speed via OpenSky
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

    def plot_binned_box(df_in: pd.DataFrame, value_col: str, bin_col: str,
                        title: str, xlabel: str, ylabel: str, out_name: str):
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

    # -----------------------------
    # RSS analyses
    # -----------------------------
    ok = df[df["CRC"] == 0].copy()
    bad = df[df["CRC"] == 1].copy()

    # 1) RSS for success vs unsuccessful
    plot_hist(ok["RSS"],  "RSS — CRC OK (0)",   f"{rss_colname}", "rss_crc0_hist.png", bins=80)
    plot_hist(bad["RSS"], "RSS — CRC Fail (1)", f"{rss_colname}", "rss_crc1_hist.png", bins=80)

    # Compare on same axes
    plot_compare_hist_clipped(
        ok["RSS"],
        bad["RSS"],
        title="RSS — CRC OK vs Fail",
        xlabel=rss_colname,
        out_path=os.path.join(out_dir, "rss_crc_compare_hist.png"),
        bins=80,
        q_hi=0.95,
        x_min=0.0,
    )

    # 2) RSS vs distance / elevation conditions
    geom_rows = df.dropna(subset=["RSS", "DistSlant_km"]).copy()

    # Distance bins (km)
    dist_bins = [0, 5, 10, 20, 40, 80, 120, 200, 400, 800]
    geom_rows["DistBin_km"] = pd.cut(geom_rows["DistSlant_km"], bins=dist_bins, include_lowest=True)

    # Elevation bins (deg)
    elev_bins = [-10, 0, 5, 10, 20, 30, 40, 60, 90]
    geom_rows["ElevBin_deg"] = pd.cut(geom_rows["Elev_deg"], bins=elev_bins, include_lowest=True)

    # Boxplots for CRC OK only
    ok_geom = ok.dropna(subset=["RSS", "DistSlant_km"]).copy()
    ok_geom["DistBin_km"] = pd.cut(ok_geom["DistSlant_km"], bins=dist_bins, include_lowest=True)
    ok_geom["ElevBin_deg"] = pd.cut(ok_geom["Elev_deg"], bins=elev_bins, include_lowest=True)

    plot_binned_box(
        ok_geom, "RSS", "DistBin_km",
        "RSS vs 3D Distance (CRC OK)",
        "3D distance bin (km)", rss_colname,
        "rss_vs_3ddist_bins_crc0_box.png"
    )
    plot_binned_box(
        ok_geom, "RSS", "ElevBin_deg",
        "RSS vs Elevation (CRC OK)",
        "Elevation bin (deg)", rss_colname,
        "rss_vs_elev_bins_crc0_box.png"
    )

    # Scatter plots
    plot_scatter(ok["DistSlant_km"], ok["RSS"],
                 "RSS vs 3D Distance (CRC OK)", "3D distance / slant range (km)", rss_colname,
                 "rss_vs_3ddist_crc0_scatter.png")
    plot_scatter(bad["DistSlant_km"], bad["RSS"],
                 "RSS vs 3D Distance (CRC Fail)", "3D distance / slant range (km)", rss_colname,
                 "rss_vs_3ddist_crc1_scatter.png")

    plot_scatter(ok["Elev_deg"], ok["RSS"],
                 "RSS vs Elevation (CRC OK)", "Elevation (deg)", rss_colname,
                 "rss_vs_elev_crc0_scatter.png")
    plot_scatter(bad["Elev_deg"], bad["RSS"],
                 "RSS vs Elevation (CRC Fail)", "Elevation (deg)", rss_colname,
                 "rss_vs_elev_crc1_scatter.png")

    # 3) RSS vs altitude (CRC OK)
    ok_alt = ok.dropna(subset=["RSS", "Alt_ft"]).copy()
    ok_alt = ok_alt[(ok_alt["Alt_ft"] > 0)]

    plot_scatter(ok_alt["Alt_ft"], ok_alt["RSS"],
                 "RSS vs Altitude (CRC OK)", "Altitude (ft)", rss_colname,
                 "rss_vs_alt_ft_crc0_scatter.png")

    ok_alt_m = ok.dropna(subset=["RSS", "Alt_m"]).copy()
    ok_alt_m = ok_alt_m[(ok_alt_m["Alt_m"] > 0)]
    plot_scatter(ok_alt_m["Alt_m"], ok_alt_m["RSS"],
                 "RSS vs Altitude (CRC OK)", "Altitude (m)", rss_colname,
                 "rss_vs_alt_m_crc0_scatter.png")

    alt_bins_ft = [0, 2000, 5000, 10000, 20000, 30000, 40000, 60000]
    ok_alt["AltBin_ft"] = pd.cut(ok_alt["Alt_ft"], bins=alt_bins_ft, include_lowest=True)

    plot_binned_box(
        ok_alt, "RSS", "AltBin_ft",
        "RSS vs Altitude (CRC OK)",
        "Altitude bin (ft)", rss_colname,
        "rss_vs_alt_bins_crc0_box.png"
    )

    # 4) Short vs extended split (optional)
    for typ in ["short", "extended"]:
        sub_ok = ok[ok["SquitterType"] == typ]
        sub_bad = bad[bad["SquitterType"] == typ]
        plot_hist(sub_ok["RSS"],  f"RSS — {typ} — CRC OK",   rss_colname, f"rss_{typ}_crc0_hist.png", bins=80)
        plot_hist(sub_bad["RSS"], f"RSS — {typ} — CRC Fail", rss_colname, f"rss_{typ}_crc1_hist.png", bins=80)

    # Print quick stats
    def stats(name: str, sub: pd.DataFrame):
        s = finite_series(sub["RSS"])
        if len(s) == 0:
            print(f"[{name}] no RSS samples")
            return
        print(f"[{name}] N={len(sub)}, RSS mean={s.mean():.5f}, median={s.median():.5f}, p10={s.quantile(0.1):.5f}, p90={s.quantile(0.9):.5f}")

    print("=== RSS Summary ===")
    print(f"RSS column used: {rss_colname}")
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
    ap.add_argument("--out", default="rss_out", help="Output directory.")
    ap.add_argument("--rss-col", default=None,
                    help="RSS column name. If omitted, the script tries to auto-detect (e.g., RSS, RSSI, RSS(dBm), etc.).")
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
        rss_col=args.rss_col,
        max_api_calls=args.max_api_calls,
    )


if __name__ == "__main__":
    main()
