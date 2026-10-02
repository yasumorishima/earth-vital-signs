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

Three charts from the daily-updated **Earth Vital Signs** dataset: Arctic sea ice by year, the Mauna Loa CO2 curve, and ocean surface temperature against its 1991-2020 normal.

Every file is rebuilt each day from NOAA and NSIDC; the build code and checks are at https://github.com/yasumorishima/earth-vital-signs
""")

code("""
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

candidates = [Path("/kaggle/input/earth-vital-signs-daily"), Path("../data"), Path("data")]
DATA = next(p for p in candidates if (p / "sst_daily.csv").exists())

ice = pd.read_csv(DATA / "sea_ice_extent_daily.csv", parse_dates=["date"])
co2 = pd.read_csv(DATA / "co2_daily_mauna_loa.csv", parse_dates=["date"])
sst = pd.read_csv(DATA / "sst_daily.csv", parse_dates=["date"])
print({name: (len(df), str(df.date.max().date())) for name, df in
       [("sea ice", ice), ("co2", co2), ("sst", sst)]})

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
## Ideas to try

- Forecast the September Arctic minimum from the extent in June and July.
- Compare the Antarctic record (`hemisphere == "south"`) with the Arctic: the trends differ.
- Relate the North Atlantic SST anomaly (`north_atlantic_0_60n_0_80w_anom_1991_2020`) to hurricane seasons.
- Use `greenhouse_gases_monthly.csv` to compare growth rates of CO2, methane and nitrous oxide.

Please cite the original producers (NSIDC, NOAA GML with Scripps, NOAA OISST/PSL) as listed in the dataset description.
""")

nb = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                   "language_info": {"name": "python"}},
      "nbformat": 4, "nbformat_minor": 5}
out = Path("notebooks/earth-vital-signs-starter.ipynb")
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", out)
