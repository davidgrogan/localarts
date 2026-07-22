"""Seed the database with a starter venue, a few local artists, and a
couple of sample shows so the calendar isn't empty on first run.

Safe to re-run: looks up by slug/title before inserting.

Usage:
    python seed.py
"""
from datetime import datetime, timedelta

from app import create_app
from app.models import db, Venue, Artist, Event
from app.utils import slugify, get_or_create_event_type


def get_or_create_venue(**kwargs):
    venue = Venue.query.filter_by(slug=kwargs["slug"]).first()
    if venue:
        return venue
    venue = Venue(**kwargs)
    db.session.add(venue)
    db.session.flush()
    return venue


def get_or_create_artist(**kwargs):
    artist = Artist.query.filter_by(slug=kwargs["slug"]).first()
    if artist:
        return artist
    artist = Artist(**kwargs)
    db.session.add(artist)
    db.session.flush()
    return artist


def main():
    app = create_app()
    with app.app_context():
        # Created (not just looked up) here since it needs to exist before
        # any venue below can reference it as a default -- get_or_create_
        # event_type() is the same case-insensitive-dedupe helper the admin
        # UI's "quick add a new tag" inputs use.
        music_tag = get_or_create_event_type("Music")

        iron_horse = get_or_create_venue(
            name="Iron Horse Music Hall",
            slug="iron-horse-music-hall",
            address="18 Center St.",
            city="Northampton",
            state="MA",
            website_url="https://ironhorse.org",
            # The venue's actual show listing page. See app/scrapers/
            # squarespace_json.py for how this gets turned into events --
            # ironhorse.org is a Squarespace site, so the scraper hits
            # this same URL with ?format=json appended.
            events_url="https://ironhorse.org/shows",
            # Confirmed via the scrape preview screen: this venue's calendar
            # is an embedded Elfsight "Event Calendar" widget, not native
            # Squarespace content (the Squarespace JSON's own collection
            # reports itemCount: 0). Confirmed via a real rendered-page
            # sample that each event card embeds a schema.org Event
            # JSON-LD script tag (full ISO dates incl. year, description,
            # location) -- much more reliable than the widget's visible
            # DOM, which uses hashed class names and never shows a year.
            # The Iron Horse's feed also covers several sibling "Parlor
            # Room Collective" venues (Black Birch Vineyard, Musician's
            # Workshop, etc.), so location_match filters events down to
            # ones actually at the Iron Horse.
            source_type="elfsight_jsonld",
            scrape_config='{"location_match": ["Iron Horse"]}',
            # Every show here is a live-music show, so scraped imports
            # (and manual adds that don't pick a tag) default to "Music"
            # rather than needing tagging by hand every time.
            default_event_type=music_tag,
        )

        parlor_room = get_or_create_venue(
            name="The Parlor Room",
            slug="the-parlor-room",
            address="32 Masonic St.",
            city="Northampton",
            state="MA",
            website_url="https://ironhorse.org",
            events_url="https://ironhorse.org/parlorroomshows",
            # Same shared Elfsight feed/"Parlor Room Collective" situation
            # as the Iron Horse above -- filter to this venue's own shows.
            source_type="elfsight_jsonld",
            scrape_config='{"location_match": ["Parlor Room"]}',
            default_event_type=music_tag,
        )

        academy_of_music = get_or_create_venue(
            name="Academy of Music",
            slug="academy-of-music",
            address="274 Main St.",
            city="Northampton",
            state="MA",
            website_url="https://aomtheatre.com",
            events_url="https://aomtheatre.com/event-calendar",
            # Plain WordPress site -- events are in the server-rendered
            # HTML (confirmed via a direct fetch, no JS execution needed),
            # so the generic CSS-selector scraper works here, unlike the
            # Iron Horse's Elfsight widget. Selectors below confirmed from
            # a real "Copy outerHTML" of one event card:
            #   <div class="event_card">
            #     <div class="event_card_content">
            #       <div class="event_card_presents">DSP Shows</div>
            #       <div class="event_card_title"><h5>...</h5></div>
            #       <div class="event_card_date_times">Saturday, July 18th, 2026 at 8:00pm</div>
            #       <div class="event_card_details_button"><a href="...">Tickets on sale now</a></div>
            # Dates here are a free-typed field, not machine-readable --
            # some entries span multiple nights and omit the year (e.g.
            # "Friday, March 12th and Saturday, March 13th"). html_generic's
            # fuzzy-date parsing handles that: it keeps only the first date
            # mentioned and carries the last confirmed year forward as a
            # fallback, which works because the listing is in chronological
            # order. No date_format is set so that fuzzy path runs.
            source_type="html",
            scrape_config=(
                '{"item_selector": ".event_card", '
                '"title_selector": ".event_card_title h5", '
                '"date_selector": ".event_card_date_times", '
                '"link_selector": ".event_card_details_button a", '
                '"description_selector": ".event_card_presents", '
                '"image_selector": ".event_card_image img"}'
            ),
            default_event_type=music_tag,
        )

        haze = get_or_create_venue(
            name="Haze",
            slug="haze",
            address="24 Main St.",
            city="Northampton",
            state="MA",
            website_url="https://www.hazenorthampton.org",
            events_url="https://www.hazenorthampton.org/",
            # A custom (non-Squarespace/WordPress) Next.js site with its
            # own built-in calendar widget -- confirmed server-rendered
            # via a direct fetch, no headless browser needed. Its markup
            # is Tailwind utility classes with no semantic hooks, but two
            # things are stable: each day cell has a real
            # <time datetime="YYYY-MM-DD"> (year included), and each
            # event's clickable card has aria-label="Event details: <title>".
            # Events are grouped under their day cell rather than each
            # carrying their own date, which doesn't fit the generic
            # item_selector/date_selector model, so this uses a dedicated
            # module (app/scrapers/haze_calendar.py) instead of scrape_config.
            source_type="haze_calendar",
            scrape_config="{}",
            default_event_type=music_tag,
        )

        smith_college = get_or_create_venue(
            name="Smith College Events",
            slug="smith-college-events",
            address="10 Elm St.",
            city="Northampton",
            state="MA",
            website_url="https://www.smith.edu",
            events_url="https://www.smith.edu/news-events/events",
            # This is the whole college's events calendar (exhibitions,
            # lectures, performances, etc.), not a single music venue --
            # David chose to pull everything rather than filter down to
            # just the "Performances" event type, so expect a lot of
            # non-music noise here; use the admin Review queue to pick
            # out what's actually relevant. Drupal 10, server-rendered
            # (confirmed via a direct fetch -- no JS needed), paginated
            # with a plain ?page=N query param. Selectors confirmed from
            # a real raw-HTML sample of one event "teaser":
            #   <article class="teaser">
            #     <h2 class="teaser__heading"><a class="heading__link" href="...">Title</a></h2>
            #     <p class="teaser__subheading"> Wednesday, July 22, 2026 | 9 a.m.-4 p.m.</p>
            #     <p class="teaser__text">Description...</p>
            #     <div class="teaser__media"><img src="..."></div>
            #   </article>
            # The subheading's "<date> | <start>-<end> time" shape needed
            # a new date-cleanup heuristic in html_generic.py (see that
            # file's docstring) -- handing the whole string to dateutil's
            # fuzzy parser directly picked up the *end* time instead of
            # the start time. max_pages fetches the first 6 pages (~36
            # listing rows) each run so near-term events aren't missed;
            # bump it in the admin if that's not far enough out.
            source_type="html",
            scrape_config=(
                '{"item_selector": "article.teaser", '
                '"title_selector": ".heading__link", '
                '"date_selector": ".teaser__subheading", '
                '"description_selector": ".teaser__text", '
                '"image_selector": ".teaser__media img", '
                '"page_param": "page", "max_pages": 6}'
            ),
        )

        luthiers_coop = get_or_create_venue(
            name="Luthier's Co-Op",
            slug="luthiers-co-op",
            address="108 Cottage St.",
            # Easthampton, not Northampton -- a few towns over, still
            # squarely in the "NoHo Now!" area David books/attends in.
            city="Easthampton",
            state="MA",
            website_url="https://www.luthiers-coop.com",
            # WordPress + "The Events Calendar" plugin -- confirmed via
            # meta tags (tec-api-*, generator: WordPress) on a direct
            # fetch of /events/. That plugin publishes a real iCal export
            # (the page's own "+ Export Events" link), which is far more
            # robust than scraping the calendar-grid HTML: real UTC/zoned
            # start-end times, stable per-event UIDs, no JS needed.
            events_url="https://www.luthiers-coop.com/events/?ical=1",
            source_type="ical",
            # This venue's feed mixes real shows in with day-to-day
            # operational notices that aren't events -- "CLOSED" /
            # "CLOSED FOR SUMMER VACATION" and "BackStage Bar Open
            # 4-11pm" (the bar's daily hours, posted as its own calendar
            # entry every day it's open). title_exclude drops both
            # before they ever reach the review queue. Recurring
            # Open Mic / Karaoke entries are kept -- those are real
            # weekly entertainment, not just operating hours.
            scrape_config='{"title_exclude": ["CLOSED", "BackStage Bar Open"]}',
            default_event_type=music_tag,
        )

        thirtythree_hawley = get_or_create_venue(
            name="33 Hawley",
            slug="33-hawley",
            address="33 Hawley St.",
            city="Northampton",
            state="MA",
            website_url="https://www.33hawley.org",
            # The calendar widget is embedded right on the homepage (no
            # separate /events page -- confirmed via sitemap.xml, which
            # lists no such page at all). Confirmed Elfsight "Event
            # Calendar" -- same widget as Iron Horse/Parlor Room -- via
            # a DevTools Network-tab request to
            # universe-static.elfsightcdn.com/.../event-calendar/....
            events_url="https://www.33hawley.org/",
            source_type="elfsight_jsonld",
            # This building hosts several resident arts orgs (Northampton
            # Center for the Arts, plus building partners) running dance,
            # theatre, classes, and workshops through the same shared
            # calendar -- David only wants live performances pulled in
            # automatically, not every class/workshop/rental (the
            # opposite tradeoff from Smith College above, which
            # deliberately pulls everything). category_include filters
            # to events whose visible Elfsight category tag contains
            # "Performance"; include_all_locations is set since this
            # widget (unlike Iron Horse's shared feed) isn't expected to
            # list other physical venues, just 33 Hawley's own building.
            # Not yet verified against this venue's actual rendered
            # widget markup (this sandbox can't launch a headless
            # browser to check) -- if the real category text doesn't say
            # exactly "Performance", tell me what it does say and this
            # filter is a one-line fix.
            scrape_config='{"include_all_locations": true, "category_include": ["Performance"]}',
        )

        artist_1 = get_or_create_artist(
            name="Sample Local Artist",
            slug=slugify("Sample Local Artist"),
            genre="Folk / Americana",
            hometown="Northampton, MA",
            is_local=True,
            bio="Placeholder artist -- replace with a real local act once you're populating this for real.",
        )
        artist_2 = get_or_create_artist(
            name="Example Trio",
            slug=slugify("Example Trio"),
            genre="Jazz",
            hometown="Amherst, MA",
            is_local=True,
            bio="Placeholder artist -- replace with a real local act once you're populating this for real.",
        )

        # A couple of manually-entered sample shows so the calendar has
        # content immediately, independent of whether a live scrape has
        # been run yet.
        if not Event.query.filter_by(title="Sample Show -- Iron Horse").first():
            db.session.add(
                Event(
                    venue_id=iron_horse.id,
                    title="Sample Show -- Iron Horse",
                    start_datetime=datetime.utcnow() + timedelta(days=5, hours=2),
                    description="Placeholder show, added manually. Delete once real scraped/added shows are in.",
                    source="manual",
                    is_approved=True,
                    artists=[artist_1],
                )
            )
        if not Event.query.filter_by(title="Sample Show -- Parlor Room").first():
            db.session.add(
                Event(
                    venue_id=parlor_room.id,
                    title="Sample Show -- Parlor Room",
                    start_datetime=datetime.utcnow() + timedelta(days=9, hours=3),
                    description="Placeholder show, added manually.",
                    source="manual",
                    is_approved=True,
                    artists=[artist_2],
                )
            )

        db.session.commit()
        print("Seed complete:")
        print(f"  Venues: {Venue.query.count()}")
        print(f"  Artists: {Artist.query.count()}")
        print(f"  Events: {Event.query.count()}")


if __name__ == "__main__":
    main()
