"""
Backfill missing historical peak demand from Grid-India Daily PSP Reports.

Fills gaps in data/peak_demand.csv (e.g. May 2024 to September 2026).
Saves progress incrementally so it can be resumed safely at any time.

Usage:
    python backfill_peak_demand.py
    python backfill_peak_demand.py --start 2024-05-02 --end 2026-09-18
"""

import argparse
import os
import shutil
import sys
import time
from datetime import date, datetime, timedelta

import pandas as pd
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import RAW_PEAK
from fetch_peak_demand import fetch_peak_demand_for_date


def get_missing_dates(start_date: date, end_date: date) -> list[date]:
    """Identify dates in [start_date, end_date] missing from peak_demand.csv."""
    if not RAW_PEAK.exists():
        all_days = pd.date_range(start_date, end_date, freq="D")
        return [d.date() for d in all_days]

    df = pd.read_csv(RAW_PEAK, parse_dates=["date"])
    valid = df.dropna(subset=["date", "tn_peak_demand_mw"])
    existing_dates = set(pd.to_datetime(valid["date"]).dt.date)

    all_days = pd.date_range(start_date, end_date, freq="D")
    missing = [d.date() for d in all_days if d.date() not in existing_dates]
    return missing


def save_batch(new_records: list[dict]):
    """Upsert new records into data/peak_demand.csv."""
    if not new_records:
        return

    if RAW_PEAK.exists():
        df = pd.read_csv(RAW_PEAK, parse_dates=["date"])
    else:
        df = pd.DataFrame(columns=[
            "date", "tn_peak_demand_mw", "tn_shortage_at_peak_mw", "tn_energy_met_mu",
            "report_published", "source", "source_file", "source_url", "retrieved_at"
        ])

    new_df = pd.DataFrame(new_records)
    new_df["date"] = pd.to_datetime(new_df["date"])

    combined = (pd.concat([df, new_df], ignore_index=True)
                  .dropna(subset=["date"])
                  .drop_duplicates(subset=["date"], keep="last")
                  .sort_values("date")
                  .reset_index(drop=True))

    combined.to_csv(RAW_PEAK, index=False)


def run_backfill(start_date: date, end_date: date, batch_save_interval: int = 10):
    missing = get_missing_dates(start_date, end_date)
    total = len(missing)

    print(f"==================================================")
    print(f"Grid-India Peak Demand Backfill")
    print(f"Date range: {start_date} to {end_date}")
    print(f"Missing dates to fetch: {total}")
    print(f"==================================================")

    if total == 0:
        print("✅ No missing dates found. Everything is up to date!")
        return

    cache = {}
    new_records = []
    success_count = 0
    fail_count = 0
    start_time = time.time()

    for idx, target_date in enumerate(missing, 1):
        elapsed = time.time() - start_time
        rate = idx / elapsed if elapsed > 0 else 0
        eta_sec = (total - idx) / rate if rate > 0 else 0

        print(f"[{idx}/{total}] Fetching {target_date} "
              f"(Success: {success_count}, Failed: {fail_count}, ETA: {int(eta_sec // 60)}m {int(eta_sec % 60)}s)...",
              end=" ", flush=True)

        try:
            res = fetch_peak_demand_for_date(target_date, cache)
            if res.get("status") == "ok":
                new_records.append(res)
                success_count += 1
                print(f"✓ {res['tn_peak_demand_mw']:.0f} MW")
            else:
                fail_count += 1
                print(f"⚠️ {res.get('status')}")
        except Exception as e:
            fail_count += 1
            print(f"❌ Error: {e}")

        # Incremental save
        if len(new_records) >= batch_save_interval:
            save_batch(new_records)
            new_records = []

        # Polite delay between requests to avoid Grid-India throttling
        time.sleep(0.3)

    # Save any remaining records
    if new_records:
        save_batch(new_records)

    # Clean downloads folder to save disk space
    if os.path.exists("downloads"):
        shutil.rmtree("downloads", ignore_errors=True)

    print("\n==================================================")
    print(f"Backfill finished in {int((time.time() - start_time) // 60)}m {int((time.time() - start_time) % 60)}s")
    print(f"Successfully added: {success_count}")
    print(f"Failed/Missing upstream: {fail_count}")
    print("==================================================")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill missing peak demand data from Grid-India.")
    parser.add_argument("--start", type=str, default="2024-05-02", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2026-09-18", help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    s_date = date.fromisoformat(args.start)
    e_date = date.fromisoformat(args.end)

    run_backfill(s_date, e_date)
