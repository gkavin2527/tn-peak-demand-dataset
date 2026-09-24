# Tamil Nadu Daily Peak Electricity Demand Dataset

A daily-updating dataset of Tamil Nadu's peak electricity demand (MW), joined with
energy consumption, weather features, and calendar indicators. Built for next-day peak demand forecasting.

Updates automatically every morning at 07:00 IST via GitHub Actions.

---

## The dataset

**`data/master.csv`** — one row per day, ready to model.

| | |
|---|---|
| Rows | 4,848+ |
| Coverage | 2013-01-02 → yesterday (~14 continuous years) |
| Columns | 52 |
| Primary Target | `tn_peak_demand_mw` (Tamil Nadu Peak Demand in MW) |
| Supporting Context | `tn_shortage_at_peak_mw`, `tn_energy_gwh` |
| Freshness | ~1–2 days behind real time |

```python
import pandas as pd
df = pd.read_csv("data/master.csv", parse_dates=["date"])
```

---

## Columns

### Primary Target & Context

| Column | Description |
|---|---|
| `tn_peak_demand_mw` | **Primary Target:** Tamil Nadu daily peak electricity demand, in MW |
| `tn_shortage_at_peak_mw` | Power deficit/shortage during peak demand hour, in MW |
| `tn_energy_gwh` | Total daily energy met in Tamil Nadu, in GWh |

### Weather

Five cities (Chennai, Coimbatore, Madurai, Trichy, Salem) collapsed into one
state series using load-share weights, representing both the interior and coastal regions.

| Column | Description |
|---|---|
| `t_mean`, `t_max`, `t_min` | Temperature, °C |
| `rh_mean` | Relative humidity, % |
| `app_t_mean`, `app_t_max` | Apparent ("feels like") temperature, °C |
| `hours_above_32` | Hours above 32 °C — duration of extreme heat driving daytime and evening cooling demand |
| `cdd` | Cooling degree days, `max(0, t_mean − 25)` |
| `cdd_max` | Cooling degree days on daily max temperature |
| `cdd_apparent` | Cooling degree days on apparent temperature |
| `thi` | Temperature-humidity index |

### Calendar

| Column | Description |
|---|---|
| `dow`, `is_weekend` | Day of week (0 = Monday), weekend flag |
| `is_holiday`, `holiday_name` | Tamil Nadu holidays, including Pongal, Mattu Pongal, Uzhavar Thirunal, and Puthandu |
| `day_before_holiday`, `day_after_holiday` | Shoulder days behaving differently from regular days |
| `pongal_window` | 13–17 January — multi-day industrial shutdown during festival |
| `sin1`, `cos1`, `sin2`, `cos2` | Fourier terms for smooth annual seasonality |
| `trend` | Days since series start, capturing long-term structural demand growth |

### Lagged & Rolling Features (Zero Leakage)

All features are backward-looking and **date-aware**, computed on a continuous daily index:

| Column | Description |
|---|---|
| `peak_lag_1` … `peak_lag_364` | Tamil Nadu peak demand 1, 2, 3, 7, 14, and 364 days back (MW) |
| `peak_roll_mean_7`, `peak_roll_mean_30` | 7-day and 30-day moving average of peak demand |
| `peak_roll_std_7`, `peak_roll_std_30` | 7-day and 30-day moving standard deviation (volatility) of peak demand |
| `peak_lag_1_diff` | Day-over-day peak change (`peak_lag_1 − peak_lag_2`) |
| `peak_lag_1_vs_week` | Week-over-week peak change (`peak_lag_1 − peak_lag_7`) |
| `energy_lag_1`, `energy_lag_7`, etc. | Daily energy consumption lags (GWh) |
| `energy_roll_mean_7`, `energy_roll_mean_30` | Rolling averages of daily energy consumption (GWh) |
| `cdd_lag1`, `cdd_max_lag1`, `thi_lag1` | Yesterday's heat load |
| `sr_*_lag1` | Southern Region grid context (max demand, wind generation, solar generation, peak shortage) |

---

## No Leakage

Every feature on day $T$ is computable **before the target day begins**:
- Peak and energy lags start at $T-1$ (`peak_lag_1`, `energy_lag_1`).
- Rolling windows use `shift(1)` before computing rolling statistics.
- Same-day regional context is dropped; only `_lag1` regional metrics are retained.

---

## Sources

- **Peak Demand:** Grid Controller of India (Grid-India, formerly POSOCO) Daily PSP Reports.
- **Daily Energy:** Grid-India daily PSP reports via Robbie Andrew (CICERO).
- **Weather:** Open-Meteo historical reanalysis and forecast APIs.
- **Holidays:** Python `holidays` package (`subdiv="TN"`).

---

## Rebuilding & Automation

Automated refresh runs every morning at **07:00 IST** via GitHub Actions:

```bash
python fetch_weather.py forecast
python fetch_demand.py
python update_peak_demand.py
python fetch_weather.py update
python build_dataset.py
python health_check.py
```

### Files

| File | Purpose |
|---|---|
| `config.py` | Cities, weights, column mapping, paths |
| `fetch_demand.py` | Downloads and slices the demand data |
| `fetch_weather.py` | Weather history, daily updates, forecast log |
| `build_dataset.py` | Merge and feature engineering |
| `health_check.py` | Validation, exits non-zero on failure |
| `.github/workflows/daily.yml` | Daily automation |

---

## Automation

Runs at 07:00 IST daily. Fetches new data, rebuilds `master.csv`, runs the
health check, commits. Pull with `git pull`.

`health_check.py` fails the run if the weather falls more than 3 days behind,
`master.csv` drops below 3,000 rows or lags the demand slice it is built from,
the target goes constant or leaves a plausible range, or the weather–demand
correlation collapses below 0.2 — the signature of a misaligned join.

Demand staleness is judged against the source rather than the calendar. Past 5
days behind, the check compares our slice to the raw upstream download from the
same run:

| | |
|---|---|
| Upstream has rows we don't | **fail** — published data isn't landing, so the slice or the commit is broken |
| Upstream is level with us | **warn** — Grid India slips over weekends and national holidays, and the rows arrive when it catches up |
| Level with us for over 14 days | **fail** — too long to still be "running late"; assume the source has moved or died |

A third party's publishing schedule shouldn't turn the run red every morning —
that only teaches you to ignore a red run. What should turn it red is data we
could have collected and didn't.

Run it locally any time:

```bash
python health_check.py
```

GitHub disables scheduled workflows after 60 days of repository inactivity. If
updates stop, check that first.

---

## Known quirks

**13 missing days.** Upstream reports were unavailable or unreadable. Not
imputed — the gaps are left visible, which is the honest representation.

**November–December 2015 is anomalously low** (~224 GWh against a ~314 series
mean). This is the Chennai floods, a genuine event rather than bad data.

**April–May 2020 is depressed** by the COVID-19 lockdown (~243 GWh mean in
April 2020). Also real. Both periods are regime breaks worth handling
explicitly if you model across them.

**`sr_*_lag1` columns are sparse before 2022.** Drop them if you want complete
cases reaching further back.
