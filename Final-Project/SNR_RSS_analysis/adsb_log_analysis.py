#!/usr/bin/env python3
"""
ADS-B MATLAB log analysis:
- parse MATLAB .txt log (CSV-like)
- correct wrong MATLAB timestamps via linear mapping (2 anchor points)
- compute distance to measurement location
- fill missing lat/lon/alt/speed via OpenSky /api/states/all (optional)
- plot distributions separated by:
  * short vs extended squitter
  * extended squitter by TC category (plus all extended)
Outputs:
- parsed.csv
- enriched.csv
- PNG histograms
"""

from __future__ import annotations

import os
import math
import time
import json
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple, Any, List
from datetime import timedelta

import pandas as pd
import requests
import matplotlib.pyplot as plt
import numpy as np


# -----------------------------
# User-provided fixed info
# -----------------------------
MEAS_LAT = 25 + 1/60 + 4/3600          # 25°01'04.0"N
MEAS_LON = 121 + 32/60 + 38.9/3600     # 121°32'38.9"E
MEAS_ALT_M = 15.0  # you are at 15 m above sea level

# MATLAB time anchors (local time, same date as in file)
# map 17:06:22 (matlab) -> 17:07:27 (real)
# map 17:14:34 (matlab) -> 17:23:05 (real)
# ANCHOR_1_MATLAB = "17:06:22"
# ANCHOR_1_REAL   = "17:07:27"
# ANCHOR_2_MATLAB = "17:14:34"
# ANCHOR_2_REAL   = "17:23:05"
ANCHOR_1_MATLAB = "17:40:11"
ANCHOR_1_REAL   = "17:40:11"
ANCHOR_2_MATLAB = "17:48:55"
ANCHOR_2_REAL   = "17:56:30"



# -----------------------------
# Helpers: geo + time mapping
# -----------------------------
def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance (meters) on WGS84 sphere approximation."""
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlmb/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def parse_matlab_timestamp(ts: str) -> datetime:
    """
    Parse MATLAB log time like: '15-Dec-2025 17:06:09'
    Returns naive datetime (local time as recorded).
    """
    ts = (ts or "").strip()
    return datetime.strptime(ts, "%d-%b-%Y %H:%M:%S")


@dataclass(frozen=True)
class LinearTimeMapper:
    """
    real_seconds = a * matlab_seconds + b
    Computed using two anchor points, per-date.
    """
    a: float
    b: float

    @staticmethod
    def from_anchors(date_str: str) -> "LinearTimeMapper":
        """
        date_str: e.g. '15-Dec-2025' (from your log)
        anchors use the same date.
        """
        d = datetime.strptime(date_str, "%d-%b-%Y")
        m1 = datetime.strptime(f"{date_str} {ANCHOR_1_MATLAB}", "%d-%b-%Y %H:%M:%S")
        r1 = datetime.strptime(f"{date_str} {ANCHOR_1_REAL}",   "%d-%b-%Y %H:%M:%S")
        m2 = datetime.strptime(f"{date_str} {ANCHOR_2_MATLAB}", "%d-%b-%Y %H:%M:%S")
        r2 = datetime.strptime(f"{date_str} {ANCHOR_2_REAL}",   "%d-%b-%Y %H:%M:%S")

        m1s = m1.timestamp()
        r1s = r1.timestamp()
        m2s = m2.timestamp()
        r2s = r2.timestamp()

        if abs(m2s - m1s) < 1e-6:
            raise ValueError("Anchor MATLAB times are identical; cannot build linear map.")

        a = (r2s - r1s) / (m2s - m1s)
        b = r1s - a * m1s
        return LinearTimeMapper(a=a, b=b)

    def map_dt(self, matlab_dt: datetime) -> datetime:
        s = matlab_dt.timestamp()
        rs = self.a * s + self.b
        return datetime.fromtimestamp(rs)  # naive local


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


def normalize_icao24(x: Any) -> Optional[str]:
    """
    ICAO24 in log may be blank. Normalize to lowercase hex without spaces.
    """
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    # sometimes padded with spaces; keep hex only
    s = s.replace(" ", "").lower()
    # basic sanity: hex string 6 chars
    if all(c in "0123456789abcdef" for c in s) and len(s) == 6:
        return s
    return None


def msg_len_class(message_hex: Any) -> str:
    """
    Classify by hex length:
      14 hex chars -> 56-bit short squitter
      28 hex chars -> 112-bit extended squitter
    Otherwise 'unknown'.
    """
    if message_hex is None:
        return "unknown"
    s = str(message_hex).strip().replace(" ", "")
    n = len(s)
    if n == 14:
        return "short"
    if n == 28:
        return "extended"
    return "unknown"


def tc_category(tc: Optional[float]) -> str:
    """
    Map ADS-B Type Code to a broad category.
    """
    if tc is None:
        return "TC_unknown"
    t = int(tc)
    if 1 <= t <= 4:
        return "ID_TC1-4"
    if 5 <= t <= 8:
        return "SurfacePos_TC5-8"
    if 9 <= t <= 18:
        return "AirbornePos_Baro_TC9-18"
    if t == 19:
        return "Velocity_TC19"
    if 20 <= t <= 22:
        return "AirbornePos_GNSS_TC20-22"
    if t == 28:
        return "Status_TC28"
    if t == 0:
        return "TC0_or_not_decoded"
    return f"Other_TC{t}"

def extract_icao24_from_df11(message_hex: Any) -> Optional[str]:
    """
    Try to extract ICAO24 (AA field) from a DF=11 (56-bit) Mode S all-call reply.
    This is a best-effort decode based on common DF11 structure.
    Returns lowercase 6-hex string, or None if cannot decode.
    """
    if message_hex is None:
        return None
    s = str(message_hex).strip().replace(" ", "")
    if len(s) != 14:  # 56-bit => 14 hex chars
        return None
    try:
        bits = bin(int(s, 16))[2:].zfill(56)
    except Exception:
        return None

    # DF is first 5 bits
    df = int(bits[0:5], 2)
    if df != 11:
        return None

    # For DF=11, ICAO24 is commonly in bits 8..31 (24 bits) depending on definition.
    # Using the widely used decode: AA field bits [8:32] (0-indexed, end-exclusive).
    aa_bits = bits[8:32]
    icao = f"{int(aa_bits, 2):06x}"
    return icao

# -----------------------------
# OpenSky backfill
# -----------------------------
class OpenSkyOAuthTokenProvider:
    TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"

    def __init__(self, client_id: str, client_secret: str, timeout_s: int = 20):
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout_s = timeout_s
        self.session = requests.Session()
        self._token: Optional[str] = None
        self._expires_at: Optional[datetime] = None  # naive local OK for comparisons

    def get_token(self) -> str:
        # Refresh if missing or expiring soon
        if self._token and self._expires_at and datetime.now() < (self._expires_at - timedelta(minutes=2)):
            return self._token

        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        resp = self.session.post(self.TOKEN_URL, data=data, timeout=self.timeout_s)
        resp.raise_for_status()
        js = resp.json()
        token = js["access_token"]
        expires_in = int(js.get("expires_in", 1800))  # docs say ~30 min
        self._token = token
        self._expires_at = datetime.now() + timedelta(seconds=expires_in)
        return token


class OpenSkyClient:
    BASE = "https://opensky-network.org/api"

    def __init__(
        self,
        username: Optional[str],
        password: Optional[str],
        client_id: Optional[str],
        client_secret: Optional[str],
        timeout_s: int = 20,
    ):
        self.basic_auth = (username, password) if (username and password) else None
        self.token_provider = None
        if client_id and client_secret:
            self.token_provider = OpenSkyOAuthTokenProvider(client_id, client_secret, timeout_s=timeout_s)

        self.timeout_s = timeout_s
        self.session = requests.Session()
        self.cache: Dict[Tuple[str, int], Optional[Dict[str, Any]]] = {}

    @staticmethod
    def round_query_time(epoch_s: int, authenticated: bool) -> int:
        # Authenticated users can use 5s resolution; anonymous often effectively 10s
        return epoch_s - (epoch_s % (5 if authenticated else 10))

    def _headers(self) -> Dict[str, str]:
        if self.token_provider is None:
            return {}
        tok = self.token_provider.get_token()
        return {"Authorization": f"Bearer {tok}"}

    def get_state(self, icao24: str, epoch_s: int) -> Optional[Dict[str, Any]]:
        authenticated = (self.basic_auth is not None) or (self.token_provider is not None)
        t = self.round_query_time(epoch_s, authenticated=authenticated)
        key = (icao24, t)
        if key in self.cache:
            return self.cache[key]

        url = f"{self.BASE}/states/all"
        params = {"time": t, "icao24": icao24}

        try:
            resp = self.session.get(
                url,
                params=params,
                auth=self.basic_auth,
                headers=self._headers(),
                timeout=self.timeout_s,
            )
            if resp.status_code != 200:
                self.cache[key] = None
                return None
            data = resp.json()
            states = data.get("states", None)
            if not states:
                self.cache[key] = None
                return None

            row = states[0]
            # indices per OpenSky docs (state vector):
            # 5=lon, 6=lat, 7=baro_alt, 9=velocity, 10=true_track, 11=vertical_rate, 13=geo_alt
            result = {
                "callsign": (row[1] or "").strip() if len(row) > 1 else None,
                "lon": row[5] if len(row) > 5 else None,
                "lat": row[6] if len(row) > 6 else None,
                "baro_alt_m": row[7] if len(row) > 7 else None,
                "vel_mps": row[9] if len(row) > 9 else None,
                "true_track_deg": row[10] if len(row) > 10 else None,
                "vert_rate_mps": row[11] if len(row) > 11 else None,
                "geo_alt_m": row[13] if len(row) > 13 else None,
            }
            self.cache[key] = result
            return result
        except Exception:
            self.cache[key] = None
            return None



def m_to_ft(m: Optional[float]) -> Optional[float]:
    if m is None:
        return None
    return m * 3.280839895


def mps_to_kts(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    return v * 1.9438444924406


# -----------------------------
# Main analysis
# -----------------------------
def analyze(
    input_txt: str,
    out_dir: str,
    use_opensky: bool,
    opensky_user: Optional[str],
    opensky_pass: Optional[str],
    restrict_bbox_deg: float = 2.0,
    max_api_calls: int = 5000,
    opensky_client_id: Optional[str] = None,
    opensky_client_secret: Optional[str] = None,
) -> None:
    os.makedirs(out_dir, exist_ok=True)

    # Read CSV-like text; skipinitialspace handles "...,  1, 7,       , ..."
    df = pd.read_csv(input_txt, skipinitialspace=True)

    # Normalize core fields
    df["Message_str"] = df["Message"].astype(str).str.strip().str.replace(" ", "", regex=False)
    df["SquitterType"] = df["Message_str"].apply(msg_len_class)

    df["CRC"] = pd.to_numeric(df["CRC"], errors="coerce")
    df["DF"]  = pd.to_numeric(df["DF"], errors="coerce")
    df["TC"]  = pd.to_numeric(df["TC"], errors="coerce")
    df["ICAO24_norm"] = df["ICAO24"].apply(normalize_icao24)
    
    # If ICAO24 missing in the log, try extracting from DF=11 short squitter message
    missing_icao = df["ICAO24_norm"].isna()
    df.loc[missing_icao, "ICAO24_norm"] = df.loc[missing_icao, "Message_str"].apply(extract_icao24_from_df11)


    # Parse MATLAB timestamps
    df["MatlabDT"] = df["Time"].apply(parse_matlab_timestamp)

    # Build per-date time mapper (in case file spans multiple dates)
    df["DateStr"] = df["MatlabDT"].dt.strftime("%d-%b-%Y")
    mappers: Dict[str, LinearTimeMapper] = {
        d: LinearTimeMapper.from_anchors(d) for d in df["DateStr"].unique()
    }

    # Apply mapping
    def map_real(dt: datetime) -> datetime:
        d = dt.strftime("%d-%b-%Y")
        return mappers[d].map_dt(dt)

    df["RealDT"] = df["MatlabDT"].apply(map_real)

    # Epoch seconds for OpenSky
    # NOTE: we treat RealDT as local naive; if you want UTC, convert here.
    df["RealEpoch"] = df["RealDT"].apply(lambda x: int(x.replace(tzinfo=timezone.utc).timestamp()))

    # Parse numeric fields we care about from log
    df["Lat_log"] = df["Latitude"].apply(safe_float)
    df["Lon_log"] = df["Longitude"].apply(safe_float)
    df["Alt_log"] = df["Altitude"].apply(safe_float)   # units in your MATLAB viewer (often feet)
    df["Spd_log"] = df["Speed"].apply(safe_float)       # often knots in GUI logs

    # Initialize enriched fields with log values
    df["Lat"] = df["Lat_log"]
    df["Lon"] = df["Lon_log"]
    df["Alt_ft"] = df["Alt_log"]
    df["Spd_kts"] = df["Spd_log"]

    # Optional OpenSky backfill
    client = OpenSkyClient(opensky_user, opensky_pass, opensky_client_id, opensky_client_secret) if use_opensky else None

    api_calls = 0

    # Optional: restrict OpenSky queries to a bbox around you to reduce credits (client-side filtering)
    # We still call /states/all with icao24, so bbox isn't necessary for credits; kept as placeholder if you
    # later switch to bbox queries.
    # restrict_bbox_deg is unused for now.

    def needs_fill(row) -> bool:
        # Fill if any key info missing/zero and we have ICAO24
        if row["ICAO24_norm"] is None:
            return False
        # Many logs store 0.0 when unknown; treat 0/0 as missing location
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

            # Fill lat/lon if missing
            if row["Lat"] is None or row["Lon"] is None or (abs(row["Lat"]) < 1e-9 and abs(row["Lon"]) < 1e-9):
                if st.get("lat") is not None and st.get("lon") is not None:
                    df.at[idx, "Lat"] = float(st["lat"])
                    df.at[idx, "Lon"] = float(st["lon"])

            # Fill altitude (convert meters->feet) if missing
            if row["Alt_ft"] is None or row["Alt_ft"] == 0:
                alt_m = st.get("baro_alt_m")
                if alt_m is None:
                    alt_m = st.get("geo_alt_m")   # <-- fallback
                alt_ft = m_to_ft(alt_m)
                if alt_ft is not None:
                    df.at[idx, "Alt_ft"] = float(alt_ft)

            # Fill speed (convert m/s->kts) if missing
            if row["Spd_kts"] is None or row["Spd_kts"] == 0:
                spd_kts = mps_to_kts(st.get("vel_mps"))
                if spd_kts is not None:
                    df.at[idx, "Spd_kts"] = float(spd_kts)

            # Optionally fill heading from OpenSky true_track
            if "Heading(°)" in df.columns:
                hd = safe_float(row.get("Heading(°)", None))
                if (hd is None or hd == 0) and st.get("true_track_deg") is not None:
                    df.at[idx, "Heading(°)"] = float(st["true_track_deg"])

            # Be polite to the API
            time.sleep(0.05)
    
    print("Short squitter rows:", (df["SquitterType"] == "short").sum())
    print("Short with ICAO24_norm:", df[df["SquitterType"]=="short"]["ICAO24_norm"].notna().sum())
    print("Short with Lat/Lon:", df[(df["SquitterType"]=="short") & df["Lat"].notna() & df["Lon"].notna()].shape[0])
    print("Short with Alt_ft:", df[(df["SquitterType"]=="short") & df["Alt_ft"].notna() & (df["Alt_ft"] > 0)].shape[0])
    short = df[df["SquitterType"]=="short"]
    print("Short with baro or geo Alt_ft:", (short["Alt_ft"].notna() & (short["Alt_ft"] > 0)).sum())

    # Distance calculations (horizontal great-circle and slant if altitude known)
    def compute_dist(row) -> Tuple[Optional[float], Optional[float]]:
        lat, lon = row["Lat"], row["Lon"]
        if lat is None or lon is None or (abs(lat) < 1e-9 and abs(lon) < 1e-9):
            return None, None

        # Horizontal great-circle distance (meters)
        horiz_m = haversine_m(MEAS_LAT, MEAS_LON, float(lat), float(lon))

        # Aircraft altitude in feet (assumed MSL), convert to meters
        alt_ft = row["Alt_ft"]
        if alt_ft is None or alt_ft == 0:
            return horiz_m, None

        aircraft_alt_m = float(alt_ft) / 3.280839895

        # Slant range uses altitude difference (aircraft altitude - your altitude)
        dh_m = aircraft_alt_m - MEAS_ALT_M
        slant_m = math.sqrt(horiz_m * horiz_m + dh_m * dh_m)
        return horiz_m, slant_m


    dists = df.apply(compute_dist, axis=1, result_type="expand")
    df["DistHoriz_m"] = dists[0]
    df["DistSlant_m"] = dists[1]
    df["DistHoriz_km"] = df["DistHoriz_m"] / 1000.0
    df["DistSlant_km"] = df["DistSlant_m"] / 1000.0

    # TC category for extended squitter grouping
    df["TC_Category"] = df["TC"].apply(lambda x: tc_category(safe_float(x)))

    # Save parsed and enriched
    parsed_csv = os.path.join(out_dir, "parsed.csv")
    enriched_csv = os.path.join(out_dir, "enriched.csv")
    df.to_csv(enriched_csv, index=False)
    # parsed.csv = without OpenSky-filling; easiest is just save key columns pre-fill
    # (Here we reuse df but indicate what came from log vs filled.)
    df.to_csv(parsed_csv, index=False)

    # -----------------------------
    # Plot distributions
    # -----------------------------
    def plot_hist(series: pd.Series, title: str, xlabel: str, out_name: str, bins: int = 60):
        s = series.dropna()
        s = s[~pd.isna(s)]
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

    # Only consider "successfully received data" = CRC == 0
    ok = df[df["CRC"] == 0].copy()

    # Split by squitter type
    ok_short = ok[ok["SquitterType"] == "short"]
    ok_ext   = ok[ok["SquitterType"] == "extended"]
    
    # 3D slant distance (km): short vs extended overlay (CRC OK)
    s_short = ok_short["DistSlant_km"].dropna()
    s_ext   = ok_ext["DistSlant_km"].dropna()

    if len(s_short) > 0 or len(s_ext) > 0:
        plt.figure()
        # Use common bin edges so the comparison is fair
        all_vals = pd.concat([s_short, s_ext], ignore_index=True)
        bins = 60
        bin_edges = np.linspace(all_vals.min(), all_vals.max(), bins + 1)

        if len(s_short) > 0:
            plt.hist(s_short, bins=bin_edges, alpha=0.6, label="Short (56-bit)")
        if len(s_ext) > 0:
            plt.hist(s_ext, bins=bin_edges, alpha=0.6, label="Extended (112-bit)")

        plt.title("3D Slant Distance — Short vs Extended (CRC OK)")
        plt.xlabel("3D distance / slant range (km)")
        plt.ylabel("Count")
        plt.legend()
        plt.tight_layout()
        plt.savefig(os.path.join(out_dir, "dist3d_short_vs_extended_crc0.png"), dpi=160)
        plt.close()

    # Distance / speed / altitude distributions: short vs extended
    plot_hist(ok_short["DistHoriz_km"], "Distance — Short squitter (CRC OK)", "km", "dist_short_crc0.png")
    plot_hist(ok_ext["DistHoriz_km"],   "Distance — Extended squitter (CRC OK)", "km", "dist_extended_crc0.png")

    plot_hist(ok_short["Spd_kts"], "Speed — Short squitter (CRC OK)", "knots", "speed_short_crc0.png")
    plot_hist(ok_ext["Spd_kts"],   "Speed — Extended squitter (CRC OK)", "knots", "speed_extended_crc0.png")

    plot_hist(ok_short["Alt_ft"], "Altitude — Short squitter (CRC OK)", "ft", "alt_short_crc0.png")
    plot_hist(ok_ext["Alt_ft"],   "Altitude — Extended squitter (CRC OK)", "ft", "alt_extended_crc0.png")

    # Extended squitter: per TC category + all extended together
    plot_hist(ok_ext["DistHoriz_km"], "Distance — All extended squitter (CRC OK)", "km", "dist_extended_all_crc0.png")
    plot_hist(ok_ext["Spd_kts"],      "Speed — All extended squitter (CRC OK)", "knots", "speed_extended_all_crc0.png")
    plot_hist(ok_ext["Alt_ft"],       "Altitude — All extended squitter (CRC OK)", "ft", "alt_extended_all_crc0.png")

    for cat, g in ok_ext.groupby("TC_Category"):
        safe_cat = cat.replace("/", "_")
        plot_hist(g["DistHoriz_km"], f"Distance — {cat} (extended, CRC OK)", "km", f"dist_ext_{safe_cat}_crc0.png")
        plot_hist(g["Spd_kts"],      f"Speed — {cat} (extended, CRC OK)", "knots", f"speed_ext_{safe_cat}_crc0.png")
        plot_hist(g["Alt_ft"],       f"Altitude — {cat} (extended, CRC OK)", "ft", f"alt_ext_{safe_cat}_crc0.png")

    # Print quick summary
    def summarize(name: str, sub: pd.DataFrame):
        n = len(sub)
        n_pos = sub["DistHoriz_km"].notna().sum()
        n_spd = sub["Spd_kts"].notna().sum()
        n_alt = sub["Alt_ft"].notna().sum()
        print(f"[{name}] N={n}, with distance={n_pos}, speed={n_spd}, altitude={n_alt}")

    print("=== Summary (CRC==0 only) ===")
    summarize("Short", ok_short)
    summarize("Extended", ok_ext)
    print(f"Saved: {parsed_csv}")
    print(f"Saved: {enriched_csv}")
    print(f"Plots saved in: {out_dir}")
    if use_opensky:
        print(f"OpenSky API calls attempted: {api_calls} (cached by (icao24,time))")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_txt", help="Path to MATLAB log .txt file (CSV-like).")
    ap.add_argument("--out", default="adsb_out", help="Output directory.")
    ap.add_argument("--use-opensky", action="store_true", help="Backfill missing lat/lon/alt/speed using OpenSky.")
    ap.add_argument("--opensky-user", default=os.getenv("OPENSKY_USER"), help="OpenSky username (or env OPENSKY_USER).")
    ap.add_argument("--opensky-pass", default=os.getenv("OPENSKY_PASS"), help="OpenSky password (or env OPENSKY_PASS).")
    ap.add_argument("--max-api-calls", type=int, default=5000, help="Safety cap on API calls.")
    ap.add_argument("--opensky-client-id", default=os.getenv("OPENSKY_CLIENT_ID"))
    ap.add_argument("--opensky-client-secret", default=os.getenv("OPENSKY_CLIENT_SECRET"))

    args = ap.parse_args()

    analyze(
        input_txt=args.input_txt,
        out_dir=args.out,
        use_opensky=args.use_opensky,
        opensky_user=args.opensky_user,
        opensky_pass=args.opensky_pass,
        max_api_calls=args.max_api_calls,
        opensky_client_id=args.opensky_client_id,
        opensky_client_secret=args.opensky_client_secret,
    )


if __name__ == "__main__":
    main()
