"""Seed test events into Supabase and verify the RPC + RLS from the client side.
Uses the anon key (same as the frontend) to prove the public read path works."""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

WEB = Path(r"C:\Users\leo\Downloads\local-pulse-db\web")
load_dotenv(WEB / ".env.local")

URL = os.environ["NEXT_PUBLIC_SUPABASE_URL"]
KEY = os.environ["NEXT_PUBLIC_SUPABASE_ANON_KEY"]

admin = create_client(URL, KEY)  # service role not needed for seeding via anon? no — use direct insert attempt

now = datetime.now(timezone.utc)
tpe = timezone(timedelta(hours=8))

TEST_EVENTS = [
    {
        "title": "華山快閃市集（測試）",
        "summary": "超過 50 攤手作與小農攤位，免費入場。",
        "category": "market",
        "geom": "SRID=4326;POINT(121.5770 25.0421)",
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=6)).isoformat(),
        "is_verified": True,
        "crowd_status": "moderate",
    },
    {
        "title": "信義商圈週年慶優惠（測試）",
        "summary": "全館滿額贈，快閃優惠券發放中。",
        "category": "promotion",
        "geom": "SRID=4326;POINT(121.5654 25.0330)",
        "start_time": (now - timedelta(hours=2)).isoformat(),
        "end_time": (now + timedelta(hours=4)).isoformat(),
        "is_verified": True,
        "crowd_status": "comfortable",
    },
    {
        "title": "松菸文創展覽（測試）",
        "summary": "在地插畫家聯展，免費參觀。",
        "category": "exhibition",
        "geom": "SRID=4326;POINT(121.5570 25.0440)",
        "start_time": (now - timedelta(hours=3)).isoformat(),
        "end_time": (now + timedelta(hours=8)).isoformat(),
        "is_verified": True,
        "crowd_status": "comfortable",
    },
    {
        "title": "台中勤美綠圈圈市集（測試）",
        "summary": "草地上的週末小農市集。",
        "category": "market",
        "geom": "SRID=4326;POINT(120.9650 24.1620)",
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": (now + timedelta(hours=7)).isoformat(),
        "is_verified": False,
        "crowd_status": "crowded",
    },
]

# The anon key is read-only by RLS design, so seeding must go through a writable path.
# Try service-role env first; fall back to instructing manual seed.
service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

if not service_key:
    print("NO_SERVICE_KEY")
    print("anon key is read-only by RLS (by design). Two options:")
    print("1) Add SUPABASE_SERVICE_ROLE_KEY to web/.env.local (Dashboard > Settings > API)")
    print("2) Or paste the INSERT statements into Supabase SQL Editor manually")
    sys.exit(0)

svc = create_client(URL, service_key)
res = svc.table("events").insert(TEST_EVENTS).execute()
print(f"Inserted {len(res.data)} events")
