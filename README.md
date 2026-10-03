# Earth Vital Signs

The planet's headline climate indicators as tidy CSV, rebuilt every day by GitHub Actions from the original NOAA, NSIDC and Copernicus files.

| File | What | From |
|---|---|---|
| [`data/sea_ice_extent_daily.csv`](data/sea_ice_extent_daily.csv) | Arctic, Antarctic and global sea ice extent, with anomaly, rank for the date, record-low flag, year-over-year change | 1978-10-26 |
| [`data/co2_daily_mauna_loa.csv`](data/co2_daily_mauna_loa.csv) | Daily CO2 at Mauna Loa, with year-over-year change and record-high flag | 1974-05 |
| [`data/greenhouse_gases_monthly.csv`](data/greenhouse_gases_monthly.csv) | Monthly CO2 (Mauna Loa, global), CH4, N2O, SF6, with annual growth | 1958 / 1979+ |
| [`data/sst_daily.csv`](data/sst_daily.csv) | Daily sea surface temperature for 11 regions (incl. Niño 1+2, 3, 3.4, 4 and the Indian Ocean Dipole poles), 1991-2020 anomalies, records, DMI, relative Niño 3.4 | 1981-09-01 |
| [`data/air_temperature_daily.csv`](data/air_temperature_daily.csv) | Daily global 2 m air temperature (ERA5, via Copernicus Climate Pulse) with the 1991-2020 anomaly, rank for the date and record flag | 1940-01-01 |
| [`data/enso_monthly.csv`](data/enso_monthly.csv) | Oceanic Niño Index (ONI) and Relative ONI (RONI) from NOAA CPC | 1950 |
| [`data/atmosphere_indices_daily.csv`](data/atmosphere_indices_daily.csv) | Daily AO, NAO, PNA and AAO from NOAA CPC | 1950-01-01 |
| [`data/climate_monthly_panel.csv`](data/climate_monthly_panel.csv) | Everything above as monthly columns, one row per month | 1950-01 |

Column-by-column descriptions are in [`kaggle/settings.json`](kaggle/settings.json) (generated with the Kaggle metadata by `scripts/make_kaggle_meta.py`, which fails if a description and the data disagree). The latest dates are in [`data/summary.json`](data/summary.json). A starter notebook is in [`notebooks/`](notebooks/earth-vital-signs-starter.ipynb). The same files are published on Kaggle as [**Earth Vital Signs - Daily Climate Indicators**](https://www.kaggle.com/datasets/yasunorim/earth-vital-signs-daily), with a new version each day the data changes, and the starter notebook runs there as [Earth Vital Signs: a first look](https://www.kaggle.com/code/yasunorim/earth-vital-signs-a-first-look). A second notebook, [Is today a climate record?](https://www.kaggle.com/code/yasunorim/earth-vital-signs-is-today-a-climate-record) ([source](notebooks/todays-records/todays-records.ipynb)), ranks the latest day of each series against the same date in every earlier year and is re-run on Kaggle after each daily version.

## Why another copy

The most-used Kaggle copies of these series stopped updating in 2017-2019. This one is regenerated daily, adds what the sources do not publish (daily Niño-box and regional SST, ranks and records for each calendar day, a monthly panel of all indicators), and only changes after checks pass.

Ranks and record flags compare a value with the same calendar day of its own year and earlier years only, so a published row never changes later and the columns can be used as targets without look-ahead.

## How each update is checked

`scripts/build.py` fetches every source, then runs gates before writing anything. One failure stops the whole update and leaves `data/` as it was.

- Dates are unique; the daily SST and air temperature series have no missing days and every region is defined every day.
- Values are inside a plausible physical range.
- The latest date is recent, so a source that silently stopped is flagged (as a warning, recorded in `summary.json` under `stale_sources`).
- Every row already published that is older than the revision window is still present and unchanged, in every published column. New columns may be added; none may be dropped. Sources revise recent values (preliminary SST for about two weeks, CO2 after calibration), so only older rows are frozen. If a source reprocesses its history, the update stops for a human to look. NOAA CPC re-normalizes its indices, so for those tables only the rows are guaranteed.
- SST means are computed here from the full 0.25-degree OISST v2.1 grid (cos-latitude weights, ocean cells only). Each run recomputes one finalized day from NCEI's separate per-day file and requires the same means for all 11 regions to within 0.005 C, compares the 7-day means of the four Niño boxes with NOAA CPC's weekly Niño SST over the last year, and compares the 60S-60N anomaly with ERA5's independent SST (Copernicus Climate Pulse).
- `scripts/test_get.py` runs first and checks that the downloader fails closed on a 404, a server that ignores HTTP Range, and a file replaced mid-download, and resumes dropped connections to the exact bytes.

## Sources and citation

Please cite the producers, not this repository.

- **Sea ice**: Fetterer, F., Knowles, K., Meier, W. N., Savoie, M., Windnagel, A. K. & Stafford, T. (2025). *Sea Ice Index* (G02135, Version 4). Boulder, Colorado USA. NSIDC: National Snow and Ice Data Center. https://doi.org/10.7265/a98x-0f50
- **Mauna Loa CO2**: Dr. Xin Lan, NOAA/GML (gml.noaa.gov/ccgg/trends/) and Dr. Ralph Keeling, Scripps Institution of Oceanography (scrippsco2.ucsd.edu/).
- **Global greenhouse gases**: Lan, X., Tans, P. and K.W. Thoning: Trends in globally-averaged CO2 determined from NOAA Global Monitoring Laboratory measurements. https://doi.org/10.15138/9N0H-ZH07, and the CH4, N2O and SF6 pages at https://gml.noaa.gov/ccgg/trends/
- **Sea surface temperature**: NOAA OISST v2.1 (Huang, B. et al. 2021, *J. Climate*, doi:10.1175/JCLI-D-20-0166.1). Data provided by the NOAA PSL, Boulder, Colorado, USA, from their website at https://psl.noaa.gov
- **Air temperature**: ERA5 daily global mean 2 m temperature from Copernicus Climate Pulse (https://pulse.climate.copernicus.eu). Contains modified Copernicus Climate Change Service information [year of the data]. Neither the European Commission nor ECMWF is responsible for any use that may be made of the Copernicus information or data it contains. ERA5: Hersbach, H. et al. (2020), *Q. J. R. Meteorol. Soc.*, 146, 1999-2049, doi:10.1002/qj.3803. Copernicus data are free and open, with attribution as above. NCEP/NCAR Reanalysis 1 was considered but stopped on 2026-03-17.
- **ENSO and circulation indices**: NOAA Climate Prediction Center (https://www.cpc.ncep.noaa.gov). Indian Ocean Dipole definition: Saji, N. H. et al. (1999), *Nature* 401, 360-363.

## License

The source values are redistributed under each provider's terms, which make them freely available and ask for the attribution above. The code and the derived regional means, anomalies and ranks are MIT-licensed. This project is not affiliated with NOAA, NSIDC, NCEP, ECMWF or Copernicus.
