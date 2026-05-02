"""Quick diagnostic: show recent gate rejections and activity."""
from database import FastTickDatabase

db = FastTickDatabase(db_path="./binance_ticks.duckdb", read_only=True)

# 1. Recent crew_events
print("=" * 100)
print("RECENT CREW EVENTS (last 50)")
print("=" * 100)
events = db.list_crew_events(symbol="BTCUSDT", limit=50)
for e in events[:50]:
    t = str(e.get("event_time", ""))[:19]
    comp = str(e.get("component", ""))[:20]
    etype = str(e.get("event_type", ""))[:30]
    title = str(e.get("title", ""))[:80]
    print(f"{t} | {comp:20s} | {etype:30s} | {title}")

# 2. Count events by type
print("\n" + "=" * 100)
print("EVENT COUNT BY TYPE (all time)")
print("=" * 100)
with db._lock:
    rows = db._conn.execute("""
        SELECT event_type, COUNT(*) as cnt
        FROM crew_events
        WHERE symbol = 'BTCUSDT'
        GROUP BY event_type
        ORDER BY cnt DESC
    """).fetchall()
for r in rows:
    print(f"  {r[0]:35s} : {r[1]}")

# 3. Count events by component
print("\n" + "=" * 100)
print("EVENT COUNT BY COMPONENT (all time)")
print("=" * 100)
with db._lock:
    rows = db._conn.execute("""
        SELECT component, COUNT(*) as cnt
        FROM crew_events
        WHERE symbol = 'BTCUSDT'
        GROUP BY component
        ORDER BY cnt DESC
    """).fetchall()
for r in rows:
    print(f"  {r[0]:35s} : {r[1]}")

# 4. Trade signals status breakdown
print("\n" + "=" * 100)
print("TRADE SIGNALS BY STATUS")
print("=" * 100)
with db._lock:
    rows = db._conn.execute("""
        SELECT status, COUNT(*) as cnt
        FROM trade_signals
        WHERE symbol = 'BTCUSDT'
        GROUP BY status
        ORDER BY cnt DESC
    """).fetchall()
for r in rows:
    print(f"  {r[0]:25s} : {r[1]}")

# 5. Recent gate rejections (from crew_events with 'gate' or 'reject' in type)
print("\n" + "=" * 100)
print("RECENT GATE EVENTS (last 30)")
print("=" * 100)
with db._lock:
    rows = db._conn.execute("""
        SELECT event_time, component, event_type, title, message
        FROM crew_events
        WHERE symbol = 'BTCUSDT'
          AND (LOWER(event_type) LIKE '%gate%'
               OR LOWER(event_type) LIKE '%reject%'
               OR LOWER(event_type) LIKE '%block%'
               OR LOWER(event_type) LIKE '%fail%'
               OR LOWER(event_type) LIKE '%skip%'
               OR LOWER(component) LIKE '%gate%'
               OR LOWER(component) LIKE '%backbone%')
        ORDER BY event_time DESC
        LIMIT 30
    """).fetchall()
for r in rows:
    t = str(r[0])[:19]
    msg = str(r[4] or "")[:100]
    print(f"{t} | {r[1]:15s} | {r[2]:25s} | {r[3][:50]:50s} | {msg}")

# 6. Check total crew_events count
print("\n" + "=" * 100)
print("TOTAL CREW EVENTS COUNT")
print("=" * 100)
with db._lock:
    cnt = db._conn.execute("SELECT COUNT(*) FROM crew_events WHERE symbol = 'BTCUSDT'").fetchone()[0]
print(f"  Total: {cnt}")

db.close()
