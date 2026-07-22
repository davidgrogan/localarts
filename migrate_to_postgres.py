"""One-time copy of your local SQLite data (venues, artists, events, event
type tags, and their scrape history) into a remote Postgres database --
for moving what you've built up locally onto the droplet without
re-entering venues or re-running every scrape.

This does NOT touch the droplet's Postgres over the open internet --
Postgres there only listens on localhost (see DEPLOY.md), which is the
right way to leave it. Instead, run this from your Mac through an SSH
tunnel that forwards a local port to the droplet's Postgres:

    ssh -L 5433:localhost:5432 root@YOUR_DROPLET_IP -N

(leave that running in its own terminal tab), then in another tab, from
this project folder with your local .venv active:

    python3 migrate_to_postgres.py "postgresql://localarts:YOUR_PG_PASSWORD@localhost:5433/localarts"

WARNING: this WIPES every row currently in the target database's venue/
artist/event_type/event/event_artists/event_event_types/scrape_run tables
before copying your local data in -- it's meant to *replace* whatever's
there (which, unless you've been managing venues through the live site's
admin screens too, is just whatever seed.py originally put there). Don't
run this if the droplet already has real data you haven't backed up.

Requires psycopg2 (already in requirements.txt) -- if you haven't run
`pip install -r requirements.txt` locally since it was added, do that
first.
"""
import os
import sys

from sqlalchemy import MetaData, Table, create_engine, insert, select, text

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH = os.path.join(BASE_DIR, "instance", "local_music.sqlite3")

# Order matters: parents before children, so foreign keys never point at
# a row that doesn't exist yet. event_type comes before venue because
# venue.default_event_type_id references it. event_artists and
# event_event_types (the many-to-many tables) have no serial "id" of
# their own, so they're skipped in the sequence-reset step further down
# but still copied like any other table.
TABLES_IN_ORDER = [
    "event_type",
    "venue",
    "artist",
    "event",
    "event_artists",
    "event_event_types",
    "scrape_run",
]


def main():
    if len(sys.argv) != 2:
        print('Usage: python3 migrate_to_postgres.py "postgresql://user:pass@host:port/dbname"')
        sys.exit(1)

    target_url = sys.argv[1]

    if not os.path.exists(SQLITE_PATH):
        print(f"No local database found at {SQLITE_PATH} -- nothing to migrate.")
        sys.exit(1)

    print(f"Source (local):  sqlite:///{SQLITE_PATH}")
    print(f"Target (remote): {target_url.split('@')[-1]}")  # don't echo the password
    confirm = input(
        "\nThis will ERASE existing venue/artist/event data in the target "
        "database and replace it with your local data. Type 'yes' to continue: "
    )
    if confirm.strip().lower() != "yes":
        print("Aborted -- nothing was changed.")
        sys.exit(0)

    src_engine = create_engine(f"sqlite:///{SQLITE_PATH}")
    dst_engine = create_engine(target_url)

    src_meta = MetaData()
    src_meta.reflect(bind=src_engine, only=TABLES_IN_ORDER)
    dst_meta = MetaData()
    dst_meta.reflect(bind=dst_engine, only=TABLES_IN_ORDER)

    with dst_engine.begin() as dst_conn:
        print("\nClearing existing target data...")
        for table_name in reversed(TABLES_IN_ORDER):
            dst_conn.execute(text(f'TRUNCATE TABLE "{table_name}" RESTART IDENTITY CASCADE'))

        with src_engine.connect() as src_conn:
            for table_name in TABLES_IN_ORDER:
                src_table = src_meta.tables[table_name]
                dst_table = dst_meta.tables[table_name]
                rows = [dict(row._mapping) for row in src_conn.execute(select(src_table))]
                if rows:
                    dst_conn.execute(insert(dst_table), rows)
                print(f"  {table_name}: copied {len(rows)} row(s)")

        print("\nResetting Postgres auto-increment sequences...")
        for table_name in TABLES_IN_ORDER:
            if table_name in ("event_artists", "event_event_types"):
                continue  # composite primary key, no serial sequence
            dst_conn.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table_name}), 1))"
                )
            )

    print("\nDone. Restart the app on the droplet (systemctl restart local-music.service)")
    print("isn't strictly required -- it reads fresh from the DB on every request -- but")
    print("worth loading the site to confirm everything looks right.")


if __name__ == "__main__":
    main()
