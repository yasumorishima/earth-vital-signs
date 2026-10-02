# Earth Vital Signs

Four headline climate indicators as tidy CSV, rebuilt every day by GitHub Actions from the original NOAA and NSIDC files.

| File | What | From |
|---|---|---|
| [`data/sea_ice_extent_daily.csv`](data/sea_ice_extent_daily.csv) | Arctic and Antarctic sea ice extent | 1978-10-26 |
| [`data/co2_daily_mauna_loa.csv`](data/co2_daily_mauna_loa.csv) | Daily CO2 at Mauna Loa | 1974-05 |
| [`data/greenhouse_gases_monthly.csv`](data/greenhouse_gases_monthly.csv) | Monthly CO2 (Mauna Loa, global), CH4, N2O, SF6 | 1958 / 1979+ |
| [`data/sst_daily.csv`](data/sst_daily.csv) | Daily mean sea surface temperature, 60S-60N and North Atlantic, with 1991-2020 anomalies | 1981-09-01 |

The latest dates are in [`data/summary.json`](data/summary.json). The same files are published on Kaggle as **Earth Vital Signs - Daily Climate Indicators**.

## Why another copy

The most-used Kaggle copies of these series stopped updating in 2017-2019. This one is regenerated daily and only changes after checks pass.

## How each update is checked

`scripts/build.py` fetches every source, then runs gates before writing anything. One failure stops the whole update and leaves `data/` as it was.

- Dates are unique, and the daily SST series has no missing days.
- Values are inside a plausible physical range.
- The latest date is recent, so a source that silently stopped is caught.
- Every row already published that is older than the revision window is still present and unchanged. Sources revise recent values (preliminary SST for about two weeks, CO2 after calibration), so only older rows are frozen. If a source reprocesses its history, the update stops for a human to look.
- SST means are computed here from the full 0.25-degree OISST v2.1 grid (cos-latitude weights, ocean cells only). Each run recomputes one finalized day from NCEI's separate per-day file and requires the same means to within 0.005 C.

## Sources and citation

Please cite the producers, not this repository.

- **Sea ice**: Fetterer, F., Knowles, K., Meier, W. N., Savoie, M., Windnagel, A. K. & Stafford, T. (2025). *Sea Ice Index* (G02135, Version 4). Boulder, Colorado USA. NSIDC: National Snow and Ice Data Center. https://doi.org/10.7265/a98x-0f50
- **Mauna Loa CO2**: Dr. Xin Lan, NOAA/GML (gml.noaa.gov/ccgg/trends/) and Dr. Ralph Keeling, Scripps Institution of Oceanography (scrippsco2.ucsd.edu/).
- **Global greenhouse gases**: Lan, X., Tans, P. and K.W. Thoning: Trends in globally-averaged CO2 determined from NOAA Global Monitoring Laboratory measurements. https://doi.org/10.15138/9N0H-ZH07, and the CH4, N2O and SF6 pages at https://gml.noaa.gov/ccgg/trends/
- **Sea surface temperature**: NOAA OISST v2.1 (Huang, B. et al. 2021, *J. Climate*, doi:10.1175/JCLI-D-20-0166.1). Data provided by the NOAA PSL, Boulder, Colorado, USA, from their website at https://psl.noaa.gov

## License

The source values are redistributed under each provider's terms, which make them freely available and ask for the attribution above. The code and the derived SST means are MIT-licensed. This project is not affiliated with NOAA or NSIDC.
