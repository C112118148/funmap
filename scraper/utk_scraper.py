"""UDN (年代售票) + KHAM (寬宏售票) scrapers — Tier 2 sources.

Both ride the same UTK ASP.NET backend (imgs2.utiki.com.tw assets) with
different storefront markup:

UDN list cards (homepage + UTK0101_0N category pages):
  a[title][href*=UTK02] > yd_card-body >
    h5.yd_card-title | icon-date > div.ellipsis | icon-loc > div.ellipsis
  -> title + date range + venue straight from the card. Product pages add
  an exact "演出時間｜19:00" refinement when present.

KHAM list cards (UTK0101_06 category pages, self-discovered via CATEGORY=):
  a[href*=UTK0201?PRODUCT_ID] > span.title
  -> title ONLY. Show info lives in JPG images, so KHAM is city-level:
  session date from 【2/27場次】 title hints, venue from city keywords
  (KAOHSIUNG/TAIPEI/.../高雄/台北/...), all flagged estimated.

Run:  python utk_scraper.py [--limit N] [--dry-run] [--site udn|kham|all]
Env:  GEMINI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import ExtractedEvent
from event_detail import TZ8, norm, parse_suffix_ts
from vendor_common import fetch_html, geocode_venue, upsert_events, clean

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv(Path(__file__).resolve().parent.parent / "web" / ".env.local")
os.environ.setdefault("NEXT_PUBLIC_SUPABASE_URL", os.environ.get("SUPABASE_URL", ""))

UDN_HOME = "https://tickets.udnfunlife.com/application/utk01/utk0101_.aspx"
UDN_CAT_RE = re.compile(r'href="([^"]*UTK0101_0\d\.aspx[^"]*)"')
UDN_CARD_RE = re.compile(
    r"<a[^>]*title='([^']*)'[^>]*href='([^']*UTK02[^']*)'[^>]*>"
    r".*?yd_card-title[^>]*>(.*?)</h5>"
    r".*?icon-date.*?ellipsis[^>]*>(.*?)</div>"
    r".*?icon-loc.*?ellipsis[^>]*>(.*?)</div>",
    re.S)
UDN_SHOWTIME_RE = re.compile(r"演出時間[｜|]\s*(\d{1,2}):(\d{2})")

KHAM_LIST_TMPL = "https://kham.com.tw/application/UTK01/UTK0101_06.aspx?TYPE=1&CATEGORY={cid}"
KHAM_CAT_RE = re.compile(r"CATEGORY=(\d+)")
KHAM_CARD_RE = re.compile(
    r'<a[^>]*href="([^"]*UTK0201[^"]*PRODUCT_ID=([A-Z0-9]+)[^"]*)"[^>]*>'
    r'.*?<span class="title">(.*?)</span>',
    re.S)
KHAM_SESSION_RE = re.compile(r"【(\d{1,2})/(\d{1,2})場次】")
CITY_HINTS = [
    ("KAOHSIUNG", "高雄市"), ("TAIPEI", "台北市"), ("TAICHUNG", "台中市"),
    ("TAINAN", "台南市"), ("TAOYUAN", "桃園市"), ("HSINCHU", "新竹市"),
    ("高雄", "高雄市"), ("台北", "台北市"), ("臺北", "台北市"),
    ("台中", "台中市"), ("臺中", "台中市"), ("台南", "台南市"),
    ("桃園", "桃園市"), ("新竹", "新竹市"), ("嘉義", "嘉義市"),
]
DAY_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")


def abs_url(href: str, base: str) -> str:
    href = href.strip()
    if href.startswith("http"):
        return href
    if href.startswith("../"):
        root = base.split("/application/")[0]
        return root + "/application/" + href[3:]
    return base.rsplit("/", 1)[0] + "/" + href


def parse_udn_cards(html: str) -> list[dict]:
    rows = []
    for title_attr, href, title, date_text, venue in UDN_CARD_RE.findall(html or ""):
        days = DAY_RE.findall(date_text or "")
        if not days:
            continue
        y, m, d = (int(x) for x in days[0])
        y2, m2, d2 = (int(x) for x in days[-1])
        try:
            start = datetime(y, m, d, tzinfo=TZ8)
            end = datetime(y2, m2, d2, tzinfo=TZ8)
        except ValueError:
            continue
        rows.append({
            "url": href if href.startswith("http") else
                   "https://tickets.udnfunlife.com" + href if href.startswith("/") else href,
            "title": (clean(title) or clean(title_attr))[:100],
            "venue": clean(venue),
            "start": start, "end": end,
        })
    return rows


def refine_udn_time(row: dict) -> None:
    """Product page '演出時間｜19:00' -> pin the card date to that HH:MM."""
    html = fetch_html(row["url"], "udn-product")
    if not html:
        return
    m = UDN_SHOWTIME_RE.search(html)
    if m and row["start"] is not None:
        row["start"] = row["start"].replace(hour=int(m.group(1)), minute=int(m.group(2)))
        row["end"] = row["end"].replace(hour=int(m.group(1)), minute=int(m.group(2)))
        row["exact_time"] = True
    time.sleep(1)


def city_hint(title: str) -> str:
    t = (title or "").upper()
    for key, city in CITY_HINTS:
        if key.upper() in t or key in (title or ""):
            return city
    return ""


def parse_kham_cards(html: str, base: str) -> list[dict]:
    rows = []
    now = datetime.now(TZ8)
    for href, pid, title in KHAM_CARD_RE.findall(html or ""):
        title = clean(title)
        if not title:
            continue
        m = KHAM_SESSION_RE.search(title)
        city = city_hint(title)
        if not m or not city:
            continue  # need both a session date and a city -> else unmappable
        year = now.year
        try:
            start = datetime(year, int(m.group(1)), int(m.group(2)), tzinfo=TZ8)
        except ValueError:
            continue
        if start < now - timedelta(days=1):
            try:
                start = start.replace(year=year + 1)
            except ValueError:
                continue
        rows.append({
            "url": abs_url(href, base),
            "title": title[:100],
            "venue": city,   # city-level only (real venue lives in JPG images)
            "start": start, "end": start,
            "city_level": True,
        })
    return rows


def collect_udn(limit: int) -> list[dict]:
    home = fetch_html(UDN_HOME, "udn")
    if not home:
        return []
    rows = parse_udn_cards(home)
    # crawl each distinct category URL found on homepage
    base = "https://tickets.udnfunlife.com/application/utk01/"
    for link in sorted(set(re.findall(r'href="([^"]*UTK0101_0\d\.aspx[^"]*)"', home)))[:5]:
        url = link if link.startswith("http") else base + link.split("/")[-1].split("?")[0] + (
            ("?" + link.split("?", 1)[1]) if "?" in link else "")
        for r in parse_udn_cards(fetch_html(url, "udn-cat") or ""):
            if r["url"] not in {x["url"] for x in rows}:
                rows.append(r)
        time.sleep(1)
        if len(rows) >= limit * 2:
            break
    return rows[:limit]


def collect_kham(limit: int) -> list[dict]:
    seed = fetch_html(KHAM_LIST_TMPL.format(cid=205), "kham")
    if not seed:
        return []
    cids = sorted(set(KHAM_CAT_RE.findall(seed)))[:4]
    rows: list[dict] = []
    for cid in cids:
        url = KHAM_LIST_TMPL.format(cid=cid)
        for r in parse_kham_cards(fetch_html(url, "kham-cat") or "", url):
            if r["url"] not in {x["url"] for x in rows}:
                rows.append(r)
        time.sleep(1)
        if len(rows) >= limit:
            break
    return rows[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--site", choices=["udn", "kham", "all"], default="all")
    args = ap.parse_args()

    all_rows: list[dict] = []
    if args.site in ("udn", "all"):
        print("Collecting UDN listing...")
        for r in collect_udn(args.limit):
            r["site"] = "udn"
            all_rows.append(r)
        print(f"  UDN rows: {len(all_rows)}")
    if args.site in ("kham", "all"):
        print("Collecting KHAM listing...")
        base_n = len(all_rows)
        for r in collect_kham(args.limit):
            r["site"] = "kham"
            all_rows.append(r)
        print(f"  KHAM rows: {len(all_rows) - base_n}")
    if not all_rows:
        print("DONE: nothing collected (exit 0).")
        return

    # UDN exact-time refinement from product pages (cap to save time).
    for r in [x for x in all_rows if x["site"] == "udn"][:12]:
        refine_udn_time(r)

    by_venue: dict[str, list[dict]] = {}
    for r in all_rows:
        if not r.get("venue"):
            continue
        by_venue.setdefault(r["venue"], []).append(r)
    print(f"{len(by_venue)} distinct venues")

    extracted: list[tuple[ExtractedEvent, str]] = []
    for i, (venue, items) in enumerate(by_venue.items(), 1):
        print(f"  [{i}/{len(by_venue)}] geocoding: {venue[:40]}...", flush=True)
        try:
            geo = geocode_venue(venue, items[0]["title"])
        except Exception as e:
            print(f"      -> error: {str(e)[:80]}")
            continue
        if not geo:
            print("      -> skipped (unlocatable venue)")
            continue
        for it in items:
            exact = it.pop("exact_time", False) or not it.get("city_level", False)
            if it["site"] == "kham" or not exact:
                # Day-granularity or city-level: whole-day window, estimated.
                start = it["start"].replace(hour=0, minute=0)
                end = it["end"].replace(hour=23, minute=59)
                estimated = True
            else:
                start, end = it["start"], it["end"]
                if end <= start:
                    end = start + timedelta(hours=3)
                estimated = False
            try:
                ev = ExtractedEvent.model_validate({
                    "title": it["title"],
                    "summary": f"場館：{venue}",
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
        time.sleep(5)

    print(f"\nExtracted {len(extracted)} mappable events; upserting...")
    n = upsert_events(extracted, args.dry_run)
    print(f"DONE: {n} events {'(dry-run)' if args.dry_run else 'inserted'}")


if __name__ == "__main__":
    main()
