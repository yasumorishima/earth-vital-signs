"""Generate notebooks/todays-records/todays-records.ipynb (run once; the notebook is committed)."""
import json
from pathlib import Path

cells = []


def md(s):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": s.strip("\n")})


def code(s):
    cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
                  "source": s.strip("\n")})


md("""
# Is today a climate record?

For the latest day in each series of the daily-updated **Earth Vital Signs** dataset, this notebook asks one question: how does today compare with the same calendar day in every earlier year?

It covers the ocean surface (60S-60N and the El Niño box Niño 3.4), global air temperature, Arctic and Antarctic sea ice and CO2 at Mauna Loa. A rank of 1 means the warmest (or, for sea ice, the lowest) value ever recorded for that date.

The data are rebuilt each day from NOAA, NSIDC and Copernicus; the build code and checks are at https://github.com/yasumorishima/earth-vital-signs
""")

code("""
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

found = sorted(Path("/kaggle/input").glob("**/sst_daily.csv")) + [p / "sst_daily.csv" for p in (Path("../../data"), Path("../data"), Path("data"))]
DATA = next(p.parent for p in found if p.exists())

ice = pd.read_csv(DATA / "sea_ice_extent_daily.csv", parse_dates=["date"])
co2 = pd.read_csv(DATA / "co2_daily_mauna_loa.csv", parse_dates=["date"])
sst = pd.read_csv(DATA / "sst_daily.csv", parse_dates=["date"])
air = pd.read_csv(DATA / "air_temperature_daily.csv", parse_dates=["date"])

plt.rcParams.update({"axes.titlesize": 16, "axes.labelsize": 14, "xtick.labelsize": 12,
                     "ytick.labelsize": 12, "legend.fontsize": 12,
                     "axes.spines.top": False, "axes.spines.right": False})
GREY, RED, BLUE, BLACK = "#c8c8c8", "#d62728", "#1f77b4", "#333333"
HIGHLIGHT = ["#1f77b4", "#ff7f0e", "#2ca02c"]

# "This year" in the charts: the latest year, or the one before it during the first month of a year
# (a few days of a new year say little).
latest = sst.date.max()
Y = latest.year if (sst.date.dt.year == latest.year).sum() >= 30 else latest.year - 1
print("year shown in the charts:", Y)


def record_holders(df, col, highest=True, k=2):
    \"\"\"Years holding the most calendar-day records among the years before Y.\"\"\"
    d = df[["date", col]].dropna()
    d = d[d.date.dt.year < Y]
    key = d.date.dt.strftime("%m-%d")
    idx = d.groupby(key)[col].idxmax() if highest else d.groupby(key)[col].idxmin()
    counts = d.loc[idx, "date"].dt.year.value_counts()
    return counts.head(k), len(idx)
""")

md("""
## 1. Today's scoreboard

Each row is the latest day of one series. `rank` is the position of that value among all years on the same calendar day (1 = most extreme), `of` is how many years have that day, and `anomaly` is the difference from the 1991-2020 normal for the date.

Sea ice, the two SST means and air temperature already carry a rank column in the dataset; for Niño 3.4 and CO2 the rank is computed here the same way (same calendar day, the row's own year and earlier).
""")

code("""
def series(df, col, mask=None):
    d = df if mask is None else df[mask]
    return d[["date", col]].dropna().rename(columns={col: "value"})


def rank_on_latest(s, highest):
    \"\"\"Rank of the latest value among all years on the same month-day (1 = most extreme), the
    number of years, and the record flag as the dataset defines it: beats every earlier year
    without a tie, with at least 10 earlier years.\"\"\"
    last = s.iloc[-1]
    same = s[(s.date.dt.month == last.date.month) & (s.date.dt.day == last.date.day)]
    better = (same.value > last.value) if highest else (same.value < last.value)
    earlier = same.iloc[:-1]
    record = len(earlier) >= 10 and not better.any() and not (earlier.value == last.value).any()
    return int(better.sum()) + 1, len(same), record


north, south = ice.hemisphere == "north", ice.hemisphere == "south"
rows = [
    ("Sea surface temp, 60S-60N (°C)", series(sst, "world_60s_60n"), True,
     series(sst, "world_60s_60n_anom_1991_2020")),
    ("Niño 3.4 sea surface temp (°C)", series(sst, "nino34_5n_5s_170w_120w"), True,
     series(sst, "nino34_5n_5s_170w_120w_anom_1991_2020")),
    ("Global air temp, ERA5 (°C)", series(air, "global_2t"), True,
     series(air, "global_2t_anom_1991_2020")),
    ("Arctic sea ice (million km²)", series(ice, "extent_mkm2", north), False,
     series(ice, "extent_anom_1991_2020_mkm2", north)),
    ("Antarctic sea ice (million km²)", series(ice, "extent_mkm2", south), False,
     series(ice, "extent_anom_1991_2020_mkm2", south)),
    ("CO2 at Mauna Loa (ppm)", series(co2, "co2_ppm"), True, None),
]
board = []
for name, s, highest, anom in rows:
    r, n, record = rank_on_latest(s, highest)
    board.append({"indicator": name, "date": s.date.iloc[-1].date(), "value": round(s.value.iloc[-1], 3),
                  "anomaly": None if anom is None else round(anom.value.iloc[-1], 3),
                  "rank": r, "of": n, "record": record})
board = pd.DataFrame(board)
board
""")

code("""
# The dataset's own rank columns must agree with the computation above.
stored = {
    0: sst[["world_60s_60n_rank_warmest_for_day", "world_60s_60n_record_warm_for_day"]].iloc[-1],
    2: air[["global_rank_warmest_for_day", "global_record_warm_for_day"]].iloc[-1],
    3: ice[north][["rank_lowest_for_day", "record_low_for_day"]].iloc[-1],
    4: ice[south][["rank_lowest_for_day", "record_low_for_day"]].iloc[-1],
}
for i, (rank, flag) in stored.items():
    assert int(rank) == board["rank"][i], (board.indicator[i], rank, board["rank"][i])
    assert bool(flag) == board.record[i], (board.indicator[i], flag, board.record[i])
print("rank and record columns agree for", len(stored), "series")
print("air temperature status of the latest day:", air.status.iloc[-1])
""")

md("""
## 2. The ocean surface: this year against every year since 1982

Grey lines are earlier years, the dashed line is the 1991-2020 normal and red is the year shown. The two highlighted years are the ones that held the most calendar-day records before it (computed below).
""")

code("""
def by_doy(df, col):
    d = df[["date", col]].dropna().copy()
    d["doy"] = d.date.dt.dayofyear
    d["year"] = d.date.dt.year
    return d


def year_lines(df, col, title, ylabel, highlight=(), first_year=None, normal=True):
    d = by_doy(df, col)
    if first_year:
        d = d[d.year >= first_year]
    fig, ax = plt.subplots(figsize=(11, 6))
    for y, g in d.groupby("year"):
        if y > Y:
            continue
        if y != Y and y not in highlight:
            ax.plot(g.doy, g[col], color=GREY, lw=0.7)
    if normal:
        clim = d[d.year.between(1991, 2020)].groupby("doy")[col].mean()
        ax.plot(clim.index, clim.values, color=BLACK, lw=1.5, ls="--", label="1991-2020 normal")
    for y, c in zip(highlight, HIGHLIGHT):
        g = d[d.year == y]
        ax.plot(g.doy, g[col], color=c, lw=1.6, label=str(y))
    g = d[d.year == Y]
    ax.plot(g.doy, g[col], color=RED, lw=2.8, marker="o" if len(g) < 10 else None, label=str(Y))
    ax.set_title(title)
    ax.set_xlabel("Day of year")
    ax.set_ylabel(ylabel)
    ax.legend(frameon=False, loc="best")
    plt.tight_layout()
    plt.show()
    return d


holders, n_days = record_holders(sst, "world_60s_60n")
print(f"Calendar-day records held before {Y}:", holders.to_dict(), "of", n_days, "days")
d = year_lines(sst, "world_60s_60n", "Sea surface temperature, 60S-60N", "°C",
               highlight=tuple(sorted(holders.index)), first_year=1982)
""")

code("""
rec = sst[sst.world_60s_60n_record_warm_for_day == True]
print(f"Days in {Y} that set the record for their date:",
      int((rec.date.dt.year == Y).sum()), "of", int((sst.date.dt.year == Y).sum()))
print("Warmest single days on record:")
print(sst.nlargest(5, "world_60s_60n")[["date", "world_60s_60n", "world_60s_60n_anom_1991_2020"]].to_string(index=False))
""")

md("""
## 3. El Niño: this year's Niño 3.4 against the strongest events

Daily Niño 3.4 anomaly (5N-5S, 170W-120W) computed from the 0.25-degree OISST grid. The highlighted years are the three strongest earlier El Niño events in the OISST record, picked by their peak ONI in `enso_monthly.csv`; +0.5 °C is the usual El Niño threshold.

The anomaly is against a fixed 1991-2020 normal, so part of any record is the warming of the whole tropical ocean, not El Niño alone. NOAA's Relative ONI (`roni_anom`) and the dataset's `nino34_minus_tropics_anom` remove that part; both are printed below.
""")

code("""
enso = pd.read_csv(DATA / "enso_monthly.csv", parse_dates=["date"])
col = "nino34_5n_5s_170w_120w_anom_1991_2020"
this_year = Y
# El Nino grows through one year and peaks around the turn of the next, so a peak in January-June
# belongs to the event that developed the year before. ONI is dated by the centre month of its season.
e = enso.assign(event=enso.date.dt.year - (enso.date.dt.month < 7).astype(int))
peak = e[(e.event >= 1982) & (e.event < this_year)].groupby("event").oni_anom.max()
big = []
for y in peak.sort_values(ascending=False).index:
    if all(abs(y - b) > 1 for b in big):
        big.append(int(y))
    if len(big) == 3:
        break
big = sorted(big)
print("Strongest earlier events by peak ONI:", {y: float(peak[y]) for y in big})

d = by_doy(sst, col)
d = d[d.year >= 1982]
fig, ax = plt.subplots(figsize=(11, 6))
for y, g in d.groupby("year"):
    if y not in big and y != this_year and y <= Y:
        ax.plot(g.doy, g[col], color=GREY, lw=0.6)
for y, c in zip(big, HIGHLIGHT):
    g = d[d.year == y]
    ax.plot(g.doy, g[col], color=c, lw=1.6, label=str(y))
g = d[d.year == this_year]
ax.plot(g.doy, g[col], color=RED, lw=2.8, label=str(this_year))
ax.axhline(0.5, color=BLACK, lw=0.6, ls="--")
ax.set_title("Niño 3.4 anomaly, each year since 1982")
ax.set_xlabel("Day of year")
ax.set_ylabel("°C vs 1991-2020")
ax.legend(frameon=False, loc="upper left")
plt.tight_layout()
plt.show()

s = sst.set_index("date")[col]
print("Highest daily Niño 3.4 anomalies on record:")
print(s.nlargest(5).round(3).to_string())
print("Same date in the big years:", {y: round(float(s.get(pd.Timestamp(y, s.index[-1].month, s.index[-1].day), float("nan"))), 2)
                                       for y in big + [int(s.index[-1].year)]})
print("Latest ONI and Relative ONI:", enso[["date", "season", "oni_anom", "roni_anom"]].dropna().iloc[-1].to_dict())
print("Latest Niño 3.4 minus tropical-mean anomaly:", round(float(sst.nino34_minus_tropics_anom.iloc[-1]), 2), "°C")
""")

md("""
## 4. Global air temperature: this year against the warmest years

Daily global mean 2 m air temperature from ERA5 (Copernicus Climate Change Service), every year since 1940, with the two years that held the most calendar-day records before the year shown.
""")

code("""
holders, n_days = record_holders(air, "global_2t")
print(f"Calendar-day records held before {Y}:", holders.to_dict(), "of", n_days, "days")
d = year_lines(air, "global_2t", "Global air temperature, 1940 to today", "°C", highlight=tuple(sorted(holders.index)))
""")

code("""
rec = air[air.global_record_warm_for_day == True]
print("Days that set the record for their date, per year (last 6 years):")
print(rec.groupby(rec.date.dt.year).size().tail(6).to_string())
top3 = air[(air.date.dt.year == Y) & (air.global_rank_warmest_for_day <= 3)]
print(f"Days in {Y} in the top 3 for their date:", len(top3), "of", int((air.date.dt.year == Y).sum()))
""")

md("""
## 5. Sea ice: Arctic and Antarctic this year

Extent in each hemisphere, every year since 1979, with the year of the lowest annual minimum before the year shown highlighted. Rank 1 in `rank_lowest_for_day` means the smallest extent ever recorded for that date.
""")

code("""
for hemi, name in (("north", "Arctic"), ("south", "Antarctic")):
    h = ice[(ice.hemisphere == hemi) & (ice.date.dt.year >= 1979)]
    mins = h[h.date.dt.year < Y].groupby(h.date.dt.year).extent_mkm2.min()
    print(f"{name}: lowest annual minimum before {Y}:", int(mins.idxmin()), round(float(mins.min()), 2), "million km²")
    year_lines(h, "extent_mkm2", f"{name} sea ice extent", "Million km²", highlight=(int(mins.idxmin()),))
""")

code("""
y = Y
for hemi in ("north", "south"):
    h = ice[(ice.hemisphere == hemi) & (ice.date.dt.year == y)]
    print(f"{hemi}: record-low days in {y}:", int((h.record_low_for_day == True).sum()),
          "| days in the lowest 3:", int((h.rank_lowest_for_day <= 3).sum()), "of", len(h))
""")

md("""
## 6. How many records each indicator set this year

The share of this year's days whose value was the most extreme for its calendar date. CO2 is left out: it rises every year, so nearly every day beats the same date of earlier years (see the note below).
""")

code("""
y = Y
share = {
    "SST 60S-60N": sst[sst.date.dt.year == y].world_60s_60n_record_warm_for_day,
    "SST, whole globe": sst[sst.date.dt.year == y].world_90s_90n_record_warm_for_day,
    "Air temperature": air[air.date.dt.year == y].global_record_warm_for_day,
    "Arctic sea ice (low)": ice[north & (ice.date.dt.year == y)].record_low_for_day,
    "Antarctic sea ice (low)": ice[south & (ice.date.dt.year == y)].record_low_for_day,
}
share = pd.Series({k: 100 * (v == True).mean() for k, v in share.items() if len(v)}).sort_values()
fig, ax = plt.subplots(figsize=(11, 6))
ax.barh(share.index, share.values, color=[RED if v > 0 else GREY for v in share.values])
for i, v in enumerate(share.values):
    ax.text(v + 1, i, f"{v:.0f}%", va="center", fontsize=12)
ax.set_xlim(0, 105)
ax.set_title(f"Share of days in {y} that set a record for their date")
ax.set_xlabel("% of days")
plt.tight_layout()
plt.show()
""")

md("""
## Notes

- Ranks use only the row's own year and earlier ones, so later years never change an earlier day's rank. (A day's own rank can still move while its value is inside the source's revision window.) Feb 29 is ranked among leap years.
- CO2 is above the same date of every earlier year on almost every day, because it rises every year. The dataset's `record_high` asks a different question: is the value above every earlier daily value (a new all-time high)? Since 2010 that has happened only between January and June, most often in April, as CO2 climbs to its yearly peak. The informative signal is `yoy_change_ppm`, the growth over the same date a year earlier.
- The most recent days of air temperature are preliminary (`status` column) and SST is preliminary for about two weeks; a record on the newest day can still change slightly.
- Please cite the original producers (NSIDC, NOAA GML with Scripps, NOAA OISST via PSL, ERA5 from Copernicus) as listed in the dataset description.
""")

nb = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                   "language_info": {"name": "python"}},
      "nbformat": 4, "nbformat_minor": 5}
out = Path("notebooks/todays-records/todays-records.ipynb")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
meta = {
    "id": "yasunorim/earth-vital-signs-is-today-a-climate-record",
    "title": "Earth Vital Signs: is today a climate record?",
    "code_file": "todays-records.ipynb",
    "language": "python",
    "kernel_type": "notebook",
    "is_private": False,
    "enable_gpu": False,
    "enable_tpu": False,
    "enable_internet": False,
    "dataset_sources": ["yasunorim/earth-vital-signs-daily"],
    "competition_sources": [],
    "kernel_sources": [],
    "model_sources": [],
    "keywords": ["earth and nature", "environment", "time series analysis", "data visualization"],
}
(out.parent / "kernel-metadata.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
print("wrote", out)
