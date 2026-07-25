"""One-off diagnostic for "a local artist has a show today but isn't showing
up as the homepage's Featured Artist." Run this the same way you run
seed.py -- from the project root, with whatever DATABASE_URL/.env is
already set up for your normal local run:

    python3 diagnose_featured_artist.py

It checks, for each band name below, exactly what
main.py's _pick_featured_artist() checks: is there an Artist row with that
name, is is_local actually True on it, and does it have at least one
*approved* event *linked to it* (not just an event whose title happens to
mention the band) with a start_datetime that's still in the future. Prints
which of those conditions fails for each name so the actual gap is obvious
instead of guessing.

Edit BAND_NAMES below to check different acts.
"""
import sys

sys.path.insert(0, ".")

from app import create_app
from app.models import Artist, Event
from app.utils import local_now

BAND_NAMES = ["Grammerhorn Wren", "Couchboy", "Teen Driver"]

app = create_app()
with app.app_context():
    now = local_now()
    print(f"Server's current local time (what 'upcoming' is compared against): {now}\n")

    for name in BAND_NAMES:
        print(f"=== {name} ===")
        # Exact match first, then a loose contains-match in case the name in
        # the DB is spelled/punctuated slightly differently than typed here.
        artist = Artist.query.filter(Artist.name == name).first()
        if not artist:
            artist = Artist.query.filter(Artist.name.ilike(f"%{name}%")).first()

        if not artist:
            print("  NO Artist row found with this name at all.")
            print("  -> They need to be added via Artists > Add artist, or via")
            print("     the \"+ Add as local artist\" link on their event card,")
            print("     before they can ever show up as Featured Artist.")
        else:
            print(f"  Found Artist id={artist.id}, name={artist.name!r}, is_local={artist.is_local}")
            if not artist.is_local:
                print("  -> is_local is False. Edit this artist and check")
                print("     \"Lives and works locally\", then save.")

            linked_events = list(artist.events)
            if not linked_events:
                print("  -> This artist has NO events linked to them at all (artist.events is empty).")
                print("     Having a show with their name in the title isn't enough -- the")
                print("     event has to actually be linked to this Artist row. Use the")
                print("     \"+ Add as local artist\" link on that event's card (if it's not")
                print("     linked yet), or edit the event and add them under Artists.")
            else:
                print(f"  Linked events ({len(linked_events)}):")
                for e in linked_events:
                    is_future = e.start_datetime >= now
                    print(
                        f"    - {e.title!r} @ {e.start_datetime} "
                        f"(is_approved={e.is_approved}, is_future={is_future})"
                        + ("" if (e.is_approved and is_future) else "  <-- disqualifying")
                    )
        print()

    print("---")
    print("Also checking for events whose TITLE mentions these bands, regardless")
    print("of whether any Artist is linked to them (this is what a scraper would")
    print("have created on its own -- linking an artist is always a separate,")
    print("manual step):")
    print()
    for name in BAND_NAMES:
        matches = Event.query.filter(Event.title.ilike(f"%{name}%")).all()
        if not matches:
            print(f"  {name}: no events with this in the title.")
            continue
        for e in matches:
            print(
                f"  {name}: Event {e.id} {e.title!r} @ {e.start_datetime} "
                f"is_approved={e.is_approved} artists_linked={[a.name for a in e.artists]}"
            )
