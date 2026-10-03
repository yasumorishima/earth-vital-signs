"""Generate notebooks/earth-vital-signs-starter.ipynb (run once; the notebook is committed)."""
import json
from pathlib import Path

cells = []


def md(s):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": s.strip("\n")})


def code(s):
    cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
                  "source": s.strip("\n")})


md("""
# Earth Vital Signs: a first look

Six charts from the daily-updated **Earth Vital Signs** dataset: Arctic sea ice by year, the Mauna Loa CO2 curve, ocean and air temperature against their 1991-2020 normals, El Niño day by day, and how the indicators move together month by month.

Every file is rebuilt each day from NOAA and NSIDC; the build code and checks are at https://github.com/yasumorishima/earth-vital-signs
""")

code("""
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

# On Kaggle the files sit somewhere under /kaggle/input; in the GitHub repo they are in data/.
found = sorted(Path("/kaggle/input").glob("**/sst_daily.csv")) + [p / "sst_daily.csv" for p in (Path("../data"), Path("data"))]
DATA = next(p.parent for p in found if p.exists())

ice = pd.read_csv(DATA / "sea_ice_extent_daily.csv", parse_dates=["date"])
co2 = pd.read_csv(DATA / "co2_daily_mauna_loa.csv", parse_dates=["date"])
sst = pd.read_csv(DATA / "sst_daily.csv", parse_dates=["date"])
air = pd.read_csv(DATA / "air_temperature_daily.csv", parse_dates=["date"])
enso = pd.read_csv(DATA / "enso_monthly.csv", parse_dates=["date"])
panel = pd.read_csv(DATA / "climate_monthly_panel.csv", parse_dates=["date"])
print({name: (len(df), str(df.date.max().date())) for name, df in
       [("sea ice", ice), ("co2", co2), ("sst", sst), ("air", air), ("enso", enso), ("panel", panel)]})

plt.rcParams.update({"axes.titlesize": 16, "axes.labelsize": 14, "xtick.labelsize": 12,
                     "ytick.labelsize": 12, "legend.fontsize": 12,
                     "axes.spines.top": False, "axes.spines.right": False})
GREY, RED, BLUE = "#c8c8c8", "#d62728", "#1f77b4"
""")

md("""
## 1. Arctic sea ice: every year since 1979 on one calendar

Grey lines are past years; the current year is red. The September minimum is where the decline shows most.
""")

code("""
north = ice[(ice.hemisphere == "north") & (ice.date.dt.year >= 1979)].copy()
north["doy"] = north.date.dt.dayofyear
this_year = north.date.dt.year.max()

fig, ax = plt.subplots(figsize=(11, 6))
for y, g in north.groupby(north.date.dt.year):
    if y != this_year:
        ax.plot(g.doy, g.extent_mkm2, color=GREY, lw=0.8)
g = north[north.date.dt.year == this_year]
ax.plot(g.doy, g.extent_mkm2, color=RED, lw=2.5, label=str(this_year))
ax.set_title("Arctic sea ice extent, 1979 to today")
ax.set_xlabel("Day of year")
ax.set_ylabel("Million km²")
ax.legend(frameon=False)
plt.tight_layout()
plt.show()
""")

code("""
# Annual minimum: how much smaller has the September low become?
mins = north[north.date.dt.year < this_year].groupby(north.date.dt.year).extent_mkm2.min()
print(mins.head(3).round(2).to_dict(), "...", mins.tail(3).round(2).to_dict())
""")

md("""
## 2. CO2 at Mauna Loa

The yearly saw-tooth is the Northern Hemisphere's vegetation breathing; the climb under it is the long-term rise.
""")

code("""
fig, ax = plt.subplots(figsize=(11, 6))
ax.plot(co2.date, co2.co2_ppm, color=BLUE, lw=0.6)
ax.set_title("Daily CO2 at Mauna Loa")
ax.set_ylabel("ppm")
plt.tight_layout()
plt.show()

yearly = co2.groupby(co2.date.dt.year).co2_ppm.mean()
full_years = yearly.loc[1975:co2.date.dt.year.max() - 1]
print("Average yearly increase, last 10 full years:", round(full_years.diff().tail(10).mean(), 2), "ppm")
""")

md("""
## 3. Ocean surface temperature against its normal

Daily mean SST between 60S and 60N, minus the 1991-2020 average for the same calendar day. Values above zero are warmer than normal.
""")

code("""
s = sst.set_index("date")["world_60s_60n_anom_1991_2020"]
fig, ax = plt.subplots(figsize=(11, 6))
ax.fill_between(s.index, s.values, 0, where=s.values >= 0, color=RED, lw=0)
ax.fill_between(s.index, s.values, 0, where=s.values < 0, color=BLUE, lw=0)
ax.axhline(0, color="black", lw=0.8)
ax.set_title("Sea surface temperature anomaly, 60S-60N")
ax.set_ylabel("°C vs 1991-2020")
plt.tight_layout()
plt.show()

print("Warmest 5 days on record:")
print(sst.nlargest(5, "world_60s_60n")[["date", "world_60s_60n"]].to_string(index=False))
""")

md("""
## 4. Global air temperature: each year against the 1991-2020 normal

Annual mean of the daily global 2 m air temperature anomaly (ERA5, Copernicus Climate Change Service). The current year is shown to date.
""")

code("""
a = air.groupby(air.date.dt.year).global_2t_anom_1991_2020.mean()
fig, ax = plt.subplots(figsize=(11, 6))
ax.bar(a.index, a.values, color=[RED if v >= 0 else BLUE for v in a.values], width=0.8)
ax.axhline(0, color="black", lw=0.8)
ax.set_title("Global air temperature anomaly by year")
ax.set_ylabel("°C vs 1991-2020")
plt.tight_layout()
plt.show()

rec = air[air.global_record_warm_for_day == True]
print("Days that set a record for their calendar date, by year (last 6 years):")
print(rec.groupby(rec.date.dt.year).size().tail(6).to_string())
""")

md("""
## 5. El Niño day by day

The daily Niño 3.4 anomaly computed from the 0.25-degree OISST grid, with NOAA's monthly ONI on top. ONI at +0.5 or above marks El Niño conditions, -0.5 or below La Niña.
""")

code("""
n34 = sst.set_index("date")["nino34_5n_5s_170w_120w_anom_1991_2020"].loc["2015":]
oni = enso.set_index("date").oni_anom.loc["2015":]
fig, ax = plt.subplots(figsize=(11, 6))
ax.plot(n34.index, n34.values, color=GREY, lw=0.8, label="Niño 3.4, daily")
ax.plot(oni.index, oni.values, color=RED, lw=2.5, label="ONI, monthly")
for y in (0.5, -0.5):
    ax.axhline(y, color="black", lw=0.6, ls="--")
ax.set_title("Niño 3.4 sea surface temperature anomaly")
ax.set_ylabel("°C")
ax.legend(frameon=False)
plt.tight_layout()
plt.show()
""")

md("""
## 6. How the indicators move together

Correlation of month-to-month values in the monthly panel since 1982. Greenhouse gases are left out because they mostly share a trend; the anomalies and indices are what vary from month to month.
""")

code("""
cols = ["air_temp_global_anom", "sst_world_60s_60n_anom", "sst_nino34_anom",
        "sst_dmi", "sea_ice_north_anom_mkm2", "sea_ice_south_anom_mkm2", "ao", "nao", "oni_anom"]
c = panel[panel.date >= "1982-01-01"][cols].corr()
fig, ax = plt.subplots(figsize=(10, 8))
im = ax.imshow(c.values, cmap="RdBu_r", vmin=-1, vmax=1)
ax.set_xticks(range(len(cols)), cols, rotation=60, ha="right")
ax.set_yticks(range(len(cols)), cols)
fig.colorbar(im, ax=ax, shrink=0.8)
ax.set_title("Correlation between monthly indicators, 1982 to today")
plt.tight_layout()
plt.show()
""")

md("""
## Ideas to try

- Forecast the September Arctic minimum from the extent in June and July.
- Compare the Antarctic record (`hemisphere == "south"`) with the Arctic: the trends differ.
- Relate the North Atlantic SST anomaly (`north_atlantic_0_60n_0_80w_anom_1991_2020`) to hurricane seasons.
- Use `greenhouse_gases_monthly.csv` (`yoy_change`) to compare growth rates of CO2, methane and nitrous oxide.
- Predict ONI a few months ahead from the daily Niño boxes, the Indian Ocean Dipole (`dmi`) and the monthly panel.
- Use `rank_lowest_for_day` / `*_rank_warmest_for_day` as targets: they only use past years, so there is no look-ahead.

Please cite the original producers (NSIDC, NOAA GML with Scripps, NOAA OISST via PSL, ERA5 from Copernicus, NOAA CPC) as listed in the dataset description.
""")

nb = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                   "language_info": {"name": "python"}},
      "nbformat": 4, "nbformat_minor": 5}
out = Path("notebooks/earth-vital-signs-starter.ipynb")
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", out)
