"""
CWB typhoon circuit breaker (PRD 2.5).
Checks CWA typhoon warnings; if any county has a land typhoon warning,
marks outdoor market events there as 'likely_canceled'.

Run:  python cwa_breaker.py [--dry-run]
Env:  CWA_API_KEY + Supabase keys
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
os.environ.setdefault("NEXT_PUBLIC_SUPABASE_URL", os.environ.get("SUPABASE_URL", ""))

API = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0034-001"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# county name -> keyword used to match event location text is not available in
# our schema (events only have lat/lng), so we map counties to bbox rectangles.
COUNTY_BBOXES = {
    "臺北市": (121.46, 24.98, 121.62, 25.22),
    "新北市": (121.28, 24.38, 122.01, 25.31),
    "桃園市": (120.98, 24.60, 121.42, 25.14),
    "臺中市": (120.52, 23.86, 121.29, 24.46),
    "臺南市": (120.00, 22.88, 120.66, 23.43),
    "高雄市": (120.24, 22.40, 121.05, 23.55),
    "基隆市": (121.65, 25.10, 121.79, 25.34),
    "新竹市": (120.90, 24.74, 120.99, 24.83),
    "新竹縣": (120.92, 24.41, 121.39, 25.02),
    "苗栗縣": (120.72, 24.26, 121.37, 24.82),
    "彰化縣": (120.30, 23.70, 120.75, 24.13),
    "南投縣": (120.63, 23.48, 121.44, 24.24),
    "雲林縣": (120.09, 23.49, 120.68, 23.87),
    "嘉義市": (120.40, 23.42, 120.50, 23.53),
    "嘉義縣": (120.06, 23.15, 120.69, 23.71),
    "屏東縣": (119.51, 21.89, 120.94, 22.68),
    "宜蘭縣": (121.45, 24.08, 122.04, 25.02),
    "花蓮縣": (120.91, 23.16, 121.95, 24.61),
    "臺東縣": (120.44, 21.89, 121.50, 23.35),
    "澎湖縣": (119.32, 23.17, 119.77, 23.76),
    "連江縣": (119.90, 25.93, 120.33, 26.24),
    "金門縣": (118.11, 24.19, 118.54, 24.56),
}


def get_warning_counties(api_key: str) -> list[str]:
    """Returns list of counties under land typhoon warning."""
    r = httpx.get(API, params={"Authorization": api_key}, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    data = r.json()

    counties: list[str] = []
    records = data.get("records", {})
    # W-C0034-001 structure: records.typhoonMessages[].typhoonWarnings[].warningInfo[].affectedAreas
    for msg in records.get("typhoonMessages", []):
        for warning in msg.get("typhoonWarnings", []):
            for info in warning.get("warningInfo", []):
                areas = info.get("affectedAreas") or []
                for area in areas:
                    if isinstance(area, str):
                        counties.append(area.replace("臺灣省", "").strip())
    return sorted(set(c for c in counties if c))


def mark_canceled(counties: list[str], dry_run: bool) -> int:
    from supabase import create_client
    svc = create_client(os.environ["NEXT_PUBLIC_SUPABASE_URL"],
                        os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    total = 0
    for county in counties:
        bbox = COUNTY_BBOXES.get(county)
        if not bbox:
            continue
        min_lng, min_lat, max_lng, max_lat = bbox
        evs = svc.rpc("get_events_in_bbox", {
            "min_lng": min_lng, "min_lat": min_lat,
            "max_lng": max_lng, "max_lat": max_lat,
        }).execute().data
        targets = [e["id"] for e in evs if e["category"] == "market"]
        if dry_run:
            print(f"  [dry-run] {county}: would cancel {len(targets)} market events")
            total += len(targets)
            continue
        for eid in targets:
            svc.table("events").update({"status": "likely_canceled"}).eq("id", eid).execute()
        print(f"  [canceled] {county}: {len(targets)} market events marked likely_canceled")
        total += len(targets)
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    key = os.environ.get("CWA_API_KEY")
    if not key:
        print("CWA_API_KEY not set — get one free at https://opendata.cwa.gov.tw/")
        sys.exit(1)

    print("Checking CWA typhoon warnings...")
    try:
        counties = get_warning_counties(key)
    except httpx.HTTPStatusError as e:
        print(f"CWA API error: {e}")
        sys.exit(1)

    if not counties:
        print("No land typhoon warnings active. All clear. ✅")
        return

    print(f"⚠️ Land typhoon warning active for: {', '.join(counties)}")
    n = mark_canceled(counties, args.dry_run)
    print(f"DONE: {n} events flagged likely_canceled")


if __name__ == "__main__":
    main()
