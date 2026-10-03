"""Write kaggle/dataset-metadata.json and kaggle/settings.json from one list of file and column
descriptions, and check that it names exactly the columns of the files in data/.

Kaggle's public API does not set per-column descriptions, so settings.json is what gets entered
in the dataset page by hand (see README); dataset-metadata.json is what `kaggle datasets version`
uploads. Keeping both generated from here stops them drifting apart."""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
C = "degrees Celsius"
ANOM = "minus its 1991-2020 mean for the same calendar day (Feb 29 uses Feb 28's normal)"
CAUSAL = ("counting only this year and earlier years, so the value never changes once published. Feb 29 is "
          "ranked among leap years only")

SST_REGIONS = {
    "world_60s_60n": "60S-60N, all longitudes (the near-global series least affected by sea ice)",
    "north_atlantic_0_60n_0_80w": "North Atlantic, 0-60N, 80W-0",
    "world_90s_90n": "the whole globe, 90S-90N (cells under sea ice carry OISST's freezing-point value)",
    "tropics_20s_20n": "tropics, 20S-20N, all longitudes",
    "north_pacific_20n_60n_120e_110w": "North Pacific, 20N-60N, 120E-110W",
    "nino12_0_10s_90w_80w": "Niño 1+2 box, 0-10S, 90W-80W (NOAA CPC definition)",
    "nino3_5n_5s_150w_90w": "Niño 3 box, 5N-5S, 150W-90W (NOAA CPC definition)",
    "nino34_5n_5s_170w_120w": "Niño 3.4 box, 5N-5S, 170W-120W (NOAA CPC definition; the main ENSO box)",
    "nino4_5n_5s_160e_150w": "Niño 4 box, 5N-5S, 160E-150W (NOAA CPC definition)",
    "iod_west_10s_10n_50e_70e": "western pole of the Indian Ocean Dipole, 10S-10N, 50E-70E (Saji et al. 1999)",
    "iod_east_10s_0_90e_110e": "eastern pole of the Indian Ocean Dipole, 10S-0, 90E-110E (Saji et al. 1999)",
}


def rank_cols(prefix: str, what: str) -> dict:
    return {
        f"{prefix}_rank_warmest_for_day": f"Rank of {what} among the same calendar day of every year up to and "
                                          f"including this one (1 = warmest), {CAUSAL}",
        f"{prefix}_record_warm_for_day": f"True when {what} is warmer than on the same calendar day of every "
                                         f"earlier year; empty until 10 earlier years exist for that day",
    }


FILES = {
    "sea_ice_extent_daily.csv": (
        "Daily sea ice extent for the Arctic, the Antarctic and both combined (NSIDC Sea Ice Index v4), "
        "with the anomaly, the rank for the calendar day, a record-low flag and the change from a year earlier.",
        {
            "date": "Date (UTC), YYYY-MM-DD",
            "hemisphere": "north (Arctic), south (Antarctic) or global (north + south, on days both are reported)",
            "extent_mkm2": "Sea ice extent in million square kilometres (area with at least 15% ice concentration)",
            "missing_mkm2": "Area with missing satellite data, million square kilometres (0 on almost all days)",
            "extent_5day_mean_mkm2": "Trailing 5-day mean of extent (this day and the 4 before) as used in NSIDC "
                                     "charts; empty where those 5 days are not all present (every-other-day era before 1987-08)",
            "extent_anom_1991_2020_mkm2": f"extent_mkm2 {ANOM}, million square kilometres",
            "rank_lowest_for_day": f"Rank of extent_mkm2 among the same calendar day of every year up to and "
                                   f"including this one (1 = lowest), {CAUSAL}",
            "record_low_for_day": "True when extent is below the same calendar day of every earlier year "
                                  "(a record low for the date); empty until 10 earlier years exist for that day",
            "yoy_change_mkm2": "extent_mkm2 minus the value on the same date one year earlier; empty if that "
                               "day was not observed (and on Feb 29)",
        }),
    "co2_daily_mauna_loa.csv": (
        "Daily mean atmospheric CO2 at Mauna Loa Observatory (NOAA GML); days without valid measurements are "
        "absent. With the change from a year earlier and a record-high flag.",
        {
            "date": "Date, YYYY-MM-DD",
            "decimal_date": "Date as a decimal year, as published by NOAA GML",
            "co2_ppm": "Daily mean CO2 mole fraction, parts per million (dry air)",
            "yoy_change_ppm": "co2_ppm minus the value on the same date one year earlier, ppm; empty when either "
                              "day has no measurement (Mauna Loa has gaps, e.g. 2025-09-27 to 10-06)",
            "record_high": "True when co2_ppm is above every earlier daily value in the file",
        }),
    "greenhouse_gases_monthly.csv": (
        "Monthly greenhouse gas averages from NOAA GML in long format (one row per series and month).",
        {
            "series": "co2_mlo (Mauna Loa, ppm), co2_global (ppm), ch4_global (ppb), n2o_global (ppb) or "
                      "sf6_global (ppt)",
            "date": "First day of the month, YYYY-MM-DD",
            "average": "Monthly mean in the series' unit; empty where NOAA reports missing",
            "deseasonalized": "Monthly value with the average seasonal cycle removed, as published by NOAA GML",
            "interpolated": "co2_mlo only, from 1974-05: True where NOAA filled a missing month by interpolation "
                            "(negative stdev in the source file); empty for other series and for the Scripps "
                            "months before 1974-05",
            "yoy_change": "average minus the same month one year earlier, in the series' unit (the annual growth)",
        }),
    "sst_daily.csv": (
        "Daily area-weighted mean sea surface temperature for 11 ocean regions, computed from the full NOAA "
        "OISST v2.1 0.25-degree grid (cos-latitude weights, ocean cells only), with anomalies, records and two "
        "climate indices (Indian Ocean Dipole, relative Niño 3.4).",
        {
            "date": "Date, YYYY-MM-DD",
            **{k: f"Mean SST over {v}, {C}" for k, v in SST_REGIONS.items()},
            **{f"{k}_anom_1991_2020": f"{k} {ANOM}, {C}" for k in SST_REGIONS},
            **rank_cols("world_60s_60n", "world_60s_60n"),
            **rank_cols("world_90s_90n", "world_90s_90n"),
            "dmi": "Dipole Mode Index of the Indian Ocean Dipole: western-pole anomaly minus eastern-pole anomaly "
                   f"(Saji et al. 1999), {C}; positive = positive IOD",
            "nino34_minus_tropics_anom": "Niño 3.4 anomaly minus the tropical (20S-20N) mean anomaly, "
                                         f"{C}. The relative Niño 3.4 behind NOAA CPC's RONI, without CPC's "
                                         "variance scaling or 3-month averaging",
        }),
    "air_temperature_daily.csv": (
        "Daily global mean 2 m air temperature from ERA5 (Copernicus Climate Change Service, via Climate Pulse), "
        "1940 to present, with C3S's 1991-2020 climatology and anomaly, the rank for the calendar day and a "
        "record flag.",
        {
            "date": "Date (UTC), YYYY-MM-DD",
            "global_2t": f"Daily global mean near-surface (2 m) air temperature from ERA5 hourly values, {C}",
            "global_2t_clim_1991_2020": f"C3S daily climatology for 1991-2020 for this calendar day, {C}",
            "global_2t_anom_1991_2020": f"global_2t minus global_2t_clim_1991_2020 as published by C3S, {C}",
            "status": "final, or preliminary for the most recent days (based on ERA5 data not yet final; "
                      "usually revised slightly within a few days)",
            **rank_cols("global", "global_2t"),
        }),
    "enso_monthly.csv": (
        "El Niño-Southern Oscillation indices from NOAA CPC: the Oceanic Niño Index (ONI) and the Relative ONI "
        "(RONI), which NOAA uses for official ENSO monitoring per NWS Public Information Statement 26-05. "
        "One row per overlapping 3-month season.",
        {
            "date": "First day of the centre month of the 3-month season (e.g. DJF 1950 -> 1950-01-01)",
            "season": "3-month season label as published by CPC (DJF, JFM, ... NDJ)",
            "oni_total_c": f"Niño 3.4 SST from ERSSTv5, 3-month mean, {C}",
            "oni_anom": "ONI: 3-month mean Niño 3.4 anomaly as published by CPC, degrees Celsius. El Niño "
                        "conditions at +0.5 or above, La Niña at -0.5 or below",
            "roni_anom": "RONI: ONI-style index of the Niño 3.4 anomaly relative to the tropical mean, as "
                         f"published by CPC, {C}",
        }),
    "atmosphere_indices_daily.csv": (
        "Daily standardized atmospheric circulation indices from NOAA CPC: Arctic Oscillation, North Atlantic "
        "Oscillation, Pacific-North American pattern and Antarctic Oscillation.",
        {
            "date": "Date, YYYY-MM-DD",
            "ao": "Arctic Oscillation index (1000 hPa height), standardized, unitless",
            "nao": "North Atlantic Oscillation index (500 hPa height), standardized, unitless",
            "pna": "Pacific-North American pattern index (500 hPa height), standardized, unitless",
            "aao": "Antarctic Oscillation (Southern Annular Mode) index (700 hPa height), standardized, "
                   "unitless; from 1979",
        }),
    "climate_monthly_panel.csv": (
        "Every indicator side by side, one row per month from 1950: monthly means of the daily tables plus the "
        "monthly gases and ENSO indices. Ready for correlation, regression or forecasting.",
        {
            "date": "First day of the month, YYYY-MM-DD",
            "air_temp_global_anom": f"Monthly mean of air_temperature_daily.global_2t_anom_1991_2020 (ERA5), {C}",
            "sst_world_60s_60n_anom": f"Monthly mean of sst_daily.world_60s_60n_anom_1991_2020, {C}",
            "sst_nino34_anom": f"Monthly mean of sst_daily.nino34_5n_5s_170w_120w_anom_1991_2020, {C}",
            "sst_dmi": f"Monthly mean of sst_daily.dmi (Indian Ocean Dipole), {C}",
            "sea_ice_north_mkm2": "Monthly mean Arctic sea ice extent, million square kilometres",
            "sea_ice_south_mkm2": "Monthly mean Antarctic sea ice extent, million square kilometres",
            "sea_ice_north_anom_mkm2": "Monthly mean Arctic extent anomaly against 1991-2020, million km2",
            "sea_ice_south_anom_mkm2": "Monthly mean Antarctic extent anomaly against 1991-2020, million km2",
            "ao": "Monthly mean of the daily Arctic Oscillation index",
            "nao": "Monthly mean of the daily North Atlantic Oscillation index",
            "pna": "Monthly mean of the daily Pacific-North American index",
            "aao": "Monthly mean of the daily Antarctic Oscillation index",
            "co2_mlo_ppm": "Monthly mean CO2 at Mauna Loa, ppm",
            "co2_global_ppm": "Monthly global marine surface CO2, ppm",
            "ch4_global_ppb": "Monthly global methane, ppb",
            "n2o_global_ppb": "Monthly global nitrous oxide, ppb",
            "sf6_global_ppt": "Monthly global sulfur hexafluoride, ppt",
            "oni_anom": "ONI for the 3-month season centred on this month",
            "roni_anom": "RONI for the 3-month season centred on this month",
        }),
}
PANEL_NOTE = (" Daily-derived columns are filled only when at least 90% of the month's days are present (40% "
              "for sea ice, observed every other day before 1987-08); empty otherwise.")

SOURCES = (
    "NSIDC Sea Ice Index v4 (G02135, doi:10.7265/a98x-0f50); NOAA Global Monitoring Laboratory CO2, CH4, N2O "
    "and SF6 trends (gml.noaa.gov/ccgg/trends, with Scripps Institution of Oceanography for Mauna Loa CO2); "
    "NOAA OISST v2.1 daily 0.25-degree SST via NOAA PSL, cross-checked against NCEI and NOAA CPC; ERA5 daily "
    "global 2 m air temperature from the Copernicus Climate Change Service (Climate Pulse); NOAA CPC ONI, RONI and daily AO/NAO/PNA/AAO. Built "
    "daily by scripts/build.py in https://github.com/yasumorishima/earth-vital-signs, which publishes nothing "
    "unless every check passes."
)
TYPES = {"date": "datetime"}


def col_type(name: str, sample: list[str]) -> str:
    if name in TYPES:
        return TYPES[name]
    vals = [v for v in sample if v != ""]
    if vals and all(v in ("True", "False") for v in vals):
        return "boolean"
    try:
        [float(v) for v in vals]
        return "integer" if vals and all(v.lstrip("-").isdigit() for v in vals) else "number"
    except ValueError:
        return "string"


def main() -> int:
    meta_path = ROOT / "kaggle" / "dataset-metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    desc_md = (ROOT / "kaggle" / "description.md").read_text(encoding="utf-8")
    bad = []
    resources = []
    settings_files = {}
    for fname, (fdesc, cols) in FILES.items():
        if fname == "climate_monthly_panel.csv":
            fdesc += PANEL_NOTE
        with open(ROOT / "data" / fname, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        header, body = rows[0], rows[1:]
        if header != list(cols):
            bad.append(f"{fname}: data columns {header} != described {list(cols)}")
            continue
        fields = [{"name": c, "type": col_type(c, [r[i] for r in body]), "description": cols[c]}
                  for i, c in enumerate(header)]
        resources.append({"path": fname, "description": fdesc, "schema": {"fields": fields}})
        settings_files[fname] = {"description": fdesc, "columns": dict(cols)}
    if bad:
        print("\n".join(bad))
        return 1
    meta["description"] = desc_md.strip()
    meta["resources"] = resources
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    settings = {"id": meta["id"], "expectedUpdateFrequency": meta["expectedUpdateFrequency"],
                "userSpecifiedSources": SOURCES, "files": settings_files}
    (ROOT / "kaggle" / "settings.json").write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
                                                   encoding="utf-8")
    print(f"{len(resources)} files, {sum(len(r['schema']['fields']) for r in resources)} columns")
    return 0


if __name__ == "__main__":
    sys.exit(main())
