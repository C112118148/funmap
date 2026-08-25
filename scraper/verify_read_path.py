"""Verify the public read path exactly as the frontend experiences it: anon key only."""
import os
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(Path(r"C:\Users\leo\Downloads\local-pulse-db\web") / ".env.local")
anon = create_client(os.environ["NEXT_PUBLIC_SUPABASE_URL"], os.environ["NEXT_PUBLIC_SUPABASE_ANON_KEY"])

# 1. Direct table read (RLS-filtered)
rows = anon.table("events").select("title,category,crowd_status,is_verified").execute()
print(f"anon table read: {len(rows.data)} rows (expect 4)")
for r in rows.data:
    print(f"  - {r['title']} [{r['category']}] {r['crowd_status']} verified={r['is_verified']}")

# 2. RPC bbox query over Taiwan (what PulseMap calls)
rpc = anon.rpc("get_events_in_bbox", {
    "min_lng": 120.8, "min_lat": 24.0, "max_lng": 121.7, "max_lat": 25.2,
}).execute()
print(f"\nanon RPC: {len(rpc.data)} rows in bbox (expect 4)")
for r in rpc.data:
    print(f"  - ({r['lng']:.4f}, {r['lat']:.4f}) {r['title']}")

# 3. Write must be blocked for anon
try:
    anon.table("events").insert({"title": "hack", "category": "market",
                                 "start_time": "2026-01-01T00:00:00Z",
                                 "end_time": "2026-01-02T00:00:00Z"}).execute()
    print("\nFAIL: anon could insert!")
except Exception as e:
    print(f"\nPASS: anon insert blocked ({str(e)[:60]}...)")
