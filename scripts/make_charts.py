"""Draw the README charts from data/.

Usage: python scripts/make_charts.py <out_dir>

Writes race.gif (this year against the warmest year, year-to-date) and air_temperature.png,
sea_ice.png, sst.png, co2.png. The workflow pushes them to the `charts` branch, which the
README links to, so they follow the data without adding images to the history of main.
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.animation import FuncAnimation, PillowWriter  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
GREY, RED, BLACK, BLUE = "#c4c4c4", "#d62728", "#222222", "#1f77b4"
MONTHS = ([1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335],
          ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
SOURCE = "github.com/yasumorishima/earth-vital-signs"

plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
    "axes.titlesize": 15, "axes.titleweight": "bold", "axes.labelsize": 12,
    "xtick.labelsize": 11, "ytick.labelsize": 11, "legend.fontsize": 11,
    "axes.spines.top": False, "axes.spines.right": False,
})


def by_year(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(year=df.date.dt.year, doy=df.date.dt.dayofyear)


def full_years(df: pd.DataFrame, current: int) -> pd.Series:
    """Years with (nearly) every day, excluding the current one."""
    n = df[df.year < current].groupby("year").size()
    return n[n >= 300].index


def footer(fig, text: str) -> None:
    fig.text(0.99, 0.01, f"{text} · {SOURCE}", ha="right", va="bottom", fontsize=9, color="#777777")


def spaghetti(ax, df: pd.DataFrame, col: str, current: int, highlight: int | None, label: str | None) -> None:
    """One grey line per complete year, the highlighted year in black, the current year in red."""
    years = full_years(df, current)
    for y, g in df[df.year.isin(years)].groupby("year"):
        if y != highlight:
            ax.plot(g.doy, g[col], color=GREY, lw=0.6, alpha=0.7)
    if highlight is not None:
        h = df[df.year == highlight]
        ax.plot(h.doy, h[col], color=BLACK, lw=1.8, label=label)
    t = df[df.year == current]
    ax.plot(t.doy, t[col], color=RED, lw=2.6, label=f"{current}")
    last = t.iloc[-1]
    ax.plot(last.doy, last[col], "o", color=RED, ms=6)
    ax.set_xlim(1, 366)
    ax.set_xticks(*MONTHS)
    ax.legend(loc="best", frameon=False)


def air_chart(out: Path) -> None:
    air = by_year(pd.read_csv(DATA / "air_temperature_daily.csv", parse_dates=["date"]))
    cur = int(air.year.max())
    years = full_years(air, cur)
    means = air[air.year.isin(years)].groupby("year").global_2t_anom_preindustrial.mean()
    rec = int(means.idxmax())
    fig, ax = plt.subplots(figsize=(11, 5.6))
    spaghetti(ax, air, "global_2t_anom_preindustrial", cur, rec, f"{rec} (warmest year, {means.max():.2f} C)")
    ax.axhline(1.5, color=BLUE, ls="--", lw=1.1)
    ax.text(4, 1.53, "1.5 C", color=BLUE, fontsize=11)
    last = air.iloc[-1]
    ax.set_ylabel("Above 1850-1900 (C)")
    ax.set_title(f"Global air temperature, every day since {int(years.min())} "
                 f"(latest {last.date:%d %b %Y}: {last.global_2t_anom_preindustrial:+.2f} C)")
    footer(fig, "ERA5 via Copernicus Climate Pulse")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out / "air_temperature.png", dpi=100)
    plt.close(fig)


def sea_ice_chart(out: Path) -> None:
    ice = by_year(pd.read_csv(DATA / "sea_ice_extent_daily.csv", parse_dates=["date"]))
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))
    for ax, hemi, name in zip(axes, ("north", "south"), ("Arctic", "Antarctic")):
        h = ice[ice.hemisphere == hemi].dropna(subset=["extent_mkm2"])
        cur = int(h.year.max())
        years = full_years(h, cur)
        mins = h[h.year.isin(years)].groupby("year").extent_mkm2.min()
        low = int(mins.idxmin())
        spaghetti(ax, h, "extent_mkm2", cur, low, f"{low} (lowest minimum)")
        last = h.iloc[-1]
        ax.set_title(f"{name} sea ice extent (latest {last.date:%d %b}: {last.extent_mkm2:.2f} million km²)")
        ax.set_ylabel("Million km²")
    footer(fig, "NSIDC Sea Ice Index")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out / "sea_ice.png", dpi=100)
    plt.close(fig)


def sst_chart(out: Path) -> None:
    sst = by_year(pd.read_csv(DATA / "sst_daily.csv", parse_dates=["date"], low_memory=False))
    cur = int(sst.year.max())
    years = full_years(sst, cur)
    means = sst[sst.year.isin(years)].groupby("year").world_60s_60n.mean()
    rec = int(means.idxmax())
    fig, ax = plt.subplots(figsize=(11, 5.6))
    spaghetti(ax, sst, "world_60s_60n", cur, rec, f"{rec} (warmest year)")
    last = sst.iloc[-1]
    ax.set_ylabel("Sea surface temperature, 60S-60N (C)")
    ax.set_title(f"Ocean surface temperature since {int(years.min())} "
                 f"(latest {last.date:%d %b %Y}: {last.world_60s_60n:.2f} C)")
    footer(fig, "NOAA OISST v2.1")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out / "sst.png", dpi=100)
    plt.close(fig)


def co2_chart(out: Path) -> None:
    co2 = pd.read_csv(DATA / "co2_daily_mauna_loa.csv", parse_dates=["date"]).dropna(subset=["co2_ppm"])
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(co2.date, co2.co2_ppm, color=BLACK, lw=0.5, alpha=0.8)
    last = co2.iloc[-1]
    ax.plot(last.date, last.co2_ppm, "o", color=RED, ms=7)
    ax.set_ylabel("CO2 at Mauna Loa (ppm)")
    ax.set_title(f"Daily CO2 at Mauna Loa since {co2.date.min():%Y} "
                 f"(latest {last.date:%d %b %Y}: {last.co2_ppm:.1f} ppm)")
    footer(fig, "NOAA GML / Scripps")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out / "co2.png", dpi=100)
    plt.close(fig)


def race_gif(out: Path) -> None:
    """The notebook's race: this year's year-to-date mean against the warmest year's."""
    air = by_year(pd.read_csv(DATA / "air_temperature_daily.csv", parse_dates=["date"]))
    latest = air.date.max()
    # in January a few days say little, so the race stays on the year before for its first month
    y = latest.year if (air.year == latest.year).sum() >= 30 else latest.year - 1
    done = air[air.year.isin(full_years(air, y))].groupby("year").global_2t_anom_preindustrial.mean()
    rec, rec_mean = int(done.idxmax()), float(done.max())
    r = air[air.year == rec].set_index("doy").global_ytd_anom_preindustrial
    t = air[air.year == y].set_index("doy").global_ytd_anom_preindustrial
    end = int(t.index.max())
    steps = list(range(7, end + 1, 7)) + ([end] if end % 7 else [])
    lo, hi = min(r.min(), t.min()) - 0.1, max(r.max(), t.max()) + 0.1

    fig, ax = plt.subplots(figsize=(8, 4.6))
    fig.subplots_adjust(left=0.11, right=0.97, top=0.88, bottom=0.12)  # fixed frame: nothing moves
    ax.set_xlim(1, 366)
    ax.set_ylim(lo, hi)
    ax.set_xticks([1, 91, 182, 274], ["Jan", "Apr", "Jul", "Oct"])
    ax.set_ylabel("Year-to-date mean above 1850-1900 (C)", fontsize=10)
    ax.set_title(f"Will {y} beat {rec}, the warmest year ({rec_mean:.2f} C)?", fontsize=14)
    ax.axhline(rec_mean, color=BLACK, ls=":", lw=1)
    (lr,) = ax.plot([], [], color=BLACK, lw=2, label=f"{rec}")
    (lt,) = ax.plot([], [], color=RED, lw=2.8, label=f"{y}")
    ax.legend(loc="lower right", frameon=False)
    stamp = ax.text(0.03, 0.9, "", transform=ax.transAxes, fontsize=16, fontweight="bold")
    gap = ax.text(0.03, 0.8, "", transform=ax.transAxes, fontsize=12)
    fig.text(0.99, 0.01, f"ERA5 via Copernicus Climate Pulse · {SOURCE}", ha="right", fontsize=8, color="#777777")

    def frame(d):
        lr.set_data(r.index[r.index <= d], r[r.index <= d])
        lt.set_data(t.index[t.index <= d], t[t.index <= d])
        stamp.set_text((pd.Timestamp(y, 1, 1) + pd.Timedelta(days=d - 1)).strftime("%d %b %Y"))
        diff = t.get(d, np.nan) - r.get(d, np.nan)
        gap.set_text(f"{y} minus {rec}: {diff:+.2f} C")
        gap.set_color(RED if diff > 0 else BLACK)
        return lr, lt, stamp, gap

    # hold the last frame for about two seconds before the loop restarts
    FuncAnimation(fig, frame, frames=steps + [steps[-1]] * 12, blit=False).save(
        out / "race.gif", writer=PillowWriter(fps=6), dpi=80)
    plt.close(fig)


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for draw in (race_gif, air_chart, sea_ice_chart, sst_chart, co2_chart):
        draw(out)
    for p in sorted(out.iterdir()):
        print(f"{p.name}: {p.stat().st_size / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
