"""
Build the modelling table.

    python build_dataset.py

Target: tn_peak_demand_mw - Tamil Nadu daily peak electricity demand, in MW.
Every feature is computable before the target day (zero data leakage).
"""

import numpy as np
import pandas as pd

from config import (CDD_BASE, CITIES, MASTER, PEAK_TARGET, RAW_DEMAND,
                    RAW_PEAK, RAW_WEATHER)

WEIGHTS = {c["name"]: c["weight"] for c in CITIES}
TARGET = PEAK_TARGET
ENERGY_COL = "tn_energy_gwh"


def state_weather(long: pd.DataFrame) -> pd.DataFrame:
    df = long.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["w"] = df["city"].map(WEIGHTS)
    if df["w"].isna().any():
        raise ValueError(f"unweighted cities: {df[df.w.isna()].city.unique().tolist()}")

    vals = [c for c in df.columns if c not in ("date", "city", "w", "issued_on")]

    def wavg(g):
        # renormalise per column, not once for the group: if a city reports
        # t_max but not rh_mean, dividing by the full weight sum would drag
        # that day's humidity toward zero instead of ignoring the gap
        out = {}
        for c in vals:
            v = g[c]
            w = g["w"].where(v.notna())
            total = w.sum()
            out[c] = float((v * w).sum() / total) if total > 0 else np.nan
        return pd.Series(out)

    out = df.groupby("date")[["w"] + vals].apply(wavg).reset_index()

    out["cdd"] = (out["t_mean"] - CDD_BASE).clip(lower=0)
    out["cdd_max"] = (out["t_max"] - CDD_BASE).clip(lower=0)
    out["cdd_apparent"] = (out["app_t_mean"] - CDD_BASE).clip(lower=0)
    out["thi"] = out["t_mean"] - (0.55 - 0.0055 * out["rh_mean"]) * (out["t_mean"] - 14.5)
    return out


def calendar_features(dates: pd.Series) -> pd.DataFrame:
    import holidays
    tn = holidays.India(subdiv="TN", years=sorted({d.year for d in dates}))
    hol = set(tn.keys())

    cal = pd.DataFrame({"date": dates})
    d = cal["date"]
    cal["dow"] = d.dt.dayofweek
    cal["is_weekend"] = (cal["dow"] >= 5).astype(int)
    cal["is_holiday"] = d.dt.date.map(lambda x: int(x in hol))
    cal["day_before_holiday"] = (d + pd.Timedelta(days=1)).dt.date.map(lambda x: int(x in hol))
    cal["day_after_holiday"] = (d - pd.Timedelta(days=1)).dt.date.map(lambda x: int(x in hol))
    cal["holiday_name"] = d.dt.date.map(lambda x: tn.get(x, ""))
    cal["pongal_window"] = ((d.dt.month == 1) & d.dt.day.between(13, 17)).astype(int)

    doy = d.dt.dayofyear
    for k in (1, 2):
        cal[f"sin{k}"] = np.sin(2 * np.pi * k * doy / 365.25)
        cal[f"cos{k}"] = np.cos(2 * np.pi * k * doy / 365.25)

    cal["trend"] = (d - d.min()).dt.days
    return cal


def add_lags(df: pd.DataFrame) -> pd.DataFrame:
    """Attach backward-looking features on a calendar-continuous index.

    Shifting positionally would be wrong: missing days in the series would cause
    positional lag_1 to quietly shift 2+ days ago. Reindexing onto a complete
    date range first guarantees every lag represents the exact calendar day it claims.
    Forward-filling (limit=2) handles isolated 1-2 day calendar blips cleanly.
    """
    span = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
    full = df.set_index("date").reindex(span)

    # 1. Target Autoregressive features (Peak Demand in MW)
    y = full[TARGET]
    for lag in (1, 2, 3, 7, 14, 364):
        full[f"peak_lag_{lag}"] = y.shift(lag).ffill(limit=2)
    for win, min_p in ((7, 3), (30, 14)):
        full[f"peak_roll_mean_{win}"] = y.shift(1).rolling(win, min_periods=min_p).mean()
        full[f"peak_roll_std_{win}"] = y.shift(1).rolling(win, min_periods=min_p).std().fillna(0.0)

    full["peak_lag_1_diff"] = full["peak_lag_1"] - full["peak_lag_2"]
    full["peak_lag_1_vs_week"] = full["peak_lag_1"] - full["peak_lag_7"]

    # 2. Daily Energy Consumption features (tn_energy_gwh)
    if ENERGY_COL in full.columns:
        e = full[ENERGY_COL]
        for lag in (1, 2, 7, 14):
            full[f"energy_lag_{lag}"] = e.shift(lag).ffill(limit=2)
        for win, min_p in ((7, 3), (30, 14)):
            full[f"energy_roll_mean_{win}"] = e.shift(1).rolling(win, min_periods=min_p).mean()
            full[f"energy_roll_std_{win}"] = e.shift(1).rolling(win, min_periods=min_p).std().fillna(0.0)

    # 3. Weather lags
    for col in ("cdd", "cdd_max", "thi"):
        if col in full.columns:
            full[f"{col}_lag1"] = full[col].shift(1).ffill(limit=2)

    # 4. Regional context lags
    for col in ("sr_max_demand_mw", "sr_wind_gwh", "sr_solar_gwh", "sr_peak_shortage_mw"):
        if col in full.columns:
            full[f"{col}_lag1"] = full[col].shift(1).ffill(limit=2)

    # Re-filter back to the observed rows
    return (full.loc[pd.DatetimeIndex(df["date"])]
                .rename_axis("date").reset_index())


def build() -> pd.DataFrame:
    # 1. Load peak demand
    peak = pd.read_csv(RAW_PEAK, parse_dates=["date"])
    peak_cols = [c for c in ["date", TARGET, "tn_shortage_at_peak_mw"] if c in peak.columns]
    peak = (peak[peak_cols]
            .dropna(subset=["date", TARGET])
            .drop_duplicates(subset="date", keep="last")
            .sort_values("date"))

    # 2. Load daily energy demand & weather
    demand = pd.read_csv(RAW_DEMAND, parse_dates=["date"])
    # Historically backfill regional metrics before tracking began:
    # Utility solar in Southern Region was negligible/zero prior to Sept 2016
    if "sr_solar_gwh" in demand.columns:
        demand["sr_solar_gwh"] = demand["sr_solar_gwh"].fillna(0.0)
    # Peak MW was recorded as Demand Met prior to April 2017
    if "sr_max_demand_mw" in demand.columns and "sr_demand_met_mw" in demand.columns:
        demand["sr_max_demand_mw"] = demand["sr_max_demand_mw"].fillna(demand["sr_demand_met_mw"])

    weather = state_weather(pd.read_csv(RAW_WEATHER))

    # 3. Merge peak demand, energy demand, weather, and calendar features
    df = (peak.merge(demand, on="date", how="inner")
              .merge(weather, on="date", how="inner")
              .sort_values("date"))
    df = df.merge(calendar_features(df["date"]), on="date", how="left")

    # Clean non-holiday string labels and default 0 MW shortage
    df["holiday_name"] = df["holiday_name"].fillna("None").replace("", "None")
    df["tn_shortage_at_peak_mw"] = df["tn_shortage_at_peak_mw"].fillna(0.0)

    df = add_lags(df).reset_index(drop=True)

    # 4. Drop leaky same-day regional columns (published day after target)
    leaky = [c for c in ("sr_max_demand_mw", "sr_demand_met_mw", "sr_peak_shortage_mw",
                         "sr_energy_gwh", "sr_wind_gwh", "sr_solar_gwh",
                         "india_energy_gwh", "kerala_energy_gwh",
                         "karnataka_energy_gwh", "ap_energy_gwh") if c in df.columns]
    df = df.drop(columns=leaky)

    # 5. Order columns cleanly: target first, followed by context, weather, calendar, and lags
    front = ["date", TARGET]
    for c in ["tn_shortage_at_peak_mw", ENERGY_COL]:
        if c in df.columns and c not in front:
            front.append(c)
    other = [c for c in df.columns if c not in front]
    df = df[front + other]

    df.to_csv(MASTER, index=False)
    return df


def report(df: pd.DataFrame):
    print(f"rows          {len(df):,}")
    print(f"date range    {df['date'].min():%Y-%m-%d} -> {df['date'].max():%Y-%m-%d}")
    print(f"features      {len(df.columns) - 2}")

    span = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
    gaps = span.difference(pd.DatetimeIndex(df["date"]))
    print(f"missing days  {len(gaps)}")

    y = df[TARGET]
    print(f"target MW     min {y.min():,.0f}  mean {y.mean():,.0f}  max {y.max():,.0f}")

    keys = [c for c in ("cdd", "cdd_max", "thi", "t_max", "peak_lag_1", "peak_lag_7",
                        "peak_roll_mean_7", "energy_lag_1", "trend") if c in df.columns]
    print("\ncorrelation with target:")
    print(df[[TARGET] + keys].corr()[TARGET].drop(TARGET).round(3).to_string())

    print("\nmean target by day type:")
    print(f"  working day  {y[(df.is_weekend == 0) & (df.is_holiday == 0)].mean():,.1f} MW")
    print(f"  weekend      {y[df.is_weekend == 1].mean():,.1f} MW")
    print(f"  holiday      {y[df.is_holiday == 1].mean():,.1f} MW")
    print(f"  Pongal week  {y[df.pongal_window == 1].mean():,.1f} MW")

    nulls = df.isna().sum()
    nulls = nulls[nulls > 0]
    if len(nulls):
        print("\nnulls (head-only, matching lag windows):")
        print(nulls.to_string())


if __name__ == "__main__":
    report(build())
    print(f"\nwrote {MASTER}")
