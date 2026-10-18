# Earth Vital Signs

The planet's headline climate indicators as tidy CSV, rebuilt every day by GitHub Actions from the original NOAA, NSIDC and Copernicus files.

| File | What | From |
|---|---|---|
| [`data/sea_ice_extent_daily.csv`](data/sea_ice_extent_daily.csv) | Arctic, Antarctic and global sea ice extent, with anomaly, rank for the date, record-low flag, year-over-year change | 1978-10-26 |
| [`data/co2_daily_mauna_loa.csv`](data/co2_daily_mauna_loa.csv) | Daily CO2 at Mauna Loa, with year-over-year change and record-high flag | 1974-05 |
| [`data/greenhouse_gases_monthly.csv`](data/greenhouse_gases_monthly.csv) | Monthly CO2 (Mauna Loa, global), CH4, N2O, SF6, with annual growth | 1958 / 1979+ |
| [`data/sst_daily.csv`](data/sst_daily.csv) | Daily sea surface temperature for 11 regions (incl. Niño 1+2, 3, 3.4, 4 and the Indian Ocean Dipole poles), 1991-2020 anomalies, records, DMI, relative Niño 3.4 | 1981-09-01 |
| [`data/air_temperature_daily.csv`](data/air_temperature_daily.csv) | Daily global 2 m air temperature (ERA5, via Copernicus Climate Pulse) with the 1991-2020 anomaly, rank for the date and record flag, the anomaly above 1850-1900 as C3S computes it, days above 1.5 / 2.0 C, a 365-day mean, and the year-to-date mean with its rank | 1940-01-01 |
| [`data/enso_monthly.csv`](data/enso_monthly.csv) | Oceanic Niño Index (ONI) and Relative ONI (RONI) from NOAA CPC, with El Niño / La Niña episodes by CPC's rule | 1950 |
| [`data/atmosphere_indices_daily.csv`](data/atmosphere_indices_daily.csv) | Daily AO, NAO, PNA and AAO from NOAA CPC | 1950-01-01 |
| [`data/climate_drivers_monthly.csv`](data/climate_drivers_monthly.csv) | Greenhouse gas effective radiative forcing (CO2, CH4, N2O; NOAA AGGI expressions), total solar irradiance (NOAA CDR), PDO (NOAA NCEI) | 1854-01 |
| [`data/climate_monthly_panel.csv`](data/climate_monthly_panel.csv) | Everything above as monthly columns, one row per month | 1950-01 |

Column-by-column descriptions are in [`kaggle/settings.json`](kaggle/settings.json) (generated with the Kaggle metadata by `scripts/make_kaggle_meta.py`, which fails if a description and the data disagree). The latest dates are in [`data/summary.json`](data/summary.json). A starter notebook is in [`notebooks/`](notebooks/earth-vital-signs-starter.ipynb). The same files are published on Kaggle as [**Earth Vital Signs - Daily Climate Indicators**](https://www.kaggle.com/datasets/yasunorim/earth-vital-signs-daily), with a new version each day the data changes, and the starter notebook runs there as [Earth Vital Signs: a first look](https://www.kaggle.com/code/yasunorim/earth-vital-signs-a-first-look). A second notebook, [Is today a climate record?](https://www.kaggle.com/code/yasunorim/earth-vital-signs-is-today-a-climate-record) ([source](notebooks/todays-records/todays-records.ipynb)), ranks the latest day of each series against the same date in every earlier year and is re-run on Kaggle after each daily version. A third, [Will this year be the warmest?](https://www.kaggle.com/code/yasunorim/earth-vital-signs-will-this-year-be-the-warmest) ([source](notebooks/hottest-year/hottest-year.ipynb)), follows the year-to-date race against the record year with an animation, the pace the rest of the year would need, the days above 1.5 C and the El Nino lag; it is re-run the same way.

## Why another copy

The most-used Kaggle copies of these series stopped updating in 2017-2019. This one is regenerated daily, adds what the sources do not publish (daily Niño-box and regional SST, ranks and records for each calendar day, a monthly panel of all indicators), and only changes after checks pass.

Ranks and record flags compare a value with the same calendar day of its own year and earlier years only, so later years never change an earlier day (a recent day can still move while the source may revise its value), and the columns can be used as targets without look-ahead.

## How each update is checked

`scripts/build.py` fetches every source, then runs gates before writing anything. One failure stops the whole update and leaves `data/` as it was.

- Dates are unique; the daily SST and air temperature series have no missing days and every region is defined every day.
- Values are inside a plausible physical range.
- The latest date is recent, so a source that silently stopped is flagged (as a warning, recorded in `summary.json` under `stale_sources`).
- Every row already published that is older than the revision window is still present and unchanged, in every published column. New columns may be added; none may be dropped. Sources revise recent values (preliminary SST for about two weeks, CO2 after calibration), so only older rows are frozen. NOAA GML says its greenhouse gas values may be revised slightly after quality control, so in that table each update may move an older value by at most 0.2 ppm (CO2), 0.5 ppb (CH4), 0.05 ppb (N2O) or 0.03 ppt (SF6). If a source reprocesses its history beyond that, the update stops for a human to look. NOAA CPC re-normalizes its indices, so for those tables only the rows are guaranteed.
- SST means are computed here from the full 0.25-degree OISST v2.1 grid (cos-latitude weights, ocean cells only). Each run recomputes one finalized day from NCEI's separate per-day file and requires the same means for all 11 regions to within 0.005 C, compares the 7-day means of the four Niño boxes with NOAA CPC's weekly Niño SST over the last year, and compares the 60S-60N anomaly with ERA5's independent SST (Copernicus Climate Pulse).
- The derived columns are checked against what their publishers print. The anomaly above 1850-1900 uses C3S's day-of-year offset (0.88 + 0.05 sin + 0.07 cos, 0.79-0.97 C); its yearly means must reproduce C3S's published 1.48 C (2023), 1.60 C (2024) and 1.47 C (2025), and the offset's peak and dip must fall in the months C3S states. The El Niño / La Niña episodes must match the colours of CPC's own ONI and RONI tables (a difference is allowed only at a value of exactly +-0.45, which CPC rounds from more digits than it publishes, or where a run is still open). The annual means of the greenhouse gas forcing must reproduce NOAA's AGGI table within 0.004 W m-2. The solar irradiance years already published must come back unchanged when re-downloaded.
- `scripts/test_get.py` runs first and checks that the downloader fails closed on a 404, a server that ignores HTTP Range, and a file replaced mid-download, and resumes dropped connections to the exact bytes. `scripts/test_derived.py` checks the offset, the episode rule, the CPC table parser, the year-to-date means and the forcing expressions against values worked out by hand.

## Sources and citation

Please cite the producers, not this repository.

- **Sea ice**: Fetterer, F., Knowles, K., Meier, W. N., Savoie, M., Windnagel, A. K. & Stafford, T. (2025). *Sea Ice Index* (G02135, Version 4). Boulder, Colorado USA. NSIDC: National Snow and Ice Data Center. https://doi.org/10.7265/a98x-0f50
- **Mauna Loa CO2**: Dr. Xin Lan, NOAA/GML (gml.noaa.gov/ccgg/trends/) and Dr. Ralph Keeling, Scripps Institution of Oceanography (scrippsco2.ucsd.edu/).
- **Global greenhouse gases**: Lan, X., Tans, P. and K.W. Thoning: Trends in globally-averaged CO2 determined from NOAA Global Monitoring Laboratory measurements. https://doi.org/10.15138/9N0H-ZH07, and the CH4, N2O and SF6 pages at https://gml.noaa.gov/ccgg/trends/
- **Sea surface temperature**: NOAA OISST v2.1 (Huang, B. et al. 2021, *J. Climate*, doi:10.1175/JCLI-D-20-0166.1). Data provided by the NOAA PSL, Boulder, Colorado, USA, from their website at https://psl.noaa.gov
- **Air temperature**: ERA5 daily global mean 2 m temperature from Copernicus Climate Pulse (https://pulse.climate.copernicus.eu). Contains modified Copernicus Climate Change Service information [year of the data]. Neither the European Commission nor ECMWF is responsible for any use that may be made of the Copernicus information or data it contains. ERA5: Hersbach, H. et al. (2020), *Q. J. R. Meteorol. Soc.*, 146, 1999-2049, doi:10.1002/qj.3803. Copernicus data are free and open, with attribution as above. NCEP/NCAR Reanalysis 1 was considered but stopped on 2026-03-17.
- **ENSO and circulation indices**: NOAA Climate Prediction Center (https://www.cpc.ncep.noaa.gov). Indian Ocean Dipole definition: Saji, N. H. et al. (1999), *Nature* 401, 360-363. AMO definition: Trenberth, K. E. & Shea, D. J. (2006), *Geophys. Res. Lett.* 33, L12704. CPC's `oni.ascii.txt` now serves the ERSST v6 ONI.
- **Pre-industrial offset**: Copernicus Climate Change Service, Climate Bulletin, "About the data and analysis" (https://climate.copernicus.eu/climate-bulletin-about-data-and-analysis).
- **Greenhouse gas forcing**: expressions of NOAA's Annual Greenhouse Gas Index (https://gml.noaa.gov/aggi/; Forster et al. 2021, Smith et al. 2021).
- **Total solar irradiance**: Coddington, O., Lean, J., Lindholm, C., the NASA Solar Irradiance Science Team Program and the NOAA CDR Program (2024): NOAA Climate Data Record (CDR) of Total Solar Irradiance (TSI), Version 3.0. NOAA NCEI. https://doi.org/10.25921/k2ff-p712
- **PDO**: NOAA National Centers for Environmental Information (https://www.ncei.noaa.gov/access/monitoring/pdo/).

## License

The source values are redistributed under each provider's terms, which make them freely available and ask for the attribution above. The code and the derived regional means, anomalies and ranks are MIT-licensed. This project is not affiliated with NOAA, NSIDC, NCEP, ECMWF or Copernicus.
