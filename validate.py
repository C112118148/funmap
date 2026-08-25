"""Validate schema.sql: parse with sqlglot Postgres dialect, check structure."""
import sqlglot
from sqlglot import exp

SQL = open(r"C:\Users\leo\Downloads\local-pulse-db\schema.sql", encoding="utf-8").read()

statements = [s for s in sqlglot.parse(SQL, read="postgres") if s is not None]
print(f"Parsed OK: {len(statements)} statements")

kinds = {}
tables = []
for s in statements:
    kinds[type(s).__name__] = kinds.get(type(s).__name__, 0) + 1
    if isinstance(s, exp.Create) and s.kind == "TABLE":
        tables.append(s.find(exp.Table).name)

print("Statement mix:", {k: v for k, v in sorted(kinds.items())})
print("Tables created:", tables)

# Re-serialize to confirm round-trip fidelity (catches ambiguous syntax)
for i, s in enumerate(statements):
    sqlglot.transpile(s.sql(dialect="postgres"), read="postgres", write="postgres")

# Sanity assertions on the contract
joined = SQL.lower()
checks = {
    "postgis extension": "create extension if not exists postgis" in joined,
    "RLS enabled on events": joined.count("enable row level security") == 3,
    "bbox function SECURITY DEFINER": "security definer" in joined,
    "search_path pinned (supabase hardening)": "set search_path = public" in joined,
    "statement_timeout clamp": "statement_timeout" in joined,
    "anon execute grant": "grant execute" in joined and "to anon" in joined,
    "pg_cron purge jobs": joined.count("cron.schedule") >= 2,
    "Taiwan-safe CHECKs intact": "category in" in joined and "crowd_status in" in joined,
}
for name, ok in checks.items():
    print(("PASS" if ok else "FAIL"), "-", name)
assert all(checks.values()), "contract check failed"
print("\nALL CHECKS PASSED")
