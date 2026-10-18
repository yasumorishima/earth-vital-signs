"""Generate notebooks/hottest-year/hottest-year.ipynb (run once; the notebook is committed and re-run on Kaggle
after every daily dataset version)."""
import json
from pathlib import Path

cells = []


def md(s):
    cells.append({"cell_type": "markdown", "id": f"c{len(cells):02d}", "metadata": {}, "source": s.strip("\n")})


def code(s):
    cells.append({"cell_type": "code", "id": f"c{len(cells):02d}", "metadata": {}, "execution_count": None,
                  "outputs": [], "source": s.strip("\n")})


md("""
# Will this year be the warmest on record? A year-to-date race

The daily **Earth Vital Signs** dataset carries the global mean air temperature (ERA5, 1940 to yesterday) as an anomaly above the **1850-1900 pre-industrial level**, computed the way the Copernicus Climate Change Service does it (a day-of-year offset, not a flat +0.88 C), plus its **year-to-date mean** and that mean's **rank among all years at the same date**.

This notebook uses those columns to follow the race between this year and the record year, to show how far the rest of the year would have to run to take the record, and to look at what drives the year-to-year swings (El Niño, greenhouse gas forcing, the Sun).

It is re-run after every daily version of the dataset, so the numbers below are always up to date. Build code and checks: https://github.com/yasumorishima/earth-vital-signs
""")

code("""
from pathlib import Path
import base64, io
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
from IPython.display import HTML, display

found = sorted(Path("/kaggle/input").glob("**/air_temperature_daily.csv")) + [
    p / "air_temperature_daily.csv" for p in (Path("../../data"), Path("../data"), Path("data"))]
DATA = next(p.parent for p in found if p.exists())

air = pd.read_csv(DATA / "air_temperature_daily.csv", parse_dates=["date"])
panel = pd.read_csv(DATA / "climate_monthly_panel.csv", parse_dates=["date"])
drivers = pd.read_csv(DATA / "climate_drivers_monthly.csv", parse_dates=["date"])

plt.rcParams.update({"axes.titlesize": 16, "axes.labelsize": 14, "xtick.labelsize": 12,
                     "ytick.labelsize": 12, "legend.fontsize": 12,
                     "axes.spines.top": False, "axes.spines.right": False})
GREY, RED, BLUE, BLACK = "#c8c8c8", "#d62728", "#1f77b4", "#333333"

air["year"] = air.date.dt.year
air["doy"] = air.date.dt.dayofyear
latest = air.date.max()
# the race is about the latest year, or the year before it during the first month of a year (a few days say little)
Y = latest.year if (air.year == latest.year).sum() >= 30 else latest.year - 1
latest = air[air.year == Y].date.max()
done = air[air.year < Y].groupby("year").global_2t_anom_preindustrial.mean()
REC_YEAR, REC = int(done.idxmax()), float(done.max())
today = air[air.date == latest].iloc[0]
print(f"latest day: {latest.date()}  ({'preliminary' if today.status == 'preliminary' else 'final'})")
print(f"record year so far: {REC_YEAR}, annual mean {REC:.2f} C above 1850-1900")
print(f"{Y} year-to-date: {today.global_ytd_anom_preindustrial:.3f} C, rank {today.global_ytd_rank_warmest_for_day} "
      f"of {air[(air.doy == today.doy) & (air.year <= Y)].year.nunique()} years at this date")
""")

md("""
## 1. The race so far

Each grey line is one year's year-to-date mean (from 1 January to the date on the x-axis). The record year and this year are highlighted. The line ends where it ends because the year-to-date mean on 31 December *is* the annual mean.
""")

code("""
fig, ax = plt.subplots(figsize=(12, 6.5))
for y, g in air[air.year < Y].groupby("year"):
    if y in (REC_YEAR,):
        continue
    ax.plot(g.doy, g.global_ytd_anom_preindustrial, color=GREY, lw=0.6, alpha=0.6)
r = air[air.year == REC_YEAR]
ax.plot(r.doy, r.global_ytd_anom_preindustrial, color=BLACK, lw=2.2, label=f"{REC_YEAR} (record, {REC:.2f} C)")
t = air[air.year == Y]
ax.plot(t.doy, t.global_ytd_anom_preindustrial, color=RED, lw=3, label=f"{Y} so far")
ax.axhline(1.5, color=BLUE, ls="--", lw=1.2)
ax.text(362, 1.48, "1.5 C above pre-industrial", color=BLUE, fontsize=12, va="top", ha="right")
ax.set_xlim(1, 366)
ax.set_xticks([1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335],
              ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
ax.set_ylabel("Year-to-date mean above 1850-1900 (C)")
ax.set_title(f"{Y} against every year since 1940: year-to-date global temperature")
ax.legend(loc="lower right", frameon=False)
plt.tight_layout()
plt.show()
""")

md("""
## 2. The same race, day by day

The animation replays this year against the record year one week at a time. The counter in the corner is the date.
""")

code("""
r = air[air.year == REC_YEAR].set_index("doy").global_ytd_anom_preindustrial
t = air[air.year == Y].set_index("doy").global_ytd_anom_preindustrial
steps = list(range(7, int(t.index.max()) + 1, 7)) + [int(t.index.max())]
lo = min(r.min(), t.min()) - 0.1
hi = max(r.max(), t.max()) + 0.1

fig, ax = plt.subplots(figsize=(9, 5.2))
fig.subplots_adjust(left=0.12, right=0.97, top=0.88, bottom=0.12)  # fixed frame: nothing moves between frames
ax.set_xlim(1, 366); ax.set_ylim(lo, hi)
ax.set_xticks([1, 91, 182, 274], ["Jan", "Apr", "Jul", "Oct"])
ax.set_ylabel("Year-to-date mean (C)")
ax.set_title(f"{Y} vs {REC_YEAR}: who is warmer so far?")
(lr,) = ax.plot([], [], color=BLACK, lw=2.2, label=str(REC_YEAR))
(lt,) = ax.plot([], [], color=RED, lw=3, label=str(Y))
ax.legend(loc="lower right", frameon=False)
stamp = ax.text(0.03, 0.92, "", transform=ax.transAxes, fontsize=18, fontweight="bold", color=BLACK)
gap = ax.text(0.03, 0.82, "", transform=ax.transAxes, fontsize=13)


def frame(d):
    lr.set_data(r.index[r.index <= d], r[r.index <= d])
    lt.set_data(t.index[t.index <= d], t[t.index <= d])
    stamp.set_text((pd.Timestamp(Y, 1, 1) + pd.Timedelta(days=d - 1)).strftime("%d %b"))
    diff = t.get(d, np.nan) - r.get(d, np.nan)
    gap.set_text(f"{Y} minus {REC_YEAR}: {diff:+.2f} C")
    return lr, lt, stamp, gap


anim = FuncAnimation(fig, frame, frames=steps + [steps[-1]] * 6, blit=False)
anim.save("race.gif", writer=PillowWriter(fps=6), dpi=80)
plt.close(fig)
b = base64.b64encode(Path("race.gif").read_bytes()).decode()
display(HTML(f'<img src="data:image/gif;base64,{b}">'))
print(f"race.gif: {Path('race.gif').stat().st_size / 1e6:.2f} MB, {len(steps) + 6} frames at 6 fps")
""")

md("""
## 3. What the rest of the year would need

To beat the record, the remaining days of this year must average at least

`(record x days in year - sum so far) / days left`.

This is arithmetic, not a forecast: it compares the pace required with the pace this year has actually been running at.
""")

code("""
t = air[air.year == Y]
n_year = 366 if pd.Timestamp(Y, 12, 31).dayofyear == 366 else 365
left = n_year - len(t)
print(f"days so far {len(t)}, days left {left}")
if left <= 0:  # 31 December: the race is over
    print(f"{Y} is complete: annual mean {t.global_2t_anom_preindustrial.mean():.3f} C against the record {REC:.3f} C")
else:
    need = (REC * n_year - t.global_2t_anom_preindustrial.sum()) / left
    recent = {k: t.global_2t_anom_preindustrial.tail(k).mean() for k in (30, 90)}
    print(f"needed average for the rest of {Y}: {need:.3f} C above 1850-1900")
    for k, v in recent.items():
        print(f"  last {k} days averaged {v:.3f} C")
    same_days = air[(air.year == REC_YEAR) & (air.doy > len(t))].global_2t_anom_preindustrial.mean()
    print(f"  the same remaining days of {REC_YEAR} averaged {same_days:.3f} C")

    fig, ax = plt.subplots(figsize=(10, 4.8))
    labels = [f"needed for the\\nrest of {Y}", f"last 90 days\\nof {Y}", f"last 30 days\\nof {Y}",
              f"same days\\nof {REC_YEAR}"]
    vals = [need, recent[90], recent[30], same_days]
    ax.bar(labels, vals, color=[RED, GREY, GREY, BLACK], width=0.6)
    for i, v in enumerate(vals):
        ax.text(i, v + 0.02, f"{v:.2f}", ha="center", fontsize=13)
    ax.set_ylabel("Mean above 1850-1900 (C)")
    ax.set_ylim(min(0, min(vals) * 1.2), max(vals) * 1.2)
    ax.set_title(f"Pace needed for {Y} to beat {REC_YEAR}")
    plt.tight_layout()
    plt.show()
""")

md("""
## 4. Days above 1.5 C

`global_above_1_5c_preindustrial` flags each day above 1.5 C. A single day (or year) above 1.5 C is **not** a breach of the Paris Agreement, which refers to warming averaged over about 20 years; the count shows how often individual days already get there.
""")

code("""
cnt = air.groupby("year").global_above_1_5c_preindustrial.sum()
cnt = cnt[cnt.index >= 1980]
fig, ax = plt.subplots(figsize=(12, 4.8))
ax.bar(cnt.index, cnt.values, color=[RED if y == Y else (BLACK if y == REC_YEAR else GREY) for y in cnt.index])
ax.set_ylabel("Days above 1.5 C")
first = int(cnt[cnt > 0].index.min())
ax.set_title(f"Days above 1.5 C per year: none before {first} ({Y} through {latest:%d %b})")
plt.tight_layout()
plt.show()
print(cnt.tail(6).to_string())
""")

md("""
## 5. What moves the year-to-year swings

The dataset's `climate_drivers_monthly.csv` has the effective radiative forcing of CO2, CH4 and N2O (NOAA's AGGI method), total solar irradiance and the PDO; `climate_monthly_panel.csv` adds ENSO. A classic question is how many months global temperature lags El Niño. Below, the monthly temperature anomaly is first regressed on the greenhouse gas forcing (the long-term rise), and the remainder is correlated with RONI shifted by 0 to 9 months.
""")

code("""
m = panel[["date", "air_temp_global_anom_preindustrial", "roni_anom", "erf_co2_ch4_n2o_wm2", "tsi_wm2"]].dropna(
    subset=["air_temp_global_anom_preindustrial", "erf_co2_ch4_n2o_wm2"])
X = np.c_[np.ones(len(m)), m.erf_co2_ch4_n2o_wm2]
beta, *_ = np.linalg.lstsq(X, m.air_temp_global_anom_preindustrial, rcond=None)
m = m.assign(resid=m.air_temp_global_anom_preindustrial - X @ beta)
print(f"{len(m)} months {m.date.min():%Y-%m}..{m.date.max():%Y-%m}; "
      f"warming per W m-2 of GHG forcing (simple fit, not a climate sensitivity): {beta[1]:.2f} C")
p = panel.set_index("date").roni_anom
lags = range(0, 10)
corr = [m.resid.corr(m.date.map(p.shift(k))) for k in lags]
best = int(np.nanargmax(corr))

fig, ax = plt.subplots(figsize=(10, 4.8))
ax.bar(list(lags), corr, color=[RED if k == best else GREY for k in lags])
ax.set_xlabel("RONI leads global temperature by ... months")
ax.set_ylabel("Correlation")
ax.set_title(f"Global temperature follows El Niño by about {best} months")
plt.tight_layout()
plt.show()
""")

md("""
## Notes

- Air temperature is ERA5 reanalysis from Copernicus Climate Pulse; the most recent days are preliminary and may change slightly. Yearly means of `global_2t_anom_preindustrial` reproduce C3S's published 1.48 C (2023), 1.60 C (2024) and 1.47 C (2025); the build stops if they do not.
- The year-to-date rank counts only the row's own year and earlier ones, so it can be used as a target without look-ahead.
- The greenhouse gas forcing starts in 2001 (NOAA's global N2O series) and excludes halocarbons, aerosols and ozone.
- Sources and citations: see the dataset page.
""")

nb = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                   "language_info": {"name": "python"}},
      "nbformat": 4, "nbformat_minor": 5}
out = Path(__file__).resolve().parent.parent / "notebooks" / "hottest-year"
out.mkdir(parents=True, exist_ok=True)
(out / "hottest-year.ipynb").write_text(json.dumps(nb, indent=1) + "\n", encoding="utf-8")
meta = {
    "id": "yasunorim/earth-vital-signs-will-this-year-be-the-warmest",
    "title": "Earth Vital Signs: will this year be the warmest?",
    "code_file": "hottest-year.ipynb", "language": "python", "kernel_type": "notebook", "is_private": False,
    "enable_gpu": False, "enable_tpu": False, "enable_internet": False,
    "dataset_sources": ["yasunorim/earth-vital-signs-daily"], "competition_sources": [], "kernel_sources": [],
    "model_sources": [], "keywords": ["earth and nature", "environment", "time series analysis", "data visualization"],
}
(out / "kernel-metadata.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
print("wrote", out)
