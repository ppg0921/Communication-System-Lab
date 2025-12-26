import pandas as pd
import matplotlib.pyplot as plt
import math
import os

legends = ["50dB"]
log1 = "Demo_Data/demo_31.csv"

md_lat = 25 + 1/60 + 4.0476/3600  # 25°01'04.08"
md_long = 121 + 32/60 + 39.3036/3600  # 121°32'39.30"
MEAS_ALT_M = 15

def compute_dist(lat, lon, alt):
        if lat is None or lon is None or (abs(lat) < 1e-9 and abs(lon) < 1e-9):
            return None, None

        # Horizontal great-circle distance (meters)
        horiz_m = haversine_m(md_lat, md_long, float(lat), float(lon))

        # Aircraft altitude in feet (assumed MSL), convert to meters
        alt_ft = alt
        if alt_ft is None or alt_ft == 0:
            return horiz_m, None

        aircraft_alt_m = float(alt_ft) / 3.280839895

        # Slant range uses altitude difference (aircraft altitude - your altitude)
        dh_m = aircraft_alt_m - MEAS_ALT_M
        slant_m = math.sqrt(horiz_m * horiz_m + dh_m * dh_m)
        return horiz_m, slant_m

def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance (meters) on WGS84 sphere approximation."""
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlmb/2)**2
    return 2 * R * math.asin(math.sqrt(a))



filename = os.path.splitext(os.path.basename(log1))[0]

df_raw = pd.read_csv(log1)

# Convert to numeric before filtering
df_raw[" Latitude"] = pd.to_numeric(df_raw[" Latitude"], errors='coerce')
df_raw[" Longitude"] = pd.to_numeric(df_raw[" Longitude"], errors='coerce')

# Filter Latitude != 0 and not NaN
df_filtered = df_raw[df_raw[" Latitude"].notna() & (df_raw[" Latitude"] != 0)]

# Save result
df_filtered.to_csv(f"demo_{filename}_filtered.csv", index=False)




df1 = pd.read_csv(f"demo_{filename}_filtered.csv")

dfs = [df1]
colors = ['blue', 'green', 'cyan', 'olive', 'orange', 'purple', 'brown', 'gray', 'pink']

for i, df in enumerate(dfs):
    col = colors[i % len(colors)]
    # Data is already filtered and numeric from the saved CSV
    longs = df[" Longitude"]
    lats = df[" Latitude"]
    mask = longs.notna() & lats.notna()
    longs = longs[mask].copy()
    lats = lats[mask]
    longs = longs.where(longs >= 0, longs + 360)
    plt.scatter(longs, lats, color=col, alpha=0.1, label=legends[i])

plt.scatter(md_long, md_lat, alpha=1, color='red', label='md')
plt.legend()
plt.show()