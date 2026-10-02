"""Fetch, derive and gate the Earth Vital Signs tables.

Writes the committed history in data/ and the Kaggle upload folder in _build/.
Every table passes its gates before anything is written; a failed gate exits non-zero
and leaves data/ untouched, so a broken upstream never replaces good history.

Sources (all redistributable with attribution; see README):
  NSIDC Sea Ice Index v4 (G02135)            daily sea ice extent, both hemispheres
  NOAA GML Mauna Loa and global networks     CO2 daily/monthly, CH4/N2O/SF6 monthly
  NOAA OISST v2.1 via NOAA PSL               daily SST, area-weighted means derived here
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import shutil
import sys
import tempfile
import time
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

FAIL: list[str] = []


def gate(ok: bool, msg: str) -> None:
    print(("PASS " if ok else "FAIL ") + msg, flush=True)
    if not ok:
        FAIL.append(msg)


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
            except Exception as e:  # network errors are retried, then raised
                if i == tries - 1:
                    raise
                print(f"retry {i + 1} {url}: {e}", flush=True)
                time.sleep(10 * (i + 1))
    total = None
    stalls = 0
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
                if total is None:
                    total = int(r.headers["Content-Length"])
                with open(dest, "ab") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
        except OSError as e:
            if "ignored Range" in str(e) or "more than" in str(e):
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
    for h, g in out.groupby("hemisphere"):
        lo, hi = (2.5, 17.5) if h == "north" else (1.0, 21.0)
        gate(g.date.is_unique, f"sea_ice {h}: dates unique")
        gate(g.extent_mkm2.between(lo, hi).all(), f"sea_ice {h}: extent within [{lo},{hi}]")
        gate(g.date.min() == pd.Timestamp("1978-10-26"), f"sea_ice {h}: starts 1978-10-26")
        gate(lag_days(g.date.max()) <= 6, f"sea_ice {h}: last date {g.date.max().date()} within 6 days")
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
    gate(lag_days(df.date.max()) <= 21, f"co2_daily: last date {df.date.max().date()} within 21 days")
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
        gate(lag_days(g.date.max()) <= 200, f"gases {name}: last month {g.date.max().date()} within 200 days")
        out.append(g)
    return pd.concat(out).reset_index(drop=True)


# ---------------------------------------------------------------- SST
REGIONS = {  # name: (lat_min, lat_max, lon_min, lon_max) in OISST 0..360 longitudes
    "world_60s_60n": (-60, 60, 0, 360),
    "north_atlantic_0_60n_0_80w": (0, 60, 280, 360),
}


def region_means(sst2d, lat: np.ndarray, lon: np.ndarray) -> dict[str, float]:
    res = {}
    x_all = np.ma.masked_invalid(np.ma.asarray(sst2d, dtype=np.float64))
    for name, (a, b, c, d) in REGIONS.items():
        li = (lat >= a) & (lat <= b)
        lj = (lon >= c) & (lon <= d)
        x = x_all[np.ix_(li, lj)]
        w = np.broadcast_to(np.cos(np.deg2rad(lat[li]))[:, None], x.shape)
        w = np.ma.array(w, mask=np.ma.getmaskarray(x))
        res[name] = float((x * w).sum() / w.sum())
    return res


def sst_year(y: int, tmp: Path) -> pd.DataFrame:
    import netCDF4
    f = tmp / f"sst.{y}.nc"
    t0 = time.time()
    get(PSL_SST.format(y=y), f)
    rows = []
    with netCDF4.Dataset(f) as ds:
        lat, lon = np.asarray(ds["lat"][:]), np.asarray(ds["lon"][:])
        t = ds["time"]
        dates = netCDF4.num2date(t[:], t.units, only_use_cftime_datetimes=False)
        for i, d in enumerate(dates):
            m = region_means(ds["sst"][i, :, :], lat, lon)
            rows.append({"date": pd.Timestamp(d.year, d.month, d.day), **m})
    f.unlink()
    print(f"sst {y}: {len(rows)} days in {time.time() - t0:.0f}s", flush=True)
    return pd.DataFrame(rows)


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


def sst(backfill_from: int | None, years_only: list[int] | None = None) -> pd.DataFrame:
    path = DATA / "sst_daily.csv"
    old = pd.read_csv(path, parse_dates=["date"]) if path.exists() else pd.DataFrame(columns=["date"])
    if years_only:
        years = years_only
    elif backfill_from:
        years = list(range(backfill_from, TODAY.year + 1))
    else:
        years = [TODAY.year]
        if TODAY.timetuple().tm_yday <= 45:
            years.insert(0, TODAY.year - 1)  # finalize the previous year's preliminary days
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        new = pd.concat([sst_year(y, tmp) for y in years])
        keep = old[~pd.to_datetime(old.date).dt.year.isin(years)]
        keep = keep[[c for c in keep.columns if c == "date" or c in REGIONS]]
        df = pd.concat([keep, new])
        df["date"] = pd.to_datetime(df.date)
        df = df.sort_values("date").reset_index(drop=True)
        sst_crosscheck(df, tmp)
    for k in REGIONS:
        df[k] = df[k].round(4)
    # anomaly against the 1991-2020 day-of-year mean (WMO normal); Feb 29 uses Feb 28's normal
    doy = df.date.dt.strftime("%m-%d").replace("02-29", "02-28")
    base = df.date.dt.year.between(1991, 2020)
    for k in REGIONS:
        norm = df[base].groupby(doy[base])[k].mean()
        df[k + "_anom_1991_2020"] = (df[k] - doy.map(norm)).round(4)
    gate(df.date.is_unique, "sst: dates unique")
    gate(df.date.min() == pd.Timestamp("1981-09-01"), f"sst: starts 1981-09-01 (got {df.date.min().date()})")
    full = pd.date_range(df.date.min(), df.date.max(), freq="D")
    gate(len(full) == len(df), f"sst: no missing days ({len(full) - len(df)} missing)")
    gate(df["world_60s_60n"].between(19.0, 22.0).all(), "sst: world 60S-60N within [19,22] C")
    gate(lag_days(df.date.max()) <= 7, f"sst: last date {df.date.max().date()} within 7 days")
    return df


# ---------------------------------------------------------------- history guard
def history_guard(name: str, new: pd.DataFrame, keys: list[str], recent_days: int) -> None:
    """Rows already published must not disappear, and rows older than the revision window
    must not change. A deliberate upstream reprocessing fails here and needs a human look."""
    path = DATA / f"{name}.csv"
    if not path.exists():
        print(f"INFO {name}: no committed history yet")
        return
    old = pd.read_csv(path, parse_dates=["date"])
    gate(len(new) >= len(old), f"{name}: rows {len(old)} -> {len(new)} (no shrink)")
    cutoff = pd.Timestamp(TODAY) - pd.Timedelta(days=recent_days)
    o = old[old.date < cutoff].set_index(keys).sort_index()
    n = new[new.date < cutoff].set_index(keys).sort_index()
    gate(o.index.isin(n.index).all(), f"{name}: every published row older than {recent_days} days still present")
    n = n.reindex(o.index)
    num = [c for c in o.select_dtypes("number").columns if c in n.columns and "anom" not in c]
    d = (o[num] - n[num]).abs().max().max() if len(o) and num else 0.0
    same_nan = bool((o[num].isna() == n[num].isna()).all().all()) if num else True
    gate(bool(np.nan_to_num(d) < 1e-6 and same_nan),
         f"{name}: values older than {recent_days} days unchanged (max diff {d})")


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
    }
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
    (DATA / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
