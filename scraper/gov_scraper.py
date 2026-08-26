"""
Tier 1 government open data scraper — 文化部展覽資訊 (data.gov.tw #6012).
Structured JSON with lat/lng included: NO LLM needed, zero AI cost.

Run:  python gov_scraper.py [--dry-run]
Env:  NEXT_PUBLIC_SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv(Path(__file__).resolve().parent.parent / "web" / ".env.local")
os.environ.setdefault("NEXT_PUBLIC_SUPABASE_URL", os.environ.get("SUPABASE_URL", ""))

API_URL = "https://cloud.culture.tw/frontsite/trans/SearchShowAction.do?method=doFindTypeJ&category=6"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


def parse_dt(s: str) -> datetime | None:
    """'2026/08/27 09:00:00' -> aware datetime."""
    try:
        return datetime.strptime(s.strip(), "%Y/%m/%d %H:%M:%S").astimezone()
    except Exception:
        return None


def fetch_events(limit: int) -> list[dict]:
    r = httpx.get(API_URL, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    raw = r.json()

    now = datetime.now().astimezone()
    out = []
    for item in raw:
        for info in item.get("showInfo", []):
            try:
                lat = float(info["latitude"])
                lng = float(info["longitude"])
            except (KeyError, ValueError, TypeError):
                continue  # no coords -> not mappable
            if not (21.5 <= lat <= 25.5 and 119.5 <= lng <= 122.5):
                continue  # outside Taiwan bounds
            start = parse_dt(info.get("time", ""))
            end = parse_dt(info.get("endTime", ""))
            if not start or not end or end <= now:
                continue  # expired

            desc = TAG_RE.sub(" ", item.get("descriptionFilterHtml", "") or "")
            desc = WHITESPACE_RE.sub(" ", desc).strip()[:500]
            price = (info.get("price") or "").strip()[:200]

            summary = desc or f"主辦：{item.get('showUnit', '')}"
            if price:
                summary += f"｜票價：{price}"

            # Prefer the source site's own link (sourceWebPromote); the UID-based
            # event.culture.tw path is not a valid public page format.
            url = (item.get("sourceWebPromote") or "").strip()
            if not re.match(r"^https?://", url):
                # fall back to the iCulture search page for this title
                from urllib.parse import quote
                url = f"https://cloud.culture.tw/frontsite/trans/SearchShowAction.do?method=doFindByTypeJ&keyword={quote(item.get('title', ''))}"

            out.append({
                "title": (item.get("title") or "").strip()[:100],
                "summary": summary,
                "category": "exhibition",
                "geom": f"SRID=4326;POINT({lng} {lat})",
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "is_verified": True,
                "source_url": url,
            })
            if len(out) >= limit:
                return out
    return out


def upsert(events: list[dict], dry_run: bool) -> int:
    if dry_run:
        for ev in events:
            print(f"  [dry-run] {ev['title'][:45]} | {ev['geom'].split('(')[1][:20]}")
        return len(events)

    from supabase import create_client
    svc = create_client(os.environ["NEXT_PUBLIC_SUPABASE_URL"],
                        os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    inserted = skipped = 0
    for ev in events:
        lng = float(ev["geom"].split("(")[1].split()[0])
        lat = float(ev["geom"].split("(")[1].split()[1].rstrip(")"))
        near = svc.rpc("get_events_in_bbox", {
            "min_lng": lng - 0.0006, "min_lat": lat - 0.0005,
            "max_lng": lng + 0.0006, "max_lat": lat + 0.0005,
        }).execute().data
        if any(n["title"].strip() == ev["title"].strip() for n in near):
            skipped += 1
            continue
        svc.table("events").insert(ev).execute()
        inserted += 1
        print(f"  [inserted] {ev['title'][:50]}")
    print(f"  done: {inserted} inserted, {skipped} duplicates skipped")
    return inserted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"Fetching 文化部 exhibition feed (limit={args.limit})...")
    events = fetch_events(args.limit)
    print(f"Got {len(events)} active Taiwan exhibitions with coordinates")

    n = upsert(events, args.dry_run)
    print(f"DONE: {n} {'(dry-run)' if args.dry_run else 'inserted'}")


if __name__ == "__main__":
    main()
