import sqlite3

conn = sqlite3.connect('trading_system.db')

# Normalize market IDs
conn.execute("""
    UPDATE markets
    SET market_id = LOWER(TRIM(market_id))
""")

conn.execute("""
    UPDATE positions
    SET market_id = LOWER(TRIM(market_id))
""")

# Remove invalid numeric IDs
deleted = conn.execute("""
    DELETE FROM positions
    WHERE market_id GLOB '[0-9]*'
""").rowcount

conn.commit()
conn.close()

print(f'Normalised IDs. Deleted {deleted} numeric market_id positions.')