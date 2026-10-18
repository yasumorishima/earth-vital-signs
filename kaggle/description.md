The planet's headline climate indicators in tidy CSV, rebuilt every day by GitHub Actions from the original NOAA, NSIDC and Copernicus files: sea ice, CO2 and other greenhouse gases, sea surface temperature for 11 ocean regions (including the four Niño boxes and the Indian Ocean Dipole), global air temperature since 1940, ENSO and atmospheric circulation indices, and one monthly table that puts all of them side by side.

The popular Kaggle copies of these series stopped updating in 2017-2019. This one is regenerated daily, and a version is only published after automated checks pass.

Source code and the full history of every update: https://github.com/yasumorishima/earth-vital-signs

Notebooks: [a first look](https://www.kaggle.com/code/yasunorim/earth-vital-signs-a-first-look), plus [is today a climate record?](https://www.kaggle.com/code/yasunorim/earth-vital-signs-is-today-a-climate-record) and [will this year be the warmest?](https://www.kaggle.com/code/yasunorim/earth-vital-signs-will-this-year-be-the-warmest), which are re-run after every daily version.

## Files

| File | What | From | Rows |
|---|---|---|---|
| `sea_ice_extent_daily.csv` | Arctic, Antarctic and global sea ice extent, with anomaly, rank for the date, record-low flag and year-over-year change | 1978-10-26 | ~47,000 |
| `co2_daily_mauna_loa.csv` | Daily CO2 at Mauna Loa, with year-over-year change and record-high flag | 1974-05 | ~16,000 |
| `greenhouse_gases_monthly.csv` | Monthly CO2 (Mauna Loa, global), CH4, N2O, SF6, with annual growth | 1958 / 1979+ | ~2,600 |
| `sst_daily.csv` | Daily sea surface temperature for 11 regions with 1991-2020 anomalies, records, Indian Ocean Dipole index and relative Niño 3.4 | 1981-09-01 | ~16,500 |
| `air_temperature_daily.csv` | Daily global 2 m air temperature (ERA5) with the 1991-2020 anomaly, the anomaly above 1850-1900 (pre-industrial) as C3S computes it, days above 1.5 / 2.0 C, a 365-day mean, the year-to-date mean and its rank | 1940-01-01 | ~31,700 |
| `enso_monthly.csv` | Oceanic Niño Index (ONI) and Relative ONI (RONI), with El Niño / La Niña episodes by NOAA's rule | 1950 | ~920 |
| `atmosphere_indices_daily.csv` | Daily Arctic Oscillation, North Atlantic Oscillation, Pacific-North American and Antarctic Oscillation | 1950-01-01 | ~28,000 |
| `climate_drivers_monthly.csv` | Drivers of global temperature: greenhouse gas radiative forcing (CO2, CH4, N2O; NOAA AGGI method), total solar irradiance, Pacific Decadal Oscillation | 1854-01 | ~2,070 |
| `climate_monthly_panel.csv` | All of the above as monthly columns, one row per month: ready for correlation, regression and forecasting | 1950-01 | ~920 |

## What you can do with it

- Follow the 1.5 C question with C3S's own method: `global_2t_anom_preindustrial` adds C3S's day-of-year offset (0.79-0.97 C, not a flat 0.88) to the 1991-2020 anomaly; its yearly means reproduce C3S's published 1.48 C (2023), 1.60 C (2024) and 1.47 C (2025), and the build stops if they do not. A single day or year above 1.5 C is not a breach of the Paris Agreement, which refers to warming averaged over about 20 years.
- Race this year against the record: `global_ytd_anom_preindustrial` is the year-to-date mean and `global_ytd_rank_warmest_for_day` its rank at the same date in every year (also for 60S-60N SST).
- Group by ENSO phase: `oni_episode` / `roni_episode` label every season el_nino, la_nina or neutral by NOAA CPC's rule, and the build checks them against the colours of CPC's own tables every day. They are retrospective (a label uses later seasons), so do not use them as features known at the time.
- Separate the drivers: greenhouse gas forcing (annual means reproduce NOAA's AGGI table), the Sun and the PDO are in `climate_drivers_monthly.csv`, and the Atlantic Multidecadal Oscillation as revised by Trenberth & Shea (2006) is in `sst_daily.csv`.
- See whether today is a record: sea ice (each hemisphere and global), the 60S-60N and global SST, and global air temperature have the rank of each day's value among all years for the same calendar day, and a record flag (CO2 has a record-high flag). The rank counts only the years up to the row's own, so later years never change it (only a recent day can move, while the source may still revise its value) and it can be used as a model target without look-ahead.
- Track El Niño day by day: Niño 3.4 SST is daily here, while NOAA publishes it weekly and the ONI monthly.
- Study links between the ocean, the atmosphere and the ice: the monthly panel lines up ENSO, the Indian Ocean Dipole, the Arctic Oscillation, sea ice, air and sea temperature and greenhouse gases.

## How each update is checked

- Dates are unique; the daily SST and air temperature series have no missing days, and every region is defined every day.
- Values are inside a plausible physical range; the latest date is recent (a source that silently stopped is flagged).
- Every published row older than its revision window is still present and unchanged. Sources revise recent values (preliminary SST for about two weeks, CO2 after calibration), so only older rows are frozen. NOAA GML says its greenhouse gas values may be revised slightly after quality control, so in that table each update may move an older value by at most 0.2 ppm (CO2), 0.5 ppb (CH4), 0.05 ppb (N2O) or 0.03 ppt (SF6). NOAA CPC re-normalizes its indices, so for those only the rows are guaranteed, not the values.
- SST means are computed here from the full 0.25-degree OISST v2.1 grid. Each run recomputes one finalized day from NCEI's separate per-day file (all 11 regions within 0.005 C), compares the Niño-box 7-day means with NOAA CPC's weekly Niño SST over the last year, and compares the 60S-60N anomaly with ERA5's independent SST.

## Sources and citation

The data are produced by the organizations below, which ask to be cited. Please cite them, not this dataset, in any publication.

- Sea ice: Fetterer, F., Knowles, K., Meier, W. N., Savoie, M., Windnagel, A. K. & Stafford, T. (2025). Sea Ice Index (G02135, Version 4). Boulder, Colorado USA. NSIDC: National Snow and Ice Data Center. https://doi.org/10.7265/a98x-0f50
- Mauna Loa CO2: Dr. Xin Lan, NOAA/GML (gml.noaa.gov/ccgg/trends/) and Dr. Ralph Keeling, Scripps Institution of Oceanography (scrippsco2.ucsd.edu/). Monthly values before 1974-05 are Scripps measurements.
- Global greenhouse gases: Lan, X., Tans, P. and K.W. Thoning: Trends in globally-averaged CO2 determined from NOAA Global Monitoring Laboratory measurements. https://doi.org/10.15138/9N0H-ZH07 (and the corresponding CH4, N2O and SF6 pages at https://gml.noaa.gov/ccgg/trends/)
- Sea surface temperature: NOAA OISST v2.1 (Huang, B. et al. 2021, J. Climate, doi:10.1175/JCLI-D-20-0166.1). Data provided by the NOAA PSL, Boulder, Colorado, USA, from their website at https://psl.noaa.gov
- Air temperature: ERA5 daily global mean 2 m temperature from Copernicus Climate Pulse (https://pulse.climate.copernicus.eu). Contains modified Copernicus Climate Change Service information {YEAR}. Neither the European Commission nor ECMWF is responsible for any use that may be made of the Copernicus information or data it contains. ERA5: Hersbach, H. et al. (2020), Q. J. R. Meteorol. Soc., 146, 1999-2049, doi:10.1002/qj.3803. Copernicus data are free and open, with attribution as above.
- ENSO and circulation indices: NOAA Climate Prediction Center (https://www.cpc.ncep.noaa.gov). Indian Ocean Dipole definition: Saji, N. H. et al. (1999), Nature 401, 360-363. AMO definition: Trenberth, K. E. & Shea, D. J. (2006), Geophys. Res. Lett. 33, L12704.
- Pre-industrial offset: Copernicus Climate Change Service, Climate Bulletin "About the data and analysis" (https://climate.copernicus.eu/climate-bulletin-about-data-and-analysis).
- Greenhouse gas forcing: expressions of NOAA's Annual Greenhouse Gas Index (https://gml.noaa.gov/aggi/; Forster et al. 2021, Smith et al. 2021).
- Total solar irradiance: Coddington, O., Lean, J., Lindholm, C., the NASA Solar Irradiance Science Team Program and the NOAA CDR Program (2024): NOAA Climate Data Record (CDR) of Total Solar Irradiance (TSI), Version 3.0. NOAA National Centers for Environmental Information. https://doi.org/10.25921/k2ff-p712
- PDO: NOAA National Centers for Environmental Information (https://www.ncei.noaa.gov/access/monitoring/pdo/).

## License

The values are redistributed under the terms of each source, which make them freely available and ask for the attribution above. The regional means, anomalies, ranks and the build code are MIT-licensed (see the GitHub repository).

## Notes

- Air temperature is ERA5 reanalysis, a model-based estimate; its absolute level differs between reanalyses, so compare anomalies rather than absolute values with other sources. The most recent days are marked preliminary and can change slightly. (NCEP/NCAR Reanalysis 1, used by many older datasets, stopped on 2026-03-17.)
- ERA5's own 60S-60N SST is compared with the OISST-based `world_60s_60n` anomaly on every run as an independent check.
- Monthly global gas averages lag by a few months, as published by NOAA GML. CPC's teleconnection files are updated with a few days' lag. The solar irradiance record is released about once a year, so its latest months are empty until the next release.
- CPC's `oni.ascii.txt` now serves the ERSST v6 ONI (DJF 1950: -1.32; the older v5 table has -1.5).
- Not included on purpose: sunspot numbers (SILSO's licence is non-commercial), NOAA PSL's AMO file (stopped in 2023), and further rolling windows or lag columns (one line of pandas, and any choice of window would be arbitrary; the 365-day mean is kept because it is the 12-month figure C3S reports).
- This dataset is not affiliated with NOAA, NSIDC, NCEP, ECMWF or Copernicus.
