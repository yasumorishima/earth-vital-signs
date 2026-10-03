"""Fetch, derive and gate the Earth Vital Signs tables.

Writes the committed history in data/ and the Kaggle upload folder in _build/.
Every table passes its gates before anything is written; a failed gate exits non-zero
and leaves data/ untouched, so a broken upstream never replaces good history.

Sources (all redistributable with attribution; see README):
  NSIDC Sea Ice Index v4 (G02135)            daily sea ice extent, both hemispheres
  NOAA GML Mauna Loa and global networks     CO2 daily/monthly, CH4/N2O/SF6 monthly
  NOAA OISST v2.1 via NOAA PSL               daily SST, area-weighted means derived here
  ERA5 via Copernicus Climate Pulse          daily global 2 m air temperature; 60S-60N SST as a cross-check
  NOAA CPC                                   ONI and RONI (monthly), AO/NAO/PNA/AAO (daily)
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BUILD = ROOT / "_build"
UA = "earth-vital-signs (+https://github.com/yasumorishima/earth-vital-signs)"
TODAY = dt.datetime.now(dt.timezone.utc).date()

NSIDC = "https://noaadata.apps.nsidc.org/NOAA/G02135/{h}/daily/data/{H}_seaice_extent_daily_v4.0.csv"
GML = "https://gml.noaa.gov/webdata/ccgg/trends/{gas}/{file}"
PSL_SST = "https://psl.noaa.gov/thredds/fileServer/Datasets/noaa.oisst.v2.highres/sst.day.mean.{y}.nc"
NCEI_SST = ("https://www.ncei.noaa.gov/data/sea-surface-temperature-optimum-interpolation/"
            "v2.1/access/avhrr/{ym}/oisst-avhrr-v02r01.{ymd}.nc")
# NCEP/NCAR Reanalysis 1 stopped on 2026-03-17 (PSL data notice), so air temperature comes from ERA5
PULSE = "https://sites.ecmwf.int/data/climatepulse/data/series/era5_daily_series_{v}.csv"
CPC = "https://www.cpc.ncep.noaa.gov/data/indices/{f}"
CPC_SST_WEEKLY = CPC.format(f="wksst9120.for")
CPC_TELE = "https://ftp.cpc.ncep.noaa.gov/cwlinks/norm.daily.{f}_current.csv"

FAIL: list[str] = []
STALE: list[str] = []


def gate(ok: bool, msg: str) -> None:
    print(("PASS " if ok else "FAIL ") + msg, flush=True)
    if not ok:
        FAIL.append(msg)


def fresh(ok: bool, msg: str) -> None:
    """Freshness only warns: a source that stopped returns the same rows as before, which cannot
    damage published history, and blocking on it would freeze the other tables too (e.g. the
    Mauna Loa outage of 2022). The warning is shown on the workflow run."""
    print(("PASS " if ok else "STALE ") + msg, flush=True)
    if not ok:
        STALE.append(msg)
        print(f"::warning title=stale source::{msg}", flush=True)


def get(url: str, dest: Path | None = None, tries: int = 4) -> bytes | None:
    """Small files are returned as bytes. Large files go to dest and are resumed with HTTP Range:
    the PSL server closes each connection after roughly 11 MB, so a 356 MB year arrives in pieces.
    The file must end at exactly the advertised length or this raises."""
    if dest is None:
        for i in range(tries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=600) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code < 500:  # 4xx will not change on retry
                    raise
                if i == tries - 1:
                    raise
                print(f"retry {i + 1} {url}: {e}", flush=True)
                time.sleep(10 * (i + 1))
            except Exception as e:  # network errors are retried, then raised
                if i == tries - 1:
                    raise
                print(f"retry {i + 1} {url}: {e}", flush=True)
                time.sleep(10 * (i + 1))
    # PSL rewrites the current year's file daily and ignores If-Range (a stale validator still
    # gets 206, measured 2026-10-03), so every piece must carry the same Last-Modified and length
    # as the first; otherwise the download starts over instead of splicing two versions.
    total = first_lm = None
    stalls = restarts = 0
    dest.write_bytes(b"")
    while True:
        have = dest.stat().st_size
        if total is not None and have == total:
            return None
        if total is not None and have > total:
            raise OSError(f"{url}: got {have} bytes, more than {total}")
        hdr = {"User-Agent": UA}
        if have:
            hdr["Range"] = f"bytes={have}-"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=600) as r:
                if have and r.status != 206:
                    raise OSError(f"{url}: server ignored Range (status {r.status})")
                lm = r.headers.get("Last-Modified")
                size = (int(r.headers["Content-Range"].rsplit("/", 1)[1]) if have
                        else int(r.headers["Content-Length"]))
                if total is None:
                    total, first_lm = size, lm
                elif (lm, size) != (first_lm, total):
                    restarts += 1
                    if restarts > 3:
                        raise OSError(f"{url}: file kept changing during download")
                    print(f"{url}: changed mid-download ({first_lm} -> {lm}); starting over", flush=True)
                    dest.write_bytes(b"")
                    total = first_lm = None
                    continue
                with open(dest, "ab") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise
        except OSError as e:
            if "ignored Range" in str(e) or "more than" in str(e) or "kept changing" in str(e):
                raise
            # a dropped connection is expected; progress is checked below
        if dest.stat().st_size == have:
            stalls += 1
            if stalls >= tries:
                raise OSError(f"{url}: no progress at {have} of {total} bytes")
            time.sleep(10 * stalls)
        else:
            stalls = 0


def lag_days(last) -> int:
    return (TODAY - pd.Timestamp(last).date()).days


# ---------------------------------------------------------------- derived columns
# Every derived column depends only on the row's own date and earlier dates (or on the fixed
# 1991-2020 normal), so a published row never changes when later data arrive.
def _md(dates: pd.Series) -> pd.Series:
    return dates.dt.strftime("%m-%d")


def anom_1991_2020(df: pd.DataFrame, col: str, by: str | None = None) -> pd.Series:
    """Value minus its 1991-2020 mean for the same calendar day (Feb 29 uses Feb 28's normal)."""
    doy = _md(df.date).replace("02-29", "02-28")
    base = df.date.dt.year.between(1991, 2020)
    keys = [doy] if by is None else [df[by], doy]
    bkeys = [doy[base]] if by is None else [df[by][base], doy[base]]
    norm = df[base].groupby(bkeys)[col].mean()
    idx = pd.MultiIndex.from_arrays([k.values for k in keys]) if by else doy.values
    return (df[col] - norm.reindex(idx).values).round(4)


def day_rank(df: pd.DataFrame, col: str, highest: bool, by: str | None = None,
             min_prior: int = 10) -> tuple[pd.Series, pd.Series]:
    """Rank of the value among the same calendar day of this and all earlier years
    (1 = highest if `highest`, else lowest), and a record flag that is True when the value beats
    every earlier year. The flag is empty until `min_prior` earlier years exist for that day."""
    rank = np.zeros(len(df), dtype=np.int64)       # 0 = empty
    rec = np.full(len(df), -1, dtype=np.int8)      # -1 = empty
    vals = df[col].to_numpy(dtype=float)
    dates = df.date.to_numpy()
    keys = [_md(df.date).to_numpy()] if by is None else [df[by].to_numpy(), _md(df.date).to_numpy()]
    # plain arrays as keys, so the group positions are positions whatever the frame's index is
    for pos in df.reset_index(drop=True).groupby(keys, sort=False).indices.values():
        pos = pos[np.argsort(dates[pos], kind="stable")]
        v = vals[pos]
        for i, p in enumerate(pos):
            x = v[i]
            if np.isnan(x):
                continue
            prior = v[:i][~np.isnan(v[:i])]
            better = int((prior > x).sum() if highest else (prior < x).sum())
            rank[p] = better + 1
            if len(prior) >= min_prior:
                rec[p] = int(better == 0 and not (prior == x).any())
    r = pd.Series(rank, index=df.index).replace(0, pd.NA).astype("Int64")
    f = pd.Series(pd.array(np.where(rec < 0, None, rec == 1).tolist(), dtype="boolean"), index=df.index)
    return r, f


def yoy(df: pd.DataFrame, col: str, by: str | None = None, months: bool = False) -> pd.Series:
    """Value minus the value on the same calendar day (or month) one year earlier; empty if that
    earlier value is missing (and on Feb 29)."""
    key = ["date"] if by is None else [by, "date"]
    prev = df[key + [col]].copy()
    if months:
        prev["date"] = prev.date + pd.DateOffset(years=1)
    else:
        ok = _md(prev.date) != "02-29"
        prev = prev[ok]
        prev["date"] = pd.to_datetime(dict(year=prev.date.dt.year + 1, month=prev.date.dt.month,
                                           day=prev.date.dt.day))
    m = df[key].merge(prev.rename(columns={col: "_prev"}), on=key, how="left")
    return pd.Series((df[col].to_numpy() - m["_prev"].to_numpy()), index=df.index).round(4)


# ---------------------------------------------------------------- sea ice
def sea_ice() -> pd.DataFrame:
    parts = []
    for h, H in (("north", "N"), ("south", "S")):
        raw = get(NSIDC.format(h=h, H=H)).decode()
        df = pd.read_csv(io.StringIO(raw), skiprows=[1], skipinitialspace=True)
        df.columns = [c.strip() for c in df.columns]
        df["date"] = pd.to_datetime(dict(year=df.Year, month=df.Month, day=df.Day))
        df = df.rename(columns={"Extent": "extent_mkm2", "Missing": "missing_mkm2"})
        df["hemisphere"] = h
        parts.append(df[["date", "hemisphere", "extent_mkm2", "missing_mkm2"]])
    out = pd.concat(parts).sort_values(["hemisphere", "date"]).reset_index(drop=True)
    # NSIDC charts use a trailing 5-day mean; only computed over 5 consecutive daily values
    # (the 1978-1987 record is every other day, where it stays empty).
    out["extent_5day_mean_mkm2"] = np.nan
    for h, g in out.groupby("hemisphere"):
        s = g.set_index("date")["extent_mkm2"].asfreq("D")
        m = s.rolling(5, min_periods=5).mean()
        out.loc[g.index, "extent_5day_mean_mkm2"] = m.reindex(g.date).round(4).values
    # global = north + south on days both hemispheres are reported
    wide = out.pivot(index="date", columns="hemisphere")
    glob = pd.DataFrame({
        "date": wide.index,
        "hemisphere": "global",
        "extent_mkm2": (wide["extent_mkm2"]["north"] + wide["extent_mkm2"]["south"]).round(3).values,
        "missing_mkm2": (wide["missing_mkm2"]["north"] + wide["missing_mkm2"]["south"]).round(3).values,
        "extent_5day_mean_mkm2": (wide["extent_5day_mean_mkm2"]["north"]
                                  + wide["extent_5day_mean_mkm2"]["south"]).round(4).values,
    }).dropna(subset=["extent_mkm2"])
    out = pd.concat([out, glob]).sort_values(["hemisphere", "date"]).reset_index(drop=True)
    out["extent_anom_1991_2020_mkm2"] = anom_1991_2020(out, "extent_mkm2", by="hemisphere")
    out["rank_lowest_for_day"], out["record_low_for_day"] = day_rank(out, "extent_mkm2", False, "hemisphere")
    out["yoy_change_mkm2"] = yoy(out, "extent_mkm2", by="hemisphere")
    for h, g in out.groupby("hemisphere"):
        lo, hi = {"north": (2.5, 17.5), "south": (1.0, 21.0), "global": (14.0, 30.0)}[h]
        gate(g.date.is_unique, f"sea_ice {h}: dates unique")
        gate(g.extent_mkm2.between(lo, hi).all(), f"sea_ice {h}: extent within [{lo},{hi}]")
        gate(g.date.min() == pd.Timestamp("1978-10-26"), f"sea_ice {h}: starts 1978-10-26")
        fresh(lag_days(g.date.max()) <= 6, f"sea_ice {h}: last date {g.date.max().date()} within 6 days")
        gate(g.extent_anom_1991_2020_mkm2.notna().all(), f"sea_ice {h}: anomaly defined on every row")
        last = g.iloc[-1]
        gate(1 <= last.rank_lowest_for_day <= last.date.year - 1977,
             f"sea_ice {h}: latest rank {last.rank_lowest_for_day} within the years on record")
    return out


# ---------------------------------------------------------------- GML gases
def gml_csv(gas: str, file: str) -> pd.DataFrame:
    raw = get(GML.format(gas=gas, file=file)).decode()
    lines = [ln for ln in raw.splitlines() if not ln.startswith("#") and ln.strip()]
    has_header = lines[0].strip()[:1].isalpha()
    return pd.read_csv(io.StringIO("\n".join(lines)), header=0 if has_header else None)


def co2_daily() -> pd.DataFrame:
    df = gml_csv("co2", "co2_daily_mlo.csv")  # no header row: year, month, day, decimal, ppm
    gate(df.shape[1] == 5, f"co2_daily: 5 columns (got {df.shape[1]})")
    df.columns = ["year", "month", "day", "decimal_date", "co2_ppm"]
    df["date"] = pd.to_datetime(df[["year", "month", "day"]])
    df = df[["date", "decimal_date", "co2_ppm"]]
    gate(df.date.is_unique and df.date.is_monotonic_increasing, "co2_daily: dates unique and sorted")
    gate(df.co2_ppm.between(320, 480).all(), "co2_daily: ppm within [320,480]")
    fresh(lag_days(df.date.max()) <= 21, f"co2_daily: last date {df.date.max().date()} within 21 days")
    df = df.copy()
    df["yoy_change_ppm"] = yoy(df, "co2_ppm")
    # highest daily value of the whole record up to that day
    df["record_high"] = (df.co2_ppm > df.co2_ppm.cummax().shift(1)).astype("boolean")
    df.loc[df.index[0], "record_high"] = pd.NA
    ok = df.yoy_change_ppm.dropna()
    gate(ok.between(-4, 10).all(), f"co2_daily: year-over-year change within [-4,10] ppm "
                                  f"(min {ok.min():.2f}, max {ok.max():.2f})")
    return df


def gases_monthly() -> pd.DataFrame:
    spec = {  # series: (dir, file, plausible range)
        "co2_mlo": ("co2", "co2_mm_mlo.csv", (310, 480)),
        "co2_global": ("co2", "co2_mm_gl.csv", (330, 480)),
        "ch4_global": ("ch4", "ch4_mm_gl.csv", (1600, 2100)),
        "n2o_global": ("n2o", "n2o_mm_gl.csv", (300, 360)),
        "sf6_global": ("sf6", "sf6_mm_gl.csv", (3, 20)),
    }
    out = []
    for name, (d, f, (lo, hi)) in spec.items():
        df = gml_csv(d, f)
        df.columns = [c.strip() for c in df.columns]
        avg = df["average"].where(df["average"] > 0)  # GML writes -9.99 / -99.99 for missing
        trend = df["deseasonalized"] if "deseasonalized" in df else df["trend"]
        date = pd.to_datetime(dict(year=df.year, month=df.month, day=1))
        # co2_mm_mlo.csv: "Missing months have been interpolated, for NOAA data indicated by
        # negative stdev" (NOAA data start 1974-05; earlier months are Scripps and carry no flag)
        interp = (pd.Series(pd.NA, index=df.index, dtype="boolean") if name != "co2_mlo"
                  else pd.Series(np.where(date >= "1974-05-01", df["sdev"] < 0, pd.NA), dtype="boolean"))
        g = pd.DataFrame({
            "series": name,
            "date": date,
            "average": avg,
            "deseasonalized": trend.where(trend > 0),
            "interpolated": interp,
        })
        gate(g.date.is_unique, f"gases {name}: months unique")
        gate(g.average.dropna().between(lo, hi).all(), f"gases {name}: values within [{lo},{hi}]")
        fresh(lag_days(g.date.max()) <= 200, f"gases {name}: last month {g.date.max().date()} within 200 days")
        out.append(g)
    df = pd.concat(out).reset_index(drop=True)
    df["yoy_change"] = yoy(df, "average", by="series", months=True)
    return df


# ---------------------------------------------------------------- gridded daily means (SST, air)
# name: (lat_min, lat_max, lon_min, lon_max) in 0..360 longitudes. Niño boxes follow NOAA CPC, the
# Indian Ocean Dipole poles follow NOAA PSL (Saji et al. 1999); the first two keep their published names.
REGIONS = {
    "world_60s_60n": (-60, 60, 0, 360),
    "north_atlantic_0_60n_0_80w": (0, 60, 280, 360),
    "world_90s_90n": (-90, 90, 0, 360),
    "tropics_20s_20n": (-20, 20, 0, 360),
    "north_pacific_20n_60n_120e_110w": (20, 60, 120, 250),
    "nino12_0_10s_90w_80w": (-10, 0, 270, 280),
    "nino3_5n_5s_150w_90w": (-5, 5, 210, 270),
    "nino34_5n_5s_170w_120w": (-5, 5, 190, 240),
    "nino4_5n_5s_160e_150w": (-5, 5, 160, 210),
    "iod_west_10s_10n_50e_70e": (-10, 10, 50, 70),
    "iod_east_10s_0_90e_110e": (-10, 0, 90, 110),
}


def region_means(field2d, lat: np.ndarray, lon: np.ndarray, regions=None) -> dict[str, float]:
    """cos-latitude weighted mean over the unmasked cells of each box (cell centres inside)."""
    res = {}
    x_all = np.ma.masked_invalid(np.ma.asarray(field2d, dtype=np.float64))
    for name, (a, b, c, d) in (regions or REGIONS).items():
        li = (lat >= a) & (lat <= b)
        lj = (lon >= c) & (lon <= d)
        x = x_all[np.ix_(li, lj)]
        w = np.broadcast_to(np.cos(np.deg2rad(lat[li]))[:, None], x.shape)
        w = np.ma.array(w, mask=np.ma.getmaskarray(x))
        res[name] = float((x * w).sum() / w.sum())
    return res


def grid_year(y: int, tmp: Path, url: str, var: str, regions: dict, offset: float) -> pd.DataFrame:
    import netCDF4
    f = tmp / f"{var}.{y}.nc"
    t0 = time.time()
    get(url.format(y=y), f)
    rows = []
    with netCDF4.Dataset(f) as ds:
        lat, lon = np.asarray(ds["lat"][:]), np.asarray(ds["lon"][:])
        t = ds["time"]
        dates = netCDF4.num2date(t[:], t.units, only_use_cftime_datetimes=False)
        for i, d in enumerate(dates):
            m = region_means(ds[var][i, :, :], lat, lon, regions)
            rows.append({"date": pd.Timestamp(d.year, d.month, d.day), **{k: v + offset for k, v in m.items()}})
    f.unlink()
    print(f"{var} {y}: {len(rows)} days in {time.time() - t0:.0f}s", flush=True)
    return pd.DataFrame(rows)


def grid_years(name: str, url: str, var: str, regions: dict, offset: float, first_year: int,
               backfill_from: int | None, years_only: list[int] | None, tmp: Path) -> pd.DataFrame:
    """Recompute the current year (and the previous one until mid-February, to finalize its
    preliminary days), or the backfill years, and keep every other year from the committed history."""
    path = DATA / f"{name}.csv"
    old = pd.read_csv(path, parse_dates=["date"]) if path.exists() else pd.DataFrame(columns=["date"])
    if years_only:
        years = years_only
    elif backfill_from:
        years = list(range(max(backfill_from, first_year), TODAY.year + 1))
    else:
        years = [TODAY.year]
        if TODAY.timetuple().tm_yday <= 45:
            years.insert(0, TODAY.year - 1)
    parts = []
    for y in years:
        try:
            parts.append(grid_year(y, tmp, url, var, regions, offset))
        except urllib.error.HTTPError as e:
            # PSL creates the new year's file only once its first day exists (2027 was 404 on
            # 2026-10-03); for the first days of January the current year may not exist yet.
            if e.code == 404 and y == TODAY.year and TODAY.timetuple().tm_yday <= 10:
                print(f"{var} {y}: no file yet (404); keeping earlier years", flush=True)
                continue
            raise
    new = pd.concat(parts)
    keep = old[~pd.to_datetime(old.date).dt.year.isin(years)]
    keep = keep[["date"] + [c for c in keep.columns if c in regions]]
    df = pd.concat([keep, new])
    df["date"] = pd.to_datetime(df.date)
    df = df.sort_values("date").reset_index(drop=True)
    for k in regions:
        df[k] = df[k].round(4)
    return df


def daily_complete(df: pd.DataFrame, tag: str, start: str, cols) -> None:
    gate(df.date.is_unique, f"{tag}: dates unique")
    gate(df.date.min() == pd.Timestamp(start), f"{tag}: starts {start} (got {df.date.min().date()})")
    full = pd.date_range(df.date.min(), df.date.max(), freq="D")
    gate(len(full) == len(df), f"{tag}: no missing days ({len(full) - len(df)} missing)")
    gate(df[list(cols)].notna().all().all(), f"{tag}: every region defined on every day "
                                             f"({int(df[list(cols)].isna().sum().sum())} empty)")


def add_anoms_and_ranks(df: pd.DataFrame, regions, ranked) -> pd.DataFrame:
    for k in regions:
        df[k + "_anom_1991_2020"] = anom_1991_2020(df, k)
    for k in ranked:
        df[k + "_rank_warmest_for_day"], df[k + "_record_warm_for_day"] = day_rank(df, k, True)
    return df


def sst_crosscheck(df: pd.DataFrame, tmp: Path) -> None:
    """The PSL yearly file and NCEI's per-day file are separate copies; a final (non-preliminary)
    day must give the same means, which checks the download and the averaging end to end."""
    import netCDF4
    d = (df.date.max() - pd.Timedelta(days=20)).date()
    f = tmp / "ncei.nc"
    get(NCEI_SST.format(ym=d.strftime("%Y%m"), ymd=d.strftime("%Y%m%d")), f)
    with netCDF4.Dataset(f) as ds:
        m = region_means(ds["sst"][0, 0, :, :], np.asarray(ds["lat"][:]), np.asarray(ds["lon"][:]))
    row = df.set_index("date").loc[pd.Timestamp(d)]
    for k, v in m.items():
        gate(abs(row[k] - v) < 0.005, f"sst cross-check {d} {k}: PSL {row[k]:.4f} vs NCEI {v:.4f}")


NINO = {"Nino12": "nino12_0_10s_90w_80w", "Nino3": "nino3_5n_5s_150w_90w",
        "Nino34": "nino34_5n_5s_170w_120w", "Nino4": "nino4_5n_5s_160e_150w"}
# CPC's weekly product is a different OISST rendering, so single weeks differ by up to ~0.4 C.
# Measured over every 52-week window 1982-2026 (2026-10-03): |mean difference| at most 0.046 C
# (Niño 1+2) and 0.017-0.026 C elsewhere; sd at most 0.196 C (Niño 1+2) and 0.070-0.083 C elsewhere.
# The limits sit 1.5x above those; a wrong box shifts the mean by far more. A 1-day shift is not
# visible here (sd 0.048 vs 0.024 at best) -- the NCEI check pins the dates instead.
WEEKLY_BIAS_TOL = 0.08
WEEKLY_SD_TOL = {"Nino12": 0.30, "Nino3": 0.13, "Nino34": 0.12, "Nino4": 0.11}


def cpc_weekly(raw: str) -> pd.DataFrame:
    """Parse NOAA CPC wksst9120.for: a date, then SST and anomaly per Niño box written with one
    decimal and no separator before a negative anomaly ("20.6-0.1")."""
    rows = []
    for ln in raw.splitlines():
        p = ln.split()
        if not p or not p[0][:2].isdigit():
            continue
        vals = [float(v) for v in re.findall(r"-?\d+\.\d", ln[len(ln) - len(ln.lstrip()) + len(p[0]):])]
        if len(vals) != 8:
            raise ValueError(f"CPC weekly line not understood: {ln!r}")
        rows.append({"date": pd.to_datetime(p[0], format="%d%b%Y"),
                     **{k: vals[2 * i] for i, k in enumerate(NINO)}})
    return pd.DataFrame(rows).set_index("date")


def cpc_weekly_check(df: pd.DataFrame) -> None:
    """NOAA CPC publishes weekly Niño-box SST from OISST (weeks centred on Wednesday). Our 7-day
    means over the same boxes are an independent computation of the same quantity. Weeks within
    21 days of our last date are left out (OISST is preliminary for about two weeks)."""
    w = cpc_weekly(get(CPC_SST_WEEKLY).decode())
    s = df.set_index("date")
    end = s.index.max() - pd.Timedelta(days=21)
    recent = w[(w.index <= end) & (w.index > end - pd.Timedelta(days=7 * 52))]
    for k, col in NINO.items():
        d = (s[col].rolling(7, center=True).mean().reindex(recent.index) - recent[k]).dropna()
        gate(len(d) >= 45 and abs(d.mean()) <= WEEKLY_BIAS_TOL and d.std() <= WEEKLY_SD_TOL[k],
             f"sst {k} 7-day mean vs CPC weekly, {len(d)} weeks: mean diff {d.mean():+.3f} C "
             f"(|.| <= {WEEKLY_BIAS_TOL}), sd {d.std():.3f} C (<= {WEEKLY_SD_TOL[k]})")


def sst(backfill_from: int | None, years_only: list[int] | None = None) -> pd.DataFrame:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df = grid_years("sst_daily", PSL_SST, "sst", REGIONS, 0.0, 1981, backfill_from, years_only, tmp)
        sst_crosscheck(df, tmp)
    daily_complete(df, "sst", "1981-09-01", REGIONS)
    df = add_anoms_and_ranks(df, REGIONS, ["world_60s_60n", "world_90s_90n"])
    # Indian Ocean Dipole mode index (west minus east anomaly, Saji et al. 1999) and the Niño 3.4
    # anomaly relative to the tropical mean (the quantity behind NOAA CPC's RONI, unscaled)
    df["dmi"] = (df["iod_west_10s_10n_50e_70e_anom_1991_2020"]
                 - df["iod_east_10s_0_90e_110e_anom_1991_2020"]).round(4)
    df["nino34_minus_tropics_anom"] = (df["nino34_5n_5s_170w_120w_anom_1991_2020"]
                                       - df["tropics_20s_20n_anom_1991_2020"]).round(4)
    gate(df["world_60s_60n"].between(19.0, 22.0).all(), "sst: world 60S-60N within [19,22] C")
    gate(df["nino34_5n_5s_170w_120w"].between(23.0, 31.0).all(), "sst: Nino 3.4 within [23,31] C")
    cpc_weekly_check(df)
    sst_vs_era5(df)
    fresh(lag_days(df.date.max()) <= 7, f"sst: last date {df.date.max().date()} within 7 days")
    return df


def pulse(v: str) -> pd.DataFrame:
    raw = get(PULSE.format(v=v)).decode()
    df = pd.read_csv(io.StringIO(raw), comment="#")
    want = ["date", v.split("_")[0], "clim_91-20", "ano_91-20", "status"]
    if list(df.columns) != want:  # stop here: the renaming below would be wrong
        raise ValueError(f"era5 {v}: columns {list(df.columns)} != {want}")
    df.columns = ["date", "value", "clim", "anom", "status"]
    df["date"] = pd.to_datetime(df.date)
    return df


def air() -> pd.DataFrame:
    """ERA5 daily global mean 2 m air temperature from Copernicus Climate Pulse (1940-present),
    with C3S's own 1991-2020 daily climatology and anomaly."""
    e = pulse("2t_global")
    df = pd.DataFrame({
        "date": e.date,
        "global_2t": e.value,
        "global_2t_clim_1991_2020": e.clim,
        "global_2t_anom_1991_2020": e.anom,
        "status": e.status.str.lower(),
    })
    daily_complete(df, "air", "1940-01-01", ["global_2t", "global_2t_clim_1991_2020", "global_2t_anom_1991_2020"])
    gate(df.status.isin(["final", "preliminary"]).all(), "air: status is final or preliminary")
    gate((df.status == "preliminary").sum() <= 10, f"air: at most 10 preliminary days "
                                                   f"({(df.status == 'preliminary').sum()})")
    # the published anomaly must be the published value minus the published climatology
    d = (df.global_2t - df.global_2t_clim_1991_2020 - df.global_2t_anom_1991_2020).abs().max()
    gate(d <= 0.0015, f"air: anomaly = value - climatology to rounding (worst {d:.4f})")
    df["global_rank_warmest_for_day"], df["global_record_warm_for_day"] = day_rank(df, "global_2t", True)
    gate(df.global_2t.between(10.0, 18.5).all(),
         f"air: global mean within [10,18.5] C ({df.global_2t.min():.2f}..{df.global_2t.max():.2f})")
    a = df.global_2t_anom_1991_2020
    gate(a.between(-2.5, 2.5).all(), f"air: global anomaly within [-2.5,2.5] C ({a.min():.2f}..{a.max():.2f})")
    fresh(lag_days(df.date.max()) <= 7, f"air: last date {df.date.max().date()} within 7 days")
    return df


# Measured 2024-10..2026-09: 30-day mean anomalies, OISST minus ERA5 +0.091 C, sd 0.019, correlation
# 0.986; daily absolute values, OISST minus ERA5 +0.092 C, sd 0.019 (last 40 years: +0.007, sd 0.070).
# The offset is a real difference between the two analyses. The absolute check is what catches a
# wrong box: 90S-90N would sit at -2.08 C, the tropics at +6.8 C.
ERA5_CORR_MIN, ERA5_BIAS_TOL, ERA5_SD_TOL = 0.9, 0.2, 0.08
ERA5_ABS_BIAS_TOL, ERA5_ABS_SD_TOL = 0.3, 0.06


def sst_vs_era5(df: pd.DataFrame) -> None:
    """ERA5's 60S-60N SST (its own boundary analysis) is a second estimate of the same quantity:
    over the last two years the 30-day mean anomalies must move together."""
    e = pulse("sst_60S-60N_ocean").set_index("date")
    lvl = pd.concat([df.set_index("date")["world_60s_60n"], e.value], axis=1, keys=["oisst", "era5"]).dropna()
    lvl = lvl[lvl.index >= lvl.index.max() - pd.Timedelta(days=730)]
    d = lvl.oisst - lvl.era5
    gate(len(lvl) >= 600 and abs(d.mean()) <= ERA5_ABS_BIAS_TOL and d.std() <= ERA5_ABS_SD_TOL,
         f"sst 60S-60N daily level vs ERA5, {len(lvl)} days: mean diff {d.mean():+.3f} C "
         f"(|.| <= {ERA5_ABS_BIAS_TOL}), sd {d.std():.3f} C (<= {ERA5_ABS_SD_TOL})")
    ours = df.set_index("date")["world_60s_60n_anom_1991_2020"]
    both = pd.concat([ours, e.anom], axis=1, keys=["oisst", "era5"]).dropna()
    both = both[both.index >= both.index.max() - pd.Timedelta(days=730)].rolling(30).mean().dropna()
    d = both.oisst - both.era5
    r = both.oisst.corr(both.era5)
    gate(len(both) >= 600 and r >= ERA5_CORR_MIN and abs(d.mean()) <= ERA5_BIAS_TOL and d.std() <= ERA5_SD_TOL,
         f"sst 60S-60N 30-day anomaly vs ERA5, {len(both)} days: corr {r:.3f} (>= {ERA5_CORR_MIN}), "
         f"mean diff {d.mean():+.3f} C (|.| <= {ERA5_BIAS_TOL}), sd {d.std():.3f} C (<= {ERA5_SD_TOL})")


# ---------------------------------------------------------------- CPC indices
SEASONS = ["DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ", "JJA", "JAS", "ASO", "SON", "OND", "NDJ"]


def enso_monthly() -> pd.DataFrame:
    """ONI (ERSSTv5 Niño 3.4, 3-month running mean) and RONI (relative ONI, NOAA CPC's official
    ENSO index per NWS statement 26-05), dated by the centre month of each 3-month season."""
    def read(f, cols):
        t = pd.read_csv(io.StringIO(get(CPC.format(f=f)).decode()), sep=r"\s+")
        gate(t.shape[1] == len(cols), f"enso {f}: {len(cols)} columns (got {t.shape[1]})")
        t.columns = cols
        return t
    oni = read("oni.ascii.txt", ["season", "year", "oni_total_c", "oni_anom"])
    roni = read("RONI.ascii.txt", ["season", "year", "roni_anom"])
    df = oni.merge(roni, on=["season", "year"], how="outer")
    gate(df.season.isin(SEASONS).all(), "enso: season labels known")
    df["date"] = pd.to_datetime(dict(year=df.year, month=df.season.map(SEASONS.index) + 1, day=1))
    df = df.sort_values("date")[["date", "season", "oni_total_c", "oni_anom", "roni_anom"]].reset_index(drop=True)
    gate(df.date.is_unique, "enso: months unique")
    gate(df.date.min() == pd.Timestamp("1950-01-01"), f"enso: starts 1950-01 (got {df.date.min().date()})")
    gate(len(pd.date_range(df.date.min(), df.date.max(), freq="MS")) == len(df), "enso: no missing months")
    gate(df.oni_anom.notna().sum() >= len(df) - 2, "enso: ONI present for all but the last 2 seasons")
    gate(df.oni_anom.dropna().between(-3, 3.5).all() and df.roni_anom.dropna().between(-3, 3.5).all(),
         "enso: anomalies within [-3,3.5] C")
    fresh(lag_days(df.date.max()) <= 120, f"enso: last season centre {df.date.max().date()} within 120 days")
    return df


def atmosphere_daily() -> pd.DataFrame:
    """Daily standardized teleconnection indices from NOAA CPC. The files keep the name "cdas"; NCEP
    replaced CDAS with CORe on 2026-03-18, so values from then on may come from the new system."""
    spec = {"ao": "ao.cdas.z1000.19500101", "nao": "nao.cdas.z500.19500101",
            "pna": "pna.cdas.z500.19500101", "aao": "aao.cdas.z700.19790101"}
    df = None
    for k, f in spec.items():
        t = pd.read_csv(io.StringIO(get(CPC_TELE.format(f=f)).decode()))
        gate(t.shape[1] == 4, f"atmosphere {k}: 4 columns")
        t = pd.DataFrame({"date": pd.to_datetime(dict(year=t.iloc[:, 0], month=t.iloc[:, 1], day=t.iloc[:, 2])),
                          k: t.iloc[:, 3].round(4)})
        gate(t.date.is_unique, f"atmosphere {k}: dates unique")
        gate(t[k].dropna().between(-8, 8).all(), f"atmosphere {k}: within [-8,8]")
        fresh(lag_days(t.date.max()) <= 10, f"atmosphere {k}: last date {t.date.max().date()} within 10 days")
        df = t if df is None else df.merge(t, on="date", how="outer")
    df = df.sort_values("date").reset_index(drop=True)
    gate(df.date.min() == pd.Timestamp("1950-01-01"), "atmosphere: starts 1950-01-01")
    gate(len(pd.date_range(df.date.min(), df.date.max(), freq="D")) == len(df), "atmosphere: no missing days")
    return df


# ---------------------------------------------------------------- monthly panel
def monthly_panel(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per month with every indicator side by side (monthly means of the daily tables).
    A month gets a daily-derived value only when enough days are present (90%, or 40% for the
    every-other-day sea ice era), so the current month appears once it is nearly complete."""
    def mmean(df, col, val, min_frac=0.9, hemi=None):
        d = df if hemi is None else df[df.hemisphere == hemi]
        g = d.groupby(d.date.dt.to_period("M"))[col]
        m, n = g.mean(), g.count()
        need = np.ceil(min_frac * m.index.days_in_month)
        return m.where(n >= need).round(4).rename(val)

    ice, gas, sst_, air_, enso, atm = (t["sea_ice_extent_daily"], t["greenhouse_gases_monthly"], t["sst_daily"],
                                       t["air_temperature_daily"], t["enso_monthly"], t["atmosphere_indices_daily"])
    cols = [
        mmean(air_, "global_2t_anom_1991_2020", "air_temp_global_anom"),
        mmean(sst_, "world_60s_60n_anom_1991_2020", "sst_world_60s_60n_anom"),
        mmean(sst_, "nino34_5n_5s_170w_120w_anom_1991_2020", "sst_nino34_anom"),
        mmean(sst_, "dmi", "sst_dmi"),
        mmean(ice, "extent_mkm2", "sea_ice_north_mkm2", 0.4, "north"),
        mmean(ice, "extent_mkm2", "sea_ice_south_mkm2", 0.4, "south"),
        mmean(ice, "extent_anom_1991_2020_mkm2", "sea_ice_north_anom_mkm2", 0.4, "north"),
        mmean(ice, "extent_anom_1991_2020_mkm2", "sea_ice_south_anom_mkm2", 0.4, "south"),
    ] + [mmean(atm, k, k) for k in ("ao", "nao", "pna", "aao")]
    for s, name in [("co2_mlo", "co2_mlo_ppm"), ("co2_global", "co2_global_ppm"),
                    ("ch4_global", "ch4_global_ppb"), ("n2o_global", "n2o_global_ppb"),
                    ("sf6_global", "sf6_global_ppt")]:
        g = gas[gas.series == s]
        cols.append(g.set_index(g.date.dt.to_period("M"))["average"].rename(name))
    e = enso.set_index(enso.date.dt.to_period("M"))
    cols += [e["oni_anom"], e["roni_anom"]]
    df = pd.concat(cols, axis=1).sort_index()
    df = df[df.index >= pd.Period("1950-01", "M")].dropna(how="all")
    df.insert(0, "date", df.index.to_timestamp())
    df = df.reset_index(drop=True)
    gate(df.date.is_unique, "panel: months unique")
    gate(len(pd.date_range(df.date.min(), df.date.max(), freq="MS")) == len(df), "panel: no missing months")
    return df


# ---------------------------------------------------------------- history guard
def history_guard(name: str, new: pd.DataFrame, keys: list[str], recent_days: int | None) -> None:
    """Rows already published must not disappear, and rows older than the revision window
    must not change. A deliberate upstream reprocessing fails here and needs a human look.
    New columns may be added; published columns may not be dropped or renamed.
    recent_days=None marks an index its publisher revises in full (CPC re-normalizes ONI and the
    teleconnection indices): rows must still not disappear, and the largest change is reported."""
    path = DATA / f"{name}.csv"
    if not path.exists():
        print(f"INFO {name}: no committed history yet")
        return
    old = pd.read_csv(path, parse_dates=["date"])
    gate(len(new) >= len(old), f"{name}: rows {len(old)} -> {len(new)} (no shrink)")
    cutoff = pd.Timestamp(TODAY) - pd.Timedelta(days=recent_days if recent_days is not None else 45)
    o = old[old.date < cutoff].set_index(keys).sort_index()
    n = new[new.date < cutoff].set_index(keys).sort_index()
    window = f"{recent_days} days" if recent_days is not None else "45 days"
    gate(o.index.isin(n.index).all(), f"{name}: every published row older than {window} still present")
    missing = [c for c in o.columns if c not in n.columns]
    gate(not missing, f"{name}: every published column still present (missing: {missing})")
    added = [c for c in n.columns if c not in o.columns]
    if added:
        print(f"INFO {name}: new columns {added}")
    n = n.reindex(o.index)[[c for c in o.columns if c in n.columns]]
    if recent_days is None:
        num = [c for c in o.select_dtypes("number").columns if c in n.columns]
        d = (o[num] - n[num].astype(float)).abs().max().max() if len(o) and num else 0.0
        print(f"INFO {name}: revisable index; largest change to an existing value {d}")
        return
    # Every column is compared, including the SST anomalies: their 1991-2020 normal uses only rows
    # older than the window, so a change there is a bug in this script, not an upstream revision.
    num = [c for c in o.select_dtypes("number").columns if c in n.columns]
    other = [c for c in o.columns if c not in num and c in n.columns]
    d = (o[num] - n[num].astype(float)).abs().max().max() if len(o) and num else 0.0
    same_nan = bool((o[num].isna() == n[num].isna()).all().all()) if num else True
    as_text = lambda x: x.astype("string").fillna("<NA>")  # noqa: E731  bool/str read back as text
    same_other = bool((as_text(o[other]) == as_text(n[other])).all().all()) if other else True
    gate(bool(np.nan_to_num(d) < 1e-6 and same_nan and same_other),
         f"{name}: values older than {recent_days} days unchanged (max diff {d}, other columns same: {same_other})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sst-backfill-from", type=int, default=None)
    ap.add_argument("--sst-years", type=int, nargs="*", default=None,
                    help="recompute only these years (testing)")
    args = ap.parse_args()

    tables = {
        "sea_ice_extent_daily": (sea_ice(), ["date", "hemisphere"], 30),
        "co2_daily_mauna_loa": (co2_daily(), ["date"], 60),
        "greenhouse_gases_monthly": (gases_monthly(), ["series", "date"], 400),
        "sst_daily": (sst(args.sst_backfill_from, args.sst_years), ["date"], 60),
        "air_temperature_daily": (air(), ["date"], 120),
        "enso_monthly": (enso_monthly(), ["date"], None),
        "atmosphere_indices_daily": (atmosphere_daily(), ["date"], None),
    }
    tables["climate_monthly_panel"] = (monthly_panel({k: v[0] for k, v in tables.items()}), ["date"], None)
    for name, (df, keys, recent) in tables.items():
        history_guard(name, df, keys, recent)
    if FAIL:
        print(f"\n{len(FAIL)} gate(s) failed; nothing written.")
        for m in FAIL:
            print("  " + m)
        return 1

    DATA.mkdir(exist_ok=True)
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir()
    for name, (df, _, _) in tables.items():
        out = df.copy()
        out["date"] = out.date.dt.strftime("%Y-%m-%d")
        out.to_csv(DATA / f"{name}.csv", index=False)
        out.to_csv(BUILD / f"{name}.csv", index=False)
    shutil.copy(ROOT / "kaggle" / "dataset-metadata.json", BUILD / "dataset-metadata.json")
    summary = {name: {"rows": len(df), "last_date": str(df.date.max().date())}
               for name, (df, _, _) in tables.items()}
    summary["stale_sources"] = STALE  # freshness warnings; empty when every source is current
    (DATA / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
