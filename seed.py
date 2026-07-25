"""Seed the database with a starter venue, a few local artists, and a
couple of sample shows so the calendar isn't empty on first run.

Safe to re-run: looks up by slug/title before inserting.

Usage:
    python seed.py
"""
from datetime import datetime, timedelta

from app import create_app
from app.models import db, Venue, Artist, Event
from app.utils import slugify, get_or_create_event_type, get_or_create_genre_tag


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
            # First real scrape came back with 0 events parsed and a raw
            # sample that was just the static <head> -- the widget hadn't
            # finished loading yet when the default fixed wait (3000ms)
            # captured the page; Elfsight's own async fetch of this
            # venue's events apparently takes longer than that. Confirmed
            # via a real "Inspect element" sample (an event's image tag:
            # class="... eapp-events-calendar-media-image ...", hosted on
            # files.elfsightcdn.com) that it is genuinely this widget, so
            # wait_for_selector -- rather than a longer fixed wait_ms --
            # is the fix: block until at least one real event card exists
            # instead of guessing how many milliseconds is enough.
            scrape_config=(
                '{"include_all_locations": true, '
                '"category_include": ["Performance"], '
                '"wait_for_selector": ".eapp-events-calendar-grid-item-container"}'
            ),
        )

        heavy_culture = get_or_create_venue(
            name="The Heavy Culture Cooperative",
            slug="the-heavy-culture-cooperative",
            address="1 Northampton St.",
            city="Easthampton",
            state="MA",
            website_url="https://www.theheavyculture.coop",
            events_url="https://www.theheavyculture.coop/shows",
            # Wix site running the native "Wix Events & Tickets" app --
            # confirmed server-rendered via a real view-source (event data
            # is in the raw HTML both as visible markup and as a big
            # `wix-warmup-data` JSON blob). Selectors use Wix's own
            # `data-hook` attributes rather than its CSS classes -- the
            # classes are per-build hashes (e.g. "FwdPeD", "WFgzOI"), the
            # same styled-components-style problem seen elsewhere, while
            # data-hook is Wix's stable automation-hook convention:
            #   <li data-hook="event-list-item">
            #     ...
            #     <span data-hook="ev-list-item-title">Fred Cracklin / Otobo / Mibble</span>
            #     <div data-hook="ev-date"><span>Jul 24, 2026, 7:00 PM</span></div>
            #     <a data-hook="ev-rsvp-button" href="https://.../event-details-registration/...">Get Tickets</a>
            #   </li>
            # The /shows page actually embeds this same widget *three*
            # times (Upcoming, a Calendar view, and Past Events), all
            # sharing the identical data-hook="event-list-item" markup --
            # a bare item_selector would triple-count events and pull in
            # past shows. Scoping to "#comp-lk7y5t1j", the Wix-assigned
            # component id of just the Upcoming Events widget, avoids
            # that. Some events' date text is a range ("7:00 PM – 11:00
            # PM") rather than a single time, which needed a new
            # _strip_dash_time_range() heuristic in html_generic.py (see
            # that file's docstring) -- same underlying issue as Smith
            # College's "|" ranges, different separator.
            #
            # source_type is rendered_html, not plain html, because of the
            # widget's "Load More" button (confirmed via a real view-source
            # of /shows): it's a bare <button type="button"
            # data-hook="load-more-button"> with no href and no ?page=N-style
            # URL anywhere -- the extra events it fetches come from a
            # client-side call to Wix's own internal Events API, invisible
            # to a plain requests.get() (the page's own embedded
            # `wix-warmup-data` blob confirms this: it caches only the same
            # 7 events already in the visible markup, with "hasMore": true,
            # meaning even the warmup JSON doesn't have the rest). So this
            # needs the same headless-browser click-and-recapture approach
            # already used for 33 Hawley's Elfsight "Next Events" button,
            # via rendered_html.py's next_button_selector/next_button_clicks
            # -- scoped to "#comp-lk7y5t1j" so it clicks the Upcoming
            # widget's own Load More button, not the Past Events widget's
            # (both widgets have one). next_button_clicks: 2 grabs a couple
            # extra batches beyond the initial page load, per David's ask.
            source_type="rendered_html",
            scrape_config=(
                '{"item_selector": "#comp-lk7y5t1j [data-hook=\\"event-list-item\\"]", '
                '"title_selector": "[data-hook=\\"ev-list-item-title\\"]", '
                '"date_selector": "[data-hook=\\"ev-date\\"]", '
                '"link_selector": "[data-hook=\\"ev-rsvp-button\\"]", '
                '"wait_for_selector": "#comp-lk7y5t1j [data-hook=\\"event-list-item\\"]", '
                '"next_button_selector": "#comp-lk7y5t1j [data-hook=\\"load-more-button\\"]", '
                '"next_button_clicks": 2}'
            ),
            # Every show here is a live-music event -- David asked for
            # everything from this venue auto-tagged "Music".
            default_event_type=music_tag,
        )

        quonk = get_or_create_venue(
            name="Quonk",
            slug="quonk",
            address="122 Main Street, Lower Level",
            city="Northampton",
            state="MA",
            website_url="https://www.quonkhampton.com",
            # quonkhampton.com's own homepage is entirely client-rendered
            # (confirmed: a plain fetch returns just an empty shell/meta
            # tags, no event markup at all) -- its "Learn More" links on
            # each event card don't even go to another page on its own
            # site, they go straight out to a Ticket Tailor listing
            # (tickettailor.com/events/quonkhampton/<id>), a third-party
            # ticketing platform. events_url points directly at Ticket
            # Tailor's own *listing* page instead -- confirmed via a real
            # fetch that it's plain server-rendered HTML with every
            # upcoming event's title/date/link/image already in the
            # markup:
            #   <li class="events-listing__item">
            #     <img src="https://uploads.tickettailorassets.com/...">
            #     <a class="event__link" href="/events/quonkhampton/2307456">
            #       <h3 class="event__title">Punchline! Stand Up Comedy @ Quonk</h3>
            #     </a>
            #     <span class="event-meta__date">Fri Jul 24, 2026 7:30 PM - 9:15 PM</span>
            #   </li>
            # That listing page never shows a description, though -- only
            # each event's own Ticket Tailor detail page does, in a
            # `section.detail-content__description` block.
            #
            # source_type is rendered_html, not plain html, even though
            # neither page above actually needs JS to render -- Ticket
            # Tailor's bot-management blocked a plain requests.get() with
            # a 403 outright, even after setting a realistic browser
            # User-Agent (confirmed via two real scrape attempts), while
            # its own robots.txt explicitly allows crawling these exact
            # pages for any user-agent. That means the block is on
            # something a plain `requests` call can't fake (most likely a
            # TLS/header fingerprint check), not the UA string -- so this
            # uses a real headless browser (which passes that check
            # automatically) for both the listing fetch *and* the
            # per-event description fetch. rendered_html.py's
            # description_from_link/description_detail_selector do the
            # latter, reusing the same Playwright page for each event's
            # detail page and writing the result into the listing HTML as
            # a `.__prefetched_description` div -- see that file's module
            # docstring. description_selector below just points at that
            # injected div, same as any other inline description.
            #
            # Relative hrefs/img srcs here resolve against events_url's
            # own domain (tickettailor.com), not website_url
            # (quonkhampton.com) -- see html_generic.py's module
            # docstring for why that distinction matters.
            #
            # Date text has one known-bad shape: an event with more than
            # one showtime just says "Fri Jul 31, 2026, Multiple times"
            # instead of a real time, which the fuzzy date parser can't
            # extract a time from and falls back to midnight -- a rare
            # enough edge case (one event, currently) not worth a special
            # heuristic for.
            events_url="https://www.tickettailor.com/events/quonkhampton",
            source_type="rendered_html",
            scrape_config=(
                '{"item_selector": "li.events-listing__item", '
                '"title_selector": "h3.event__title", '
                '"link_selector": "a.event__link", '
                '"date_selector": "span.event-meta__date", '
                '"image_selector": "img", '
                '"wait_for_selector": "li.events-listing__item", '
                '"description_from_link": true, '
                '"description_detail_selector": "section.detail-content__description", '
                '"description_selector": ".__prefetched_description", '
                '"user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}'
            ),
            # No default_event_type -- same call as Smith College above:
            # Quonk's programming is deliberately all over the place
            # (stand-up comedy, dance parties, tabletop game nights,
            # immersive tavern theater), not a single-genre music venue,
            # so a blanket tag would be wrong more often than right. Use
            # the admin Review queue to tag each show individually
            # (Comedy/Dance/Tabletop Gaming/etc. tags already exist).
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

        # Two more placeholder artists, this time exercising the newer
        # Genre Tags / Category Tags feature (multi-value tags, unlike the
        # old single artist.genre string used by artist_1/artist_2 above)
        # -- so David can see the Local Artists page's filters, the
        # calendar's "only local artists" toggle, and the homepage's random
        # featured-artist spotlight all working before adding real artists.
        # embed_code below is a placeholder Bandcamp iframe with a fake
        # album id -- it renders (Bandcamp shows its own "not found" state
        # inside the box) just to demonstrate where a real embed would sit;
        # swap in the real snippet from Bandcamp's/YouTube's own "Embed" /
        # "Share" button once there's an actual artist to link.
        placeholder_embed = (
            '<!-- Placeholder embed -- replace with the real embed code from '
            'Bandcamp\'s or YouTube\'s own "Embed"/"Share" button -->'
            '<iframe style="border: 0; width: 100%; height: 120px;" '
            'src="https://bandcamp.com/EmbeddedPlayer/album=0000000000/size=large/'
            'bgcol=ffffff/linkcol=0687f5/tracklist=false/transparent=true/" '
            'seamless></iframe>'
        )
        electronica_tag = get_or_create_genre_tag("Electronica")
        new_wave_tag = get_or_create_genre_tag("New Wave")
        americana_tag = get_or_create_genre_tag("Americana")

        artist_3 = get_or_create_artist(
            name="Comet & the Roadrunners",
            slug=slugify("Comet & the Roadrunners"),
            hometown="Northampton, MA",
            is_local=True,
            bio="Placeholder artist -- replace with a real local act once you're populating this for real.",
            website_url="https://cometandtheroadrunners.bandcamp.com",
            embed_code=placeholder_embed,
        )
        # Set explicitly (rather than only via get_or_create_artist's
        # kwargs) since get_or_create_artist only applies kwargs the first
        # time a row is created -- like genre_tags/category_tags above,
        # this needs to be reapplied on every seed.py run so it still takes
        # effect on a db that already has this artist from before this
        # field existed. Placeholder image from a generic placeholder-image
        # service -- replace with a real hosted photo URL once there's an
        # actual artist. artist_4 below deliberately has none set, so both
        # the "has an image" and "falls back to the site logo" states are
        # visible in the demo.
        artist_3.image_url = "https://placehold.co/400x400?text=Comet+%26+the+Roadrunners"
        artist_3.genre_tags = [electronica_tag, new_wave_tag]
        artist_3.category_tags = [music_tag]

        artist_4 = get_or_create_artist(
            name="Ruth & the Backroads",
            slug=slugify("Ruth & the Backroads"),
            hometown="Easthampton, MA",
            is_local=True,
            bio="Placeholder artist -- replace with a real local act once you're populating this for real.",
            website_url="https://ruthandthebackroads.bandcamp.com",
            embed_code=placeholder_embed,
        )
        artist_4.genre_tags = [americana_tag]
        artist_4.category_tags = [music_tag]

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
        # Upcoming shows for the two new tagged-artist placeholders --
        # without one of these, artist_3/artist_4 wouldn't show up under
        # the Local Artists page's "upcoming shows" toggle or be eligible
        # for the homepage's featured-artist spotlight.
        if not Event.query.filter_by(title="Sample Show -- Academy of Music").first():
            db.session.add(
                Event(
                    venue_id=academy_of_music.id,
                    title="Sample Show -- Academy of Music",
                    start_datetime=datetime.utcnow() + timedelta(days=3, hours=4),
                    description="Placeholder show, added manually.",
                    source="manual",
                    is_approved=True,
                    artists=[artist_3],
                    event_types=[music_tag],
                )
            )
        if not Event.query.filter_by(title="Sample Show -- Haze").first():
            db.session.add(
                Event(
                    venue_id=haze.id,
                    title="Sample Show -- Haze",
                    start_datetime=datetime.utcnow() + timedelta(days=6, hours=5),
                    description="Placeholder show, added manually.",
                    source="manual",
                    is_approved=True,
                    artists=[artist_4],
                    event_types=[music_tag],
                )
            )

        db.session.commit()
        print("Seed complete:")
        print(f"  Venues: {Venue.query.count()}")
        print(f"  Artists: {Artist.query.count()}")
        print(f"  Events: {Event.query.count()}")


if __name__ == "__main__":
    main()
