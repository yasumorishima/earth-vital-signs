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


def _retryable(e: urllib.error.HTTPError) -> bool:
    """5xx, 408 and 429 can clear on their own; other 4xx will not change on retry.
    NOAA GML answered 429 Too Many Requests to a GitHub runner on 2026-10-12."""
    return e.code >= 500 or e.code in (408, 429)


def _retry_after(e: urllib.error.HTTPError, attempt: int) -> float:
    """Seconds to wait before retry number `attempt`: Retry-After (in seconds) when the server sends
    it, otherwise a minute per attempt for 429, capped at 5 minutes. NOAA PSL sent 429 without
    Retry-After on 2026-10-12 and served again a few minutes later, so 10-30 s waits were too short."""
    v = (e.headers.get("Retry-After") or "").strip() if e.headers else ""
    if v.isdecimal():
        return min(float(v), 300.0)
    return min(60.0 * attempt, 300.0) if e.code == 429 else 0.0


def _refused(e: Exception) -> bool:
    """The server kept refusing or stalling (retries used up), as opposed to sending wrong bytes."""
    if isinstance(e, urllib.error.HTTPError):
        return _retryable(e)
    return isinstance(e, OSError) and "no progress at" in str(e)


# NOAA GML was unreachable from a runner (Errno 101) for over a minute on 2026-10-27, half a minute
# after serving the CO2 file, so network errors wait 30 s, 1, 2, 4 and 5 min (about 12 min in all).
NET_TRIES = 6


def _net_wait(attempt: int) -> float:
    return min(30.0 * 2 ** (attempt - 1), 300.0)


def transient(e: BaseException) -> bool:
    """A failure that the next scheduled run can be expected to clear: the network or the server
    kept refusing after the retries, as opposed to wrong bytes or a failed gate."""
    if isinstance(e, urllib.error.HTTPError):
        return _retryable(e)
    return isinstance(e, (urllib.error.URLError, ConnectionError, TimeoutError)) or _refused(e)


def get(url: str, dest: Path | None = None, tries: int = 4) -> bytes | None:
    """Small files are returned as bytes. Large files go to dest and are resumed with HTTP Range:
    the PSL server closes each connection after roughly 11 MB, so a 356 MB year arrives in pieces.
    The file must end at exactly the advertised length or this raises."""
    if dest is None:
        refused = net = 0
        while True:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=600) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                refused += 1
                if not _retryable(e) or refused == tries:
                    raise
                print(f"retry {refused} {url}: {e}", flush=True)
                time.sleep(max(10 * refused, _retry_after(e, refused)))
            except Exception as e:  # network errors are retried with longer waits, then raised
                net += 1
                if net == NET_TRIES:
                    raise
                print(f"retry {net} {url}: {e}", flush=True)
                time.sleep(_net_wait(net))
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
        wait = 0.0
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
            if not _retryable(e):
                raise
            print(f"{url}: {e}; retrying", flush=True)
            wait = _retry_after(e, stalls + 1)
        except OSError as e:
            if "ignored Range" in str(e) or "more than" in str(e) or "kept changing" in str(e):
                raise
            # a dropped connection is expected; progress is checked below
        if dest.stat().st_size == have:
            stalls += 1
            if stalls >= tries:
                raise OSError(f"{url}: no progress at {have} of {total} bytes")
            time.sleep(max(10 * stalls, wait))
        else:
            stalls = 0


def lag_days(last) -> int:
    return (TODAY - pd.Timestamp(last).date()).days


# ---------------------------------------------------------------- derived columns
# Every derived column depends only on the row's own date and earlier dates (or on the fixed
# 1991-2020 normal), so later data never change an earlier day's rank.
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


def ytd_mean(df: pd.DataFrame, col: str) -> pd.Series:
    """Mean of `col` from 1 January to the row's own date (the frame is one row per day, sorted).
    Empty in a year whose record does not start on 1 January (e.g. SST in 1981)."""
    y = df.date.dt.year
    m = df.groupby(y)[col].cumsum() / df[col].notna().groupby(y).cumsum()
    starts = df.groupby(y).date.transform("min")
    return m.where((starts.dt.month == 1) & (starts.dt.day == 1)).round(4)


def change_over(df: pd.DataFrame, col: str, days: int, by: str | None = None) -> pd.Series:
    """Value minus the value `days` calendar days earlier; empty if that earlier day is absent."""
    key = ["date"] if by is None else [by, "date"]
    prev = df[key + [col]].copy()
    prev["date"] = prev.date + pd.Timedelta(days=days)
    m = df[key].merge(prev.rename(columns={col: "_prev"}), on=key, how="left")
    return pd.Series(df[col].to_numpy() - m["_prev"].to_numpy(), index=df.index).round(4)


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
    out["change_7d_mkm2"] = change_over(out, "extent_mkm2", 7, by="hemisphere")
    for h, g in out.groupby("hemisphere"):
        lo, hi = {"north": (2.5, 17.5), "south": (1.0, 21.0), "global": (14.0, 30.0)}[h]
        gate(g.date.is_unique, f"sea_ice {h}: dates unique")
        gate(g.extent_mkm2.between(lo, hi).all(), f"sea_ice {h}: extent within [{lo},{hi}]")
        gate(g.date.min() == pd.Timestamp("1978-10-26"), f"sea_ice {h}: starts 1978-10-26")
        fresh(lag_days(g.date.max()) <= 6, f"sea_ice {h}: last date {g.date.max().date()} within 6 days")
        gate(g.extent_anom_1991_2020_mkm2.notna().all(), f"sea_ice {h}: anomaly defined on every row")
        # Largest 7-day changes on record (measured 2026-10-18): north 1.78 (2020-11), south 2.45 (2004-12),
        # global 2.17 (2004-12). The limits sit 1.5 times above.
        c7 = g.change_7d_mkm2.dropna()
        gate(len(c7) > 0.9 * len(g[g.date >= "1987-09-01"])
             and c7.abs().max() <= {"north": 2.7, "south": 3.7, "global": 3.3}[h],
             f"sea_ice {h}: 7-day change defined on the daily era and within bounds (max |.| {c7.abs().max():.2f})")
        if h == "north":  # direction: the Arctic freezes up in October and melts in July
            mo = g.date.dt.month
            up, down = g.change_7d_mkm2[mo == 10].median(), g.change_7d_mkm2[mo == 7].median()
            gate(up > 0 > down, f"sea_ice north: 7-day change median October {up:+.2f} > 0 > July {down:+.2f}")
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


KEPT_LIMIT_DAYS = 14


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
        except OSError as e:  # urllib.error.HTTPError is an OSError
            # PSL creates the new year's file only once its first day exists (2027 was 404 on
            # 2026-10-03); for the first days of January the current year may not exist yet.
            if (isinstance(e, urllib.error.HTTPError) and e.code == 404 and y == TODAY.year
                    and TODAY.timetuple().tm_yday <= 10):
                print(f"{var} {y}: no file yet (404); keeping earlier years", flush=True)
                continue
            # PSL answered 429 to every request for minutes on 2026-10-12 (from GitHub and from a
            # home line alike, with no notice). In the daily update a refusing server leaves this
            # table exactly as committed, like any stopped source, so the other tables still update;
            # a backfill, or any error that could mean wrong bytes, still fails.
            # Keeping it is a stopgap, so it fails once the kept table is KEPT_LIMIT_DAYS old:
            # a permanent refusal must not leave the table frozen behind a warning nobody is sent.
            if not (backfill_from or years_only) and _refused(e) and len(old):
                why = f"HTTP {e.code}" if isinstance(e, urllib.error.HTTPError) else "no progress"
                last = pd.Timestamp(old.date.max()).date()
                fresh(False, f"{name}: {url.format(y=y)} kept refusing ({why}); "
                             f"keeping the committed table (last date {last})")
                gate(lag_days(last) <= KEPT_LIMIT_DAYS,
                     f"{name}: kept table last date {last} within {KEPT_LIMIT_DAYS} days")
                keep = old[["date"] + [c for c in old.columns if c in regions]].copy()
                keep["date"] = pd.to_datetime(keep.date)
                return keep.sort_values("date").reset_index(drop=True)
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
    # sorted as well: the trailing and year-to-date means are computed by row order
    gate(df.date.is_unique and df.date.is_monotonic_increasing, f"{tag}: dates unique and sorted")
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
    # Trenberth & Shea (2006, GRL 33, L12704) revised AMO: North Atlantic (0-60N, 0-80W) anomaly with the
    # 60S-60N mean removed. PSL's AMO file stopped in 2023-01.
    df["amo_ts06"] = (df["north_atlantic_0_60n_0_80w_anom_1991_2020"]
                      - df["world_60s_60n_anom_1991_2020"]).round(4)
    df["world_60s_60n_ytd_anom_1991_2020"] = ytd_mean(df, "world_60s_60n_anom_1991_2020")
    (df["world_60s_60n_ytd_rank_warmest_for_day"],
     df["world_60s_60n_ytd_record_warm_for_day"]) = day_rank(df, "world_60s_60n_ytd_anom_1991_2020", True)
    last = df[_md(df.date) == "12-31"]
    yearly = df.groupby(df.date.dt.year)["world_60s_60n_anom_1991_2020"].mean()
    dec31 = last.set_index(last.date.dt.year)["world_60s_60n_ytd_anom_1991_2020"].dropna()
    gate(len(dec31) >= 40 and bool(np.allclose(dec31, yearly.reindex(dec31.index), atol=1e-4)),
         f"sst: year-to-date mean on 31 Dec = annual mean ({len(dec31)} years)")
    # the record North Atlantic warmth of the second half of 2023 must come out positive
    h2 = df[(df.date >= "2023-06-01") & (df.date <= "2023-12-31")].amo_ts06.mean()
    gate(h2 > 0, f"sst: amo_ts06 mean for 2023-06..12 is positive ({h2:+.3f} C)")
    gate(df["world_60s_60n"].between(19.0, 22.0).all(), "sst: world 60S-60N within [19,22] C")
    gate(df["nino34_5n_5s_170w_120w"].between(23.0, 32.0).all(), "sst: Nino 3.4 within [23,32] C")  # record 29.97 on 2026-10-01
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


# C3S relates ERA5's 1991-2020 anomalies to 1850-1900 with a day-of-year offset (0.88 C on the annual mean):
# 0.88 + 0.05 sin(2 pi (n - 0.5) / 365) + 0.07 cos(2 pi (n - 0.5) / 365) for day n of a 365-day year, and the mean of
# 28 February and 1 March on 29 February (climate.copernicus.eu/climate-bulletin-about-data-and-analysis).
# A flat 0.88 is, in C3S's words, adequate only for twelve-month averages.
def c3s_preindustrial_offset(dates: pd.Series) -> pd.Series:
    def f(n):
        return 0.88 + 0.05 * np.sin(2 * np.pi * (n - 0.5) / 365) + 0.07 * np.cos(2 * np.pi * (n - 0.5) / 365)
    md = _md(dates)
    n = pd.to_datetime("2001-" + md.replace("02-29", "02-28")).dt.dayofyear.to_numpy(dtype=float)
    o = f(n)
    o[(md == "02-29").to_numpy()] = (f(59.0) + f(60.0)) / 2
    return pd.Series(o, index=dates.index)


# Annual global means above 1850-1900 published by C3S (Global Climate Highlights 2023, 2024 and 2025, ERA5).
# The yearly mean of our daily values must reproduce them: checks the offset and the anomaly column end to end.
C3S_ANNUAL_PREINDUSTRIAL = {2023: 1.48, 2024: 1.60, 2025: 1.47}


def air() -> pd.DataFrame:
    """ERA5 daily global mean 2 m air temperature from Copernicus Climate Pulse (1940-present),
    with C3S's own 1991-2020 daily climatology and anomaly, and the anomaly above 1850-1900."""
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
    off = c3s_preindustrial_offset(df.date)
    # C3S: daily offsets range 0.79-0.97, largest change in (northern) late winter, smallest in July. The annual
    # means below cannot see the phase or a flat 0.88, so the shape is checked here.
    one = off[df.date.dt.year == 2001]
    hi, lo = df.date[one.idxmax()], df.date[one.idxmin()]
    gate(abs(one.max() - 0.966) < 0.002 and abs(one.min() - 0.794) < 0.002 and hi.month in (1, 2, 3)
         and lo.month in (7, 8, 9), f"air: C3S daily offset peaks {one.max():.3f} on {hi:%m-%d} and dips {one.min():.3f} "
                                    f"on {lo:%m-%d} (expected 0.966 in Jan-Mar, 0.794 in Jul-Sep)")
    pi = "global_2t_anom_preindustrial"
    df[pi] = (a + off).round(4)
    df["global_above_1_5c_preindustrial"] = (df[pi] > 1.5).astype("boolean")
    df["global_above_2_0c_preindustrial"] = (df[pi] > 2.0).astype("boolean")
    df[pi + "_365d"] = df[pi].rolling(365, min_periods=365).mean().round(4)
    df["global_ytd_anom_preindustrial"] = ytd_mean(df, pi)
    (df["global_ytd_rank_warmest_for_day"],
     df["global_ytd_record_warm_for_day"]) = day_rank(df, "global_ytd_anom_preindustrial", True)
    yearly = df.groupby(df.date.dt.year)[pi].mean()
    first2 = df.date[df.global_above_2_0c_preindustrial.fillna(False)].min()
    gate(first2 == pd.Timestamp("2023-11-17"), f"air: first day above 2.0 C is 2023-11-17 as C3S reported ({first2})")
    for y, v in C3S_ANNUAL_PREINDUSTRIAL.items():
        gate(abs(yearly.get(y, np.nan) - v) <= 0.006,
             f"air: {y} mean above 1850-1900 {yearly.get(y, np.nan):.3f} vs C3S published {v:.2f} (within 0.006)")
    # the year-to-date mean on 31 December is the annual mean
    last = df[_md(df.date) == "12-31"]
    dec31 = last.set_index(last.date.dt.year)["global_ytd_anom_preindustrial"]
    gate(bool(np.allclose(dec31, yearly.reindex(dec31.index), atol=1e-4)), "air: year-to-date mean on 31 Dec = annual mean")
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


CPC_ONI_PAGE = "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso/oni/v6/"
CPC_RONI_PAGE = "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso/roni/"
EPISODE_EXEMPT_MAX = 6  # measured 2026-10-18 over 920 seasons: ONI 3, RONI 1 (MJJ 2024 at -0.45); twice the larger


def cpc_round(x: np.ndarray) -> np.ndarray:
    """One decimal, half away from zero, as in CPC's tables (0.45 -> 0.5, -0.45 -> -0.5)."""
    return np.sign(x) * np.floor(np.abs(x) * 10 + 0.5 + 1e-9) / 10


def enso_episodes(x: pd.Series) -> pd.Series:
    """NOAA CPC's historical ENSO episodes: the one-decimal index at or above +0.5 (El Niño) or at or below
    -0.5 (La Niña) for at least 5 consecutive overlapping seasons; other seasons are neutral. A qualifying run
    that is still open at the end of the record (fewer than 5 seasons so far) is left empty: undetermined."""
    v = x.to_numpy(dtype=float)
    s = cpc_round(v)
    st = np.where(s >= 0.5, 1, np.where(s <= -0.5, -1, 0))
    st[np.isnan(v)] = 0
    lab = np.where(np.isnan(v), "", "neutral").astype(object)
    last = np.flatnonzero(~np.isnan(v)).max()
    i = 0
    while i < len(v):
        if st[i] == 0:
            i += 1
            continue
        j = i
        while j + 1 < len(v) and st[j + 1] == st[i]:
            j += 1
        if j - i + 1 >= 5:
            lab[i:j + 1] = "el_nino" if st[i] > 0 else "la_nina"
        elif j == last:
            lab[i:j + 1] = ""
        i = j + 1
    return pd.Series(lab, index=x.index, dtype="string")


def cpc_table(url: str) -> pd.DataFrame:
    """The season table on a CPC ONI/RONI page: year, centre month, value and colour class (warm/cold/normal).
    The RONI page marks cells with classes roni-warm/roni-cold/roni-normal, the ONI page with red/blue/black."""
    page = get(url).decode("utf-8", "replace")
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)(?=<tr[^>]*>|</tbody>|</table>)", page, re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)
        if not cells:
            continue
        yr = re.sub(r"<[^>]+>", "", cells[0]).strip()
        if not re.fullmatch(r"\d{4}", yr):
            continue
        for k, c in enumerate(cells[1:13]):
            v = re.sub(r"<[^>]+>", "", c).strip()
            if not re.fullmatch(r"-?\d+\.\d", v):
                continue
            m = re.search(r"(?:roni|oni)-(warm|cold|normal)|color:\s*(red|blue|black)", c)
            cls = {"red": "warm", "blue": "cold", "black": "normal"}.get(m.group(2), m.group(2)) if m and m.group(2) \
                else (m.group(1) if m else "")
            rows.append({"date": pd.Timestamp(int(yr), k + 1, 1), "cpc_value": float(v), "cpc_class": cls})
    return pd.DataFrame(rows, columns=["date", "cpc_value", "cpc_class"])


def cpc_episode_check(df: pd.DataFrame, col: str, url: str) -> None:
    """Our episode labels must match the colours CPC prints. CPC rounds from more digits than the two-decimal
    file, so a season at exactly +-0.45 can fall either way: mismatches are allowed only within 4 seasons of
    such a value, and where our run is still undetermined (CPC leaves it uncoloured)."""
    t = cpc_table(url)
    # a page that can no longer be read fails like any other source, so the check cannot quietly switch off
    gate(len(t) >= 900 and t.date.is_unique and not t.cpc_class.eq("").any(),
         f"enso {col}: CPC table read ({len(t)} seasons, dates unique {t.date.is_unique}, "
         f"{int(t.cpc_class.eq('').sum())} without a colour)")
    if len(t) == 0 or not t.date.is_unique:
        return
    # The page and the file are updated separately. Label the file as it stood at the page's last season, so a
    # run that the newer file has just completed (or the page has not reached yet) is compared like for like.
    anom = col.replace("_episode", "_anom")
    f = df[(df.date <= t.date.max()) & df[anom].notna()][["date", anom]].copy()
    f[col] = enso_episodes(f[anom]).to_numpy()
    m = f.merge(t, on="date", how="inner").reset_index(drop=True)
    if t.date.max() > f.date.max():
        # the page is ahead: its colours for our last 4 seasons may already use seasons the file lacks
        m = m[m.date <= f.date.max() - pd.DateOffset(months=4)].reset_index(drop=True)
    x = m[anom].to_numpy(dtype=float)
    ours = m[col].map({"el_nino": "warm", "la_nina": "cold", "neutral": "normal", "": "normal"})
    near = pd.Series(np.isclose(np.abs(x), 0.45, atol=1e-9)).rolling(9, center=True, min_periods=1).max().astype(bool)
    bad = (ours != m.cpc_class) & ~near
    exempt = int(((ours != m.cpc_class) & near).sum())
    gate(exempt <= EPISODE_EXEMPT_MAX, f"enso {col}: {exempt} seasons differ at a +-0.45 value (<= {EPISODE_EXEMPT_MAX})")
    gate(len(m) >= 900 and not bad.any(),
         f"enso {col}: CPC's coloured table, {len(m)} seasons: {int((ours == m.cpc_class).sum())} same colour "
         f"(an open run counts as uncoloured, as on CPC's page), {exempt} differ at a +-0.45 value, "
         f"{int(bad.sum())} unexplained"
         + (f"; unexplained: {m.date[bad].dt.strftime('%Y-%m').tolist()[:6]}" if bad.any() else ""))
    dv = np.abs(m.cpc_value - cpc_round(x))
    gate(bool((dv <= 0.1 + 1e-9).all()), f"enso {col}: CPC table values within 0.1 of the file (max {dv.max():.2f})")


def enso_monthly() -> pd.DataFrame:
    """ONI (ERSST v6 Niño 3.4, 3-month running mean, as now served by CPC's oni.ascii.txt) and RONI (relative
    ONI, NOAA CPC's official ENSO index per NWS statement 26-05), dated by the centre month of each season."""
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
    # oni.ascii.txt switched from ERSST v5 to v6 (DJF 1950: v5 -1.5, v6 -1.32). Another version change makes the
    # column descriptions wrong, not the data, so it is a warning.
    fresh(abs(df.oni_anom.iloc[0] - (-1.32)) <= 0.02,
          f"enso: ONI DJF 1950 {df.oni_anom.iloc[0]} is the ERSST v6 value -1.32 (else update the descriptions)")
    df["oni_episode"] = enso_episodes(df.oni_anom)
    df["roni_episode"] = enso_episodes(df.roni_anom)
    cpc_episode_check(df, "oni_episode", CPC_ONI_PAGE)
    cpc_episode_check(df, "roni_episode", CPC_RONI_PAGE)
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


# ---------------------------------------------------------------- climate drivers (monthly)
PDO_URL = "https://www.ncei.noaa.gov/pub/data/cmb/ersst/v5/index/ersst.v5.pdo.dat"
TSI_DIR = "https://www.ncei.noaa.gov/data/total-solar-irradiance/access/monthly/"
TSI_VERSION = "v03r00"
AGGI_TABLE = "https://gml.noaa.gov/aggi/AGGI_Table.csv"

# NOAA AGGI Table 1 (gml.noaa.gov/aggi/aggi.html): effective radiative forcing expressions of Forster et al. (2021)
# and Smith et al. (2021), with 1750 abundances C0, M0, N0. C, M, N are global CO2 (ppm), CH4 (ppb), N2O (ppb).
ERF_C0, ERF_M0, ERF_N0 = 278.3, 729.2, 270.1
# W m-2. Measured 2001-2024: max |diff| 0.0006 (Table 2 prints 3 decimals). A CO2 revision of 0.2 ppm, which the
# gases table accepts, moves the CO2 forcing by about 0.003, so the limit leaves room for it.
ERF_TOL = 0.004


def erf_gases(C, M, N):
    a1, b1, c1, d1, e1 = -2.48e-7, 7.59e-4, -2.15e-3, 5.2488, 1.05
    a2, b2, d2, e2 = -8.96e-5, -1.25e-4, 0.045194, 0.86
    a3, b3, c3, d3, e3 = -3.42e-4, 2.546e-4, -2.44e-4, 0.12173, 1.07
    alpha = d1 + a1 * (C - ERF_C0) ** 2 + b1 * (C - ERF_C0)
    co2 = (alpha + c1 * np.sqrt(N)) * np.log(C / ERF_C0) * e1
    ch4 = (a2 * np.sqrt(M) + b2 * np.sqrt(N) + d2) * (np.sqrt(M) - np.sqrt(ERF_M0)) * e2
    n2o = (a3 * np.sqrt(C) + b3 * np.sqrt(N) + c3 * np.sqrt(M) + d3) * (np.sqrt(N) - np.sqrt(ERF_N0)) * e3
    return co2, ch4, n2o


def erf_monthly(gases: pd.DataFrame) -> pd.DataFrame:
    w = gases.pivot(index="date", columns="series", values="average")[["co2_global", "ch4_global", "n2o_global"]].dropna()
    co2, ch4, n2o = erf_gases(w.co2_global, w.ch4_global, w.n2o_global)
    out = pd.DataFrame({"erf_co2_wm2": co2, "erf_ch4_wm2": ch4, "erf_n2o_wm2": n2o})
    out["erf_co2_ch4_n2o_wm2"] = out.sum(axis=1)
    # NOAA's own annual values: our annual mean of the monthly forcing must reproduce them
    t = pd.read_csv(io.StringIO(get(AGGI_TABLE).decode()), comment="#").set_index("Year")
    gate(list(t.columns[:3]) == ["CO2", "CH4", "N2O"], f"aggi: table columns {list(t.columns[:3])}")
    full = out.groupby(out.index.year).filter(lambda g: len(g) == 12)
    ann = full.groupby(full.index.year).mean().join(t[["CO2", "CH4", "N2O"]], how="inner")
    worst = max((ann[a] - ann[b]).abs().max() for a, b in
                [("erf_co2_wm2", "CO2"), ("erf_ch4_wm2", "CH4"), ("erf_n2o_wm2", "N2O")])
    gate(len(ann) >= 20 and worst <= ERF_TOL,
         f"erf: annual means vs NOAA AGGI Table 2 over {len(ann)} years, max |diff| {worst:.4f} W m-2 (<= {ERF_TOL})")
    return out.round(4).rename_axis("date").reset_index()


def pdo_monthly() -> pd.DataFrame:
    """NOAA NCEI's PDO index (ERSST v5 anomalies projected on the Mantua PDO pattern), monthly from 1854."""
    rows = []
    for ln in get(PDO_URL).decode().splitlines():
        p = ln.split()
        if len(p) == 13 and re.fullmatch(r"\d{4}", p[0]):
            for k, v in enumerate(p[1:]):
                if float(v) < 99:  # 99.99 = not yet available
                    rows.append({"date": pd.Timestamp(int(p[0]), k + 1, 1), "pdo": float(v)})
    df = pd.DataFrame(rows)
    gate(len(df) > 2000 and df.date.min() == pd.Timestamp("1854-01-01"), f"pdo: from 1854-01 ({len(df)} months)")
    gate(len(pd.date_range(df.date.min(), df.date.max(), freq="MS")) == len(df), "pdo: no missing months")
    gate(df.pdo.between(-5, 5).all(), f"pdo: within [-5,5] ({df.pdo.min()}..{df.pdo.max()})")
    fresh(lag_days(df.date.max()) <= 75, f"pdo: last month {df.date.max().date()} within 75 days")
    return df


def tsi_monthly() -> pd.DataFrame:
    """NOAA's Total Solar Irradiance CDR (monthly, one file per year, NNLTSI model), updated about once a year.
    Only the latest two years and years missing from the committed table are downloaded; earlier years come
    from the committed table, and history_guard keeps them from changing silently."""
    import netCDF4
    listing = get(TSI_DIR).decode()
    found = {(m.group(0), m.group(1), int(m.group(2)))
             for m in re.finditer(r"tsi_(v\d+r\d+)_monthly_s(\d{4})\d\d_e\d{6}_c\d{8}\.nc", listing)}
    names = {y: f for f, _, y in found}
    gate(len(found) > 100 and len(names) == len(found),
         f"tsi: {len(found)} files listed, one per year ({len(names)} years)")
    versions = sorted({v for _, v, _ in found})
    gate(versions == [TSI_VERSION], f"tsi: every file is version {TSI_VERSION} ({versions}); "
                                     "a new version needs a human look")
    path = DATA / "climate_drivers_monthly.csv"
    old = pd.read_csv(path, parse_dates=["date"])[["date", "tsi_wm2"]].dropna() if path.exists() else None
    have = set(old.date.dt.year) if old is not None else set()
    latest = max(names)
    want = sorted(y for y in names if y >= latest - 1 or y not in have)
    parts = [old[~old.date.dt.year.isin(want)]] if old is not None else []
    with tempfile.TemporaryDirectory() as td:
        for y in want:
            f = Path(td) / names[y]
            get(TSI_DIR + names[y], f)
            with netCDF4.Dataset(f) as ds:
                t = ds["time"]
                d = netCDF4.num2date(t[:], t.units, only_use_cftime_datetimes=False)
                parts.append(pd.DataFrame({"date": [pd.Timestamp(x.year, x.month, 1) for x in d],
                                           "tsi_wm2": np.ma.filled(ds["TSI"][:].astype(float), np.nan).round(4)}))
    # the newly downloaded years must continue the committed ones: a recalibrated release would be spliced onto
    # old values otherwise
    if old is not None:
        new = pd.concat(parts[1:]).set_index("date").tsi_wm2
        both = pd.concat([old.set_index("date").tsi_wm2, new], axis=1, keys=["old", "new"]).dropna()
        d = (both.old - both.new).abs().max() if len(both) else 0.0
        gate(len(both) >= 12 and d <= 0.01, f"tsi: re-downloaded {len(both)} committed months unchanged (max diff {d})")
    df = pd.concat(parts).sort_values("date").reset_index(drop=True)
    gate(df.date.is_unique and len(pd.date_range(df.date.min(), df.date.max(), freq="MS")) == len(df),
         f"tsi: months unique and contiguous ({df.date.min().date()}..{df.date.max().date()})")
    gate(df.tsi_wm2.between(1355, 1368).all(), f"tsi: within [1355,1368] W m-2 ({df.tsi_wm2.min()}..{df.tsi_wm2.max()})")
    fresh(lag_days(df.date.max()) <= 500, f"tsi: last month {df.date.max().date()} within 500 days (yearly releases)")
    print(f"tsi: downloaded {len(want)} yearly files ({want[0]}..{want[-1]})", flush=True)
    return df


def climate_drivers(gases: pd.DataFrame) -> pd.DataFrame:
    df = pdo_monthly().merge(tsi_monthly(), on="date", how="outer").merge(erf_monthly(gases), on="date", how="outer")
    df = df.sort_values("date").reset_index(drop=True)
    gate(df.date.is_unique and len(pd.date_range(df.date.min(), df.date.max(), freq="MS")) == len(df),
         "drivers: months unique and contiguous")
    return df[["date", "erf_co2_wm2", "erf_ch4_wm2", "erf_n2o_wm2", "erf_co2_ch4_n2o_wm2", "tsi_wm2", "pdo"]]


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
    drv = t["climate_drivers_monthly"]
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
    # columns added in v4 go last: Kaggle carries column descriptions over by position
    d = drv.set_index(drv.date.dt.to_period("M"))
    cols += [mmean(air_, "global_2t_anom_preindustrial", "air_temp_global_anom_preindustrial"),
             mmean(sst_, "amo_ts06", "sst_amo_ts06"),
             e["roni_episode"], d["erf_co2_ch4_n2o_wm2"], d["tsi_wm2"], d["pdo"]]
    df = pd.concat(cols, axis=1).sort_index()
    df = df[df.index >= pd.Period("1950-01", "M")].dropna(how="all")
    df.insert(0, "date", df.index.to_timestamp())
    df = df.reset_index(drop=True)
    gate(df.date.is_unique, "panel: months unique")
    gate(len(pd.date_range(df.date.min(), df.date.max(), freq="MS")) == len(df), "panel: no missing months")
    return df


# ---------------------------------------------------------------- history guard
# Publishers that say their published values may be revised by quality control get a bounded
# allowance per update instead of exact equality: (table, column naming the series, largest allowed
# absolute change per series, in the series' own unit). Every numeric column of an older row must stay
# within it, and missing values and the other columns must still match exactly.
# NOAA GML: "These values are subject to change depending on quality control checks of the measured
# data, but any revisions are expected to be small" (gml.noaa.gov/ccgg/trends/data.html). Its
# 2026-10-09 reprocessing moved rows older than 400 days by at most CH4 0.19 ppb, CO2 0.06 ppm
# (global and Mauna Loa), N2O 0.02 ppb and SF6 0.01 ppt. The limits sit at 2.5-3.3 times that. On the
# published history a one-month shift of either column breaks them in at least 30% of the older rows of
# every series (SF6 104 of 339) and an average/deseasonalized swap in at least 8 rows (SF6, whose
# seasonal cycle is tiny); one row is enough to stop the update.
# A larger revision, like a calibration-scale change, still stops the update for a human look.
REVISION_TOLERANCE: dict[str, tuple[str, dict[str, float]]] = {
    "greenhouse_gases_monthly": ("series", {
        "co2_mlo": 0.2, "co2_global": 0.2, "ch4_global": 0.5, "n2o_global": 0.05, "sf6_global": 0.03,
    }),
}


def history_guard(name: str, new: pd.DataFrame, keys: list[str], recent_days: int | None) -> None:
    """Rows already published must not disappear, and rows older than the revision window
    must not change. A deliberate upstream reprocessing fails here and needs a human look,
    unless the table has a REVISION_TOLERANCE, in which case small revisions pass and are reported.
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
    if name in REVISION_TOLERANCE and len(o) and num:
        by, limits = REVISION_TOLERANCE[name]
        series = o.index.get_level_values(by)
        gate(set(series) <= set(limits), f"{name}: every {by} has a revision limit (missing: "
             f"{sorted(set(series) - set(limits))})")
        allowed = pd.Series(series.map(limits), index=o.index, dtype=float)
        diff = (o[num] - n[num].astype(float)).abs().max(axis=1)
        used = diff.div(allowed).fillna(0.0)
        worst = used.groupby(level=by).max()
        print(f"INFO {name}: {int((diff > 1e-9).sum())} rows older than {recent_days} days revised; "
              f"largest share of the limit per {by}: " + ", ".join(f"{k} {v:.2f}" for k, v in worst.items()))
        gate(bool((used <= 1.0).all() and same_nan and same_other),
             f"{name}: values older than {recent_days} days within the per-series revision limit "
             f"(max diff {d}, largest share of limit {used.max():.2f}, missing values same: {same_nan}, "
             f"other columns same: {same_other})")
        return
    gate(bool(np.nan_to_num(d) < 1e-6 and same_nan and same_other),
         f"{name}: values older than {recent_days} days unchanged (max diff {d}, other columns same: {same_other})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sst-backfill-from", type=int, default=None)
    ap.add_argument("--sst-years", type=int, nargs="*", default=None,
                    help="recompute only these years (testing)")
    args = ap.parse_args()

    gases = gases_monthly()
    tables = {
        "sea_ice_extent_daily": (sea_ice(), ["date", "hemisphere"], 30),
        "co2_daily_mauna_loa": (co2_daily(), ["date"], 60),
        "greenhouse_gases_monthly": (gases, ["series", "date"], 400),
        "sst_daily": (sst(args.sst_backfill_from, args.sst_years), ["date"], 60),
        "air_temperature_daily": (air(), ["date"], 120),
        "enso_monthly": (enso_monthly(), ["date"], None),
        "atmosphere_indices_daily": (atmosphere_daily(), ["date"], None),
        # PDO follows ERSST revisions, TSI is reprocessed with each yearly release and the forcing follows NOAA
        # GML's gas revisions: revisable like the CPC indices (rows may not disappear).
        "climate_drivers_monthly": (climate_drivers(gases), ["date"], None),
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


# Exit status for "a source could not be reached": the workflow lets a scheduled run end with a
# warning instead of a failure, as long as the data were updated recently (see update.yml).
EXIT_TRANSIENT = 75

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        if not transient(e):
            raise
        import traceback
        traceback.print_exc()
        print(f"\nA source could not be reached ({type(e).__name__}: {e}); nothing written.", flush=True)
        sys.exit(EXIT_TRANSIENT)
