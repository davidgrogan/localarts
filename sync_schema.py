"""Bring a deployed database's actual table structure in line with what
app/models.py currently declares -- meant for the droplet's Postgres
database, which (unlike local SQLite) has no automatic column-migration
step (see app/__init__.py's _run_sqlite_column_migrations(), which
explicitly skips any non-SQLite backend).

Why this exists: db.create_all() (called automatically by create_app(),
so it already ran once when this script imports the app) only creates
tables that are *completely missing* -- it never alters a table that
already exists to add a newly-declared column. Every time a new column
gets added to an existing model (Event.genre, Artist.image_url,
GigSubmission.genres_text, etc.), a from-scratch install picks it up for
free via create_all(), but an *existing* database -- like the droplet's,
which has been running since before most of these columns existed --
silently keeps the old, narrower table shape until someone runs the
matching ALTER TABLE by hand. DEPLOY.md's "Redeploying after future
changes" section has always covered this with a manually-written list of
ALTER TABLE commands, but that list only gets updated when someone
remembers to add to it -- and after a long stretch of local-only changes
(Genre Tags, Category Tags, the Quonk/Bandcamp/Bombyx work, the gig
submission feature, etc.) all landing on the droplet in one sync, that
list is exactly the kind of thing that's easy to undercount by hand.

This script instead *asks the database itself* what columns each table
actually has (via SQLAlchemy's cross-backend inspection API) and compares
that against what the current models.py declares, so it can't miss
anything regardless of how long it's been since the last deploy or how
many features landed in between. It only ever proposes ADD COLUMN and
widening ALTER COLUMN ... TYPE statements -- never drops a column,
narrows one, or otherwise touches existing data -- so it's safe to run
repeatedly.

IMPORTANT for anyone adding a new `nullable=False` column to an existing
table: it also needs a `server_default=` set in models.py, not just
`default=`. SQLAlchemy's `default=` is a Python-side value it applies on
INSERT through the ORM -- invisible to raw DDL. `CreateColumn` (what
`find_missing_columns()` below uses to generate each ALTER TABLE
statement) only ever emits a `DEFAULT` clause from `server_default=`.
Without one, a NOT NULL column with no way to backfill existing rows
fails outright on Postgres: `psycopg2.errors.NotNullViolation: column
"..." contains null values` -- exactly what happened deploying
`EventType.is_public_category` (see that column's own comment in
app/models.py for the fix). A nullable column doesn't need this --
existing rows can just take NULL -- only a NOT NULL one does.

Usage (on the droplet, with DATABASE_URL etc. already exported --
see DEPLOY.md's "Redeploying after future changes"):

    python3 sync_schema.py            # dry run: show what's missing, change nothing
    python3 sync_schema.py --apply    # actually run the ALTER TABLE statements

Safe to run against local SQLite too (e.g. to sanity-check the script
itself, or on a install that's been sitting untouched for a while) --
it'll typically report nothing to do there, since
_run_sqlite_column_migrations() already keeps SQLite installs current
automatically on every startup, and the widening check below is skipped
entirely on SQLite (see find_type_widenings()'s docstring for why).
"""
import sys

from sqlalchemy import inspect
from sqlalchemy.schema import CreateColumn

from app import create_app
from app.models import db


def find_missing_columns():
    """Returns a list of (table_name, column, ddl_statements) for every
    column app/models.py declares that the live database's matching table
    doesn't actually have yet. ddl_statements is a list (usually just one
    ADD COLUMN, but two for a foreign-key column -- see below) since a
    single missing column can need more than one statement to add
    correctly. Tables that don't exist at all aren't included here --
    create_app() already called db.create_all() before this runs, which
    creates any brand-new table (with every one of its columns) in one
    step; only *existing* tables gaining a *new* column need this."""
    inspector = inspect(db.engine)
    missing = []
    for table in db.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            # Shouldn't happen -- db.create_all() (called by create_app(),
            # already run by the time this executes) creates any missing
            # table outright. Noted rather than silently skipped in case
            # create_all() itself failed partway through for some reason.
            print(f"NOTE: table '{table.name}' doesn't exist yet and wasn't "
                  f"created by create_all() -- check for an earlier error.")
            continue
        existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in existing_columns:
                coldef = CreateColumn(column).compile(dialect=db.engine.dialect)
                statements = [f"ALTER TABLE {table.name} ADD COLUMN {coldef};"]
                # CreateColumn only compiles the column itself (name, type,
                # nullability) -- it deliberately doesn't include a foreign
                # key's REFERENCES clause, since that's a table-level
                # constraint in standard SQL, not part of the column
                # definition. Add it as its own ALTER TABLE ... ADD
                # CONSTRAINT so a new FK column (e.g. Venue.default_event_type_id)
                # gets real referential-integrity enforcement at the DB
                # level, not just an ordinary integer column that happens
                # to hold ids. SQLite doesn't support ADD CONSTRAINT via
                # ALTER TABLE at all (and never enforced this historically
                # either -- _run_sqlite_column_migrations()'s equivalent
                # entries are always just a bare column type, no
                # REFERENCES), so this step only applies on other backends
                # (in practice: Postgres, on the droplet).
                if db.engine.dialect.name != "sqlite":
                    for fk in column.foreign_keys:
                        constraint_name = f"{table.name}_{column.name}_fkey"
                        statements.append(
                            f"ALTER TABLE {table.name} ADD CONSTRAINT {constraint_name} "
                            f"FOREIGN KEY ({column.name}) REFERENCES "
                            f"{fk.column.table.name} ({fk.column.name});"
                        )
                missing.append((table.name, column.name, statements))
    return missing


def find_type_widenings(dialect_name=None):
    """Returns a list of (table_name, column, ddl_statement) for every
    *existing* column whose live type is more restrictive than what
    app/models.py currently declares -- in practice, a length-capped
    VARCHAR(n) that the model has since widened (to a longer VARCHAR(m),
    or to an unbounded Text). Written after Venue.image_url (originally
    VARCHAR(500)) broke migrate_to_postgres.py with "value too long for
    type character varying(500)" the first time someone pasted a real
    Instagram/Facebook photo URL (519 chars, mostly a long signed query
    string) -- the exact same class of bug the ADD COLUMN logic above
    exists to prevent, just for a column that already exists rather than
    one that's missing outright.

    Only ever proposes *widening* -- a longer VARCHAR, or VARCHAR -> Text
    -- never narrowing, so it can never truncate or lose data already in
    the column. Deliberately conservative about what counts as "safe to
    widen": both the live and declared types need a comparable `.length`
    attribute (i.e. both are bounded string types) for a plain widen, or
    the model's type needs to be genuinely unbounded (no .length at all,
    e.g. Text) for a bounded-to-unbounded widen. Anything else (a type
    that isn't a simple bounded string, e.g. changing a column's type
    entirely) is left alone rather than guessed at.

    Skipped entirely on SQLite: SQLite has no real fixed-width VARCHAR at
    all -- it stores whatever string you give it regardless of the
    declared length (this is why Haze's 519-char image_url never caused
    a problem locally, only on the droplet's actual Postgres). There's
    genuinely nothing to alter there, so this returns an empty list
    without even asking the database, rather than proposing a no-op
    ALTER TABLE.

    dialect_name defaults to the live db.engine's own dialect; overridable
    for testing this comparison logic without a real Postgres connection.
    """
    dialect_name = dialect_name or db.engine.dialect.name
    if dialect_name == "sqlite":
        return []

    inspector = inspect(db.engine)
    widenings = []
    for table in db.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        live_types = {col["name"]: col["type"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            live_type = live_types.get(column.name)
            if live_type is None:
                continue  # brand-new column -- handled by find_missing_columns() above
            live_length = getattr(live_type, "length", None)
            if live_length is None:
                continue  # live column is already unbounded (or not a string type) -- nothing to widen
            model_length = getattr(column.type, "length", None)
            model_is_wider = model_length is None or model_length > live_length
            if not model_is_wider:
                continue
            new_type_sql = column.type.compile(dialect=db.engine.dialect)
            stmt = f"ALTER TABLE {table.name} ALTER COLUMN {column.name} TYPE {new_type_sql};"
            widenings.append((table.name, column.name, stmt))
    return widenings


def main():
    apply_changes = "--apply" in sys.argv
    app = create_app()  # also runs db.create_all() + the SQLite-only column migration
    with app.app_context():
        missing = find_missing_columns()
        widenings = find_type_widenings()

        if not missing and not widenings:
            print("Nothing to do -- every table already has every column app/models.py declares, "
                  "at least as wide as it declares them.")
            return

        if missing:
            print(f"Found {len(missing)} missing column(s):\n")
            for table_name, column_name, statements in missing:
                print(f"  {table_name}.{column_name}")
                for stmt in statements:
                    print(f"    {stmt}")

        if widenings:
            print(f"\nFound {len(widenings)} column(s) narrower than app/models.py now declares "
                  f"(widening only -- existing data is never touched):\n")
            for table_name, column_name, stmt in widenings:
                print(f"  {table_name}.{column_name}")
                print(f"    {stmt}")

        if not apply_changes:
            print("\nDry run only -- nothing was changed. Re-run with --apply to execute the above.")
            return

        print("\nApplying...")
        with db.engine.connect() as conn:
            for table_name, column_name, statements in missing:
                for stmt in statements:
                    conn.execute(db.text(stmt))
                    conn.commit()
                print(f"  done: {table_name}.{column_name}")
            for table_name, column_name, stmt in widenings:
                conn.execute(db.text(stmt))
                conn.commit()
                print(f"  done: {table_name}.{column_name} (widened)")
        print("\nAll changes applied.")


if __name__ == "__main__":
    main()
