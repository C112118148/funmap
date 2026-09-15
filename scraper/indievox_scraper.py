"""IndieVox (街聲) scraper — Tier 2 source.

Same platform family as TixCraft (static assets on *.tixcraft.com) but
— crucially — detail pages return HTTP 200 to plain clients, so full
enrichment works: list (/activity) gives title + date range, detail
(/activity/detail/XX_ivNNNNNNN) gives exact show times + venue + address:

  <span>演出地點：WESTAR（台北市萬華區西門里漢中街116號8樓）</span>
  ... & 2026/09/27 (日) 16:00 & 2026/09/28 (一) 16:00 ...

List card: panel-body > a[href=/activity/detail/...] > div.date + div.multi_ellipsis.
(Titles group under panel-heading dates; card div.date is authoritative.)

Run:  python indievox_scraper.py [--limit N] [--dry-run]
Env:  GEMINI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import ExtractedEvent
from event_detail import TZ8, norm, parse_suffix_ts
from vendor_common import fetch_html, geocode_venue, upsert_events, clean

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv(Path(__file__).resolve().parent.parent / "web" / ".env.local")
os.environ.setdefault("NEXT_PUBLIC_SUPABASE_URL", os.environ.get("SUPABASE_URL", ""))

LIST_URL = "https://www.indievox.com/activity"
CARD_RE = re.compile(
    r'<a[^>]*href="((?:https://www\.indievox\.com)?/activity/detail/[^"]+)"[^>]*>'
    r'.*?<div class="date">(.*?)</div>.*?<div class="multi_ellipsis">(.*?)</div>',
    re.S)
DETAIL_VENUE_RE = re.compile(r"演出地點[：:]\s*([^<]+)")
DETAIL_TIME_RE = re.compile(r"(\d{4}/\d{1,2}/\d{1,2}).{0,50}?(\d{1,2}:\d{2})")
LIST_DAY_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")


def filter_to_list_range(times: list, list_date: str) -> list:
    """Drop sale-window timestamps (e.g. 07/31 on-sale) that leak into the
    detail page: keep only times inside the card's displayed date range."""
    days = LIST_DAY_RE.findall(list_date or "")
    if not days or not times:
        return times
    from datetime import date as _date
    ds = sorted(_date(int(y), int(m), int(d)) for y, m, d in days)
    lo, hi = ds[0], ds[-1] + timedelta(days=1)
    kept = [t for t in times if lo <= t.date() <= hi]
    return kept or []  # empty = untrustworthy, caller skips


def parse_detail(html: str) -> dict:
    out: dict = {"venue": "", "address": "", "times": []}
    m = DETAIL_VENUE_RE.search(html or "")
    if m:
        v = clean(m.group(1))
        # norm() full2half turns （） into () — accept both.
        pm = re.match(r"(.+?)[（(](.+)[）)]\s*$", v)
        if pm:
            out["venue"], out["address"] = pm.group(1).strip(), pm.group(2).strip()
        else:
            out["venue"] = v
    seen = set()
    for dm, tm in DETAIL_TIME_RE.findall(html or ""):
        ts = parse_suffix_ts(f"{dm} {tm}(+0800)")
        if ts and ts not in seen:
            seen.add(ts)
            out["times"].append(ts)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print("Fetching IndieVox listing...")
    html = fetch_html(LIST_URL, "indievox")
    if not html:
        print("DONE: listing unreachable, nothing to do (exit 0).")
        return
    rows = []
    for href, date_text, title in CARD_RE.findall(html)[:args.limit]:
        url = href if href.startswith("http") else "https://www.indievox.com" + href
        if url in {r["url"] for r in rows}:
            continue  # desktop+mobile duplicate anchors
        rows.append({"url": url, "title": norm(title)[:100],
                     "list_date": clean(date_text)})
    print(f"Got {len(rows)} events")

    # Detail enrichment: exact times + venue + street address.
    for i, r in enumerate(rows, 1):
        print(f"  [{i}/{len(rows)}] detail: {r['title'][:40]}...", flush=True)
        d = parse_detail(fetch_html(r["url"], "indievox") or "")
        r.update(d)
        time.sleep(2)  # be polite

    # Group by venue for one geocode call each.
    by_venue: dict[str, list[dict]] = {}
    for r in rows:
        key = r["venue"] or r["title"]
        by_venue.setdefault(key, []).append(r)
    print(f"{len(by_venue)} distinct venues")

    extracted: list[tuple[ExtractedEvent, str]] = []
    for i, (venue, items) in enumerate(by_venue.items(), 1):
        sample = items[0]
        addr = sample.get("address", "")
        print(f"  [{i}/{len(by_venue)}] geocoding: {venue[:40]}...", flush=True)
        try:
            geo = geocode_venue(f"{venue} {addr}".strip(), sample["title"])
        except Exception as e:
            print(f"      -> error: {str(e)[:80]}")
            continue
        if not geo:
            print("      -> skipped (unlocatable venue)")
            continue
        for it in items:
            times = filter_to_list_range(it["times"], it.get("list_date", ""))
            if times:
                start = min(times)
                end = max(times)
                if end <= start:
                    end = start + timedelta(hours=3)
                estimated = False
            else:
                continue  # no deterministic time -> skip
            try:
                ev = ExtractedEvent.model_validate({
                    "title": it["title"],
                    "summary": f"場館：{venue}" + (f"（{addr}）" if addr else ""),
                    "category": "promotion",
                    "lat": geo["lat"], "lng": geo["lng"],
                    "start_time": start.isoformat(),
                    "end_time": end.isoformat(),
                    "is_time_estimated": estimated,
                })
            except Exception as ve:
                print(f"      -> validation failed: {str(ve)[:80]}")
                continue
            extracted.append((ev, it["url"]))
        time.sleep(5)  # Gemini free tier 429s fast

    print(f"\nExtracted {len(extracted)} mappable events; upserting...")
    n = upsert_events(extracted, args.dry_run)
    print(f"DONE: {n} events {'(dry-run)' if args.dry_run else 'inserted'}")


if __name__ == "__main__":
    main()
