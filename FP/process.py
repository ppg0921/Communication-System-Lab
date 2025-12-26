import pandas as pd
import matplotlib.pyplot as plt
import math

md_lat = 25 + 1/60 + 4.0476/3600  # 25°01'04.08"
md_long = 121 + 32/60 + 39.3036/3600  # 121°32'39.30"
tpe_lat = 25 + 4/60 + 35/3600  # 25°04'35"
tpe_long = 121 + 13/60 + 26/3600  # 121°13'26"
tsa_lat = 25 + 4/60 + 11/3600  # 25°04'11"
tsa_long = 121 + 33/60 + 9/3600  # 121°33'09"

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




log1 = "Demo_Data/40dB_extended.csv"
log2 = "Demo_Data/50dB_extended.csv"
log3 = "Demo_Data/60dB_extended.csv"


legends = ["40dB", "50dB", "60dB"]

df1 = pd.read_csv(log1)
df2 = pd.read_csv(log2)
df3 = pd.read_csv(log3)

# dfs = [df1, df2, df3]
dfs = [df1, df2, df3]
colors = ['blue', 'green', 'cyan', 'olive', 'orange', 'purple', 'brown', 'gray', 'pink']

for i, df in enumerate(dfs):
    col = colors[i % len(colors)]
    df = df[df[" Latitude"].notna() & (df[" Latitude"] != 0)]
    longs = pd.to_numeric(df[" Longitude"], errors='coerce')
    lats = pd.to_numeric(df[" Latitude"], errors='coerce')
    mask = longs.notna() & lats.notna()
    longs = longs[mask].copy()
    lats = lats[mask]
    longs = longs.where(longs >= 0, longs + 360)
    plt.scatter(longs, lats, color=col, alpha=0.1, label=legends[i])

plt.scatter(md_long, md_lat, alpha=1, color='red', label='md')
plt.scatter(tpe_long, tpe_lat, alpha=0.8, color='pink', label='tpe')
plt.scatter(tsa_long, tsa_lat, alpha=0.8, color='brown', label='tsa')
plt.xlabel("Longitude (degrees)")
plt.ylabel("Latitude (degrees)")
plt.title("Flight Trails")
plt.legend()
plt.show()

# max_long = max(df[" Longitude"]) + 360
# min_long = min(df[" Longitude"]) + 360
# max_lat = max(df[" Latitude"])
# min_lat = min(df[" Latitude"])

# plt.figure(figsize=(10, 6))
# plt.scatter(max_long, max_lat, alpha=0.5)
# plt.scatter(min_long, min_lat, alpha=0.5)   
# plt.scatter(max_long, min_lat, alpha=0.5)
# plt.scatter(min_long, max_lat, alpha=0.5)

# plt.show()
