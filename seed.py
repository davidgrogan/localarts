"""Seed the database with a starter venue, a few local artists, and a
couple of sample shows so the calendar isn't empty on first run.

Safe to re-run: looks up by slug/title before inserting.

Usage:
    python seed.py
"""
import os
from datetime import timedelta

from app import create_app
from app.models import db, Venue, Artist, Event
from app.utils import slugify, get_or_create_event_type, get_or_create_genre_tag, local_now


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
        #
        # is_public_category=True on all eight of these: the curated set
        # that actually appears as a toggleable pill on the public
        # calendar's filter bar (see app/routes/main.py's category
        # filter and app/routes/events.py's manage_categories() admin
        # page), as opposed to an internal-only tag an admin quick-adds
        # while editing one event (e.g. the pre-existing "karaoke" tag),
        # which stays off the public filter bar unless someone
        # deliberately promotes it there. This flag is only ever set on
        # a brand-new row -- re-running this seed script never flips it
        # back on for a category an admin has since turned off, or vice
        # versa (see get_or_create_event_type()'s own docstring).
        music_tag = get_or_create_event_type("Music", is_public_category=True)
        get_or_create_event_type("Comedy", is_public_category=True)
        get_or_create_event_type("Theater", is_public_category=True)
        get_or_create_event_type("Spoken Word", is_public_category=True)
        get_or_create_event_type("Lectures", is_public_category=True)
        get_or_create_event_type("Art Exhibits", is_public_category=True)
        film_tag = get_or_create_event_type("Film", is_public_category=True)
        get_or_create_event_type("Dance", is_public_category=True)

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
            #
            # "user_agent" override added after this venue started reliably
            # timing out (not a 403 -- a plain connection-then-read hang, 15s
            # every time) on html_generic.py's default self-identifying UA
            # ("...LocalMusicSitePOC/0.1"). That symptom -- connects fine,
            # then silently stalls instead of rejecting outright -- is
            # consistent with a WAF/bot-mitigation product tarpitting a
            # request it's flagged as automated, the same underlying problem
            # Quonk's Ticket Tailor listing and Bombyx's Ludus install hit
            # (see those write-ups in README.md), just a different-shaped
            # failure than their flat 403s. A real Chrome UA is the same fix
            # applied there.
            source_type="html",
            scrape_config=(
                '{"item_selector": ".event_card", '
                '"title_selector": ".event_card_title h5", '
                '"date_selector": ".event_card_date_times", '
                '"link_selector": ".event_card_details_button a", '
                '"description_selector": ".event_card_presents", '
                '"image_selector": ".event_card_image img", '
                '"user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}'
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
            # squarely in the "Paradise City Music" area David books/attends in.
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

        bombyx = get_or_create_venue(
            name="BOMBYX Center for Arts & Equity",
            slug="bombyx-center-for-arts-and-equity",
            address="130 Pine St.",
            city="Northampton",
            state="MA",
            # bombyx.live is this venue's own marketing site -- its actual
            # ticketing/event listing lives on a separate Ludus
            # (ludus.com) install instead, confirmed via the real site.
            website_url="https://bombyx.live",
            events_url="https://bombyx.ludus.com/index.php",
            # See app/scrapers/ludus.py's module docstring for the full
            # confirmed DOM structure (.show_item > .showtimes_item,
            # data-show-id/data-showtime-id, etc.). A first real scrape
            # attempt with a plain requests.get() came back a flat 403
            # Forbidden -- the same shape of block Quonk's Ticket Tailor
            # listing hit (see that write-up below), almost certainly a
            # TLS/header fingerprint check rather than a UA-string one.
            # fetch_raw() now reuses rendered_html.py's Playwright-based
            # fetch (headless Chromium + automation-hiding flags) instead,
            # which is why wait_for_selector/user_agent below are set even
            # though source_type is "ludus", not "rendered_html" -- see
            # ludus.py's module docstring for why it borrows that one
            # function but keeps its own bespoke parse().
            source_type="ludus",
            # This is a genuine multi-use community arts space (dance
            # classes, grant-writing workshops, speed networking, theater)
            # sharing the same Ludus listing as its real concerts -- David
            # only wants live music pulled in automatically, the same
            # tradeoff as 33 Hawley above. category_include filters to
            # shows whose visible category pill contains "Concert". Real
            # tradeoff seen in a live listing: a couple of obviously-music
            # events ("Noho Music Presents: Summer Jam '26", "Choro Camp
            # 2026") had NO category pill at all and would be silently
            # excluded by this filter -- worth a look at the scrape
            # preview once this runs for real, and loosening/removing
            # category_include if that's happening more than rarely.
            scrape_config=(
                '{"category_include": ["Concert"], '
                '"wait_for_selector": ".show_item", '
                '"user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}'
            ),
            default_event_type=music_tag,
        )

        cityspace = get_or_create_venue(
            name="CitySpace",
            slug="cityspace",
            address="43 Main St.",
            city="Easthampton",
            state="MA",
            website_url="https://www.cityspaceeasthampton.org",
            # The venue's own homepage-linked events page -- content lives
            # entirely behind a client-side hash route (#/events) rendered
            # by an embedded VenuePilot widget, confirmed via live browser
            # inspection: a plain fetch of this URL only ever returns the
            # empty WordPress page shell, and network traffic shows the
            # real listing comes from a POST to www.venuepilot.co/graphql
            # (introspection disabled, undocumented) rather than anything
            # in the page's own initial HTML. See app/scrapers/
            # venuepilot.py's module docstring for the full confirmed DOM
            # shape (.vp-event-row, .vp-month-n-day, etc.) and why this
            # renders the real page in a headless browser instead of
            # reverse-engineering that API.
            events_url="https://www.cityspaceeasthampton.org/all-events/#/events",
            source_type="venuepilot",
            scrape_config=(
                '{"wait_for_selector": ".vp-event-row"}'
            ),
            # No default_event_type -- CitySpace is Old Town Hall's shared
            # community space, not a dedicated music venue: the same
            # listing mixes real Blue Room concerts in with ECA Gallery
            # art openings, a monthly building tour, a pop-up market, and
            # a volunteer day, with no visible category field on the
            # listing page to tell them apart programmatically (same
            # tradeoff as Quonk/Smith College above -- see those
            # comments). Every newly scraped event lands untagged and off
            # the public Music-only calendar until tagged "Music" by hand
            # via the Review queue. venuepilot.py's title_exclude config
            # key is there if it's worth permanently dropping the
            # recurring non-music filler (it repeats close to monthly)
            # once that's confirmed rather than reviewing/discarding the
            # same handful of listings every scrape.
        )

        amherst_college = get_or_create_venue(
            name="Amherst College",
            slug="amherst-college",
            address="220 South Pleasant St.",
            city="Amherst",
            state="MA",
            website_url="https://www.amherst.edu",
            # Confirmed Drupal Views (body class "not-logged-in", each
            # event an <article class="mm-calendar-event">) via live DOM
            # inspection -- a plain requests.get() sees the same
            # server-rendered markup, no headless browser needed.
            events_url="https://www.amherst.edu/news/events/calendar",
            source_type="html",
            # David asked for only events explicitly marked "Open to the
            # Public" -- this campus calendar mixes those in with a much
            # larger volume of students-only/internal events (roughly
            # 1-in-6 on a real page, confirmed live), with no way to tell
            # them apart except a small flag/badge element per event.
            # require_selector (added to html_generic.py this session)
            # only keeps items containing a
            # ".mm-event-listing-flag.open-to-the-public" match -- an
            # event carrying that flag *alongside* another one (e.g.
            # "Tickets Required", "Registration Required" -- confirmed
            # both appear on real events) is still kept; only
            # "Students Only" or no flag at all gets filtered out.
            #
            # date_selector targets the event's own schema.org
            # <meta itemprop="startDate" content="2026-...-04:00">
            # rather than any visible text -- Amherst College's calendar
            # doesn't print a plain-text date/time on the listing at all
            # (only "TUE, AUG 25, 2026" as a *label*, not machine-
            # readable) -- date_attr="content" reads that meta tag's
            # attribute instead of get_text(). Its ISO string carries an
            # explicit -04:00/-05:00 UTC offset that html_generic.py's
            # new _to_local() converts to naive America/New_York time
            # (same reasoning as the Amherst Cinema/One Amber Lane fixes
            # earlier this session), rather than relying on the printed
            # offset always exactly matching what zoneinfo computes.
            #
            # No default_event_type -- like CitySpace above, "open to the
            # public" here spans lectures, concerts, receptions, and
            # exhibits, not just music, so scraped events land untagged
            # in the Review queue for an admin to tag by hand.
            #
            # page_param/max_pages: the "SHOW MORE EVENTS" link at the
            # bottom of the page is a plain '?_page=1', '?_page=2', ...
            # Views pager URL (confirmed via direct navigation, not an
            # AJAX-only control) -- a request past the last real page
            # comes back 200 with zero events rather than an error, so
            # over-fetching is harmless. Each page covers roughly 3
            # weeks (25 raw events/page, ~1-in-6 of which are actually
            # "Open to the Public"); max_pages=4 covers about 10-12
            # weeks forward, similar in spirit to Amherst Cinema's own
            # 21-day default -- just wider, given the low keep-rate here.
            scrape_config=(
                '{"item_selector": "article.mm-calendar-event", '
                '"title_selector": "h2.mm-event-listing-title", '
                '"date_selector": "meta[itemprop=\\"startDate\\"]", '
                '"date_attr": "content", '
                '"description_selector": "div.mm-event-listing-description", '
                '"image_selector": "img", '
                '"require_selector": ".mm-event-listing-flag.open-to-the-public", '
                '"page_param": "_page", '
                '"max_pages": 4}'
            ),
        )

        umass_amherst = get_or_create_venue(
            name="UMass Amherst",
            slug="umass-amherst",
            address="Amherst, MA",
            city="Amherst",
            state="MA",
            website_url="https://www.umass.edu",
            # David supplied this exact URL, pre-filtered server-side to
            # "Public/Everyone" events via its event_types[] param
            # (confirmed by the page's own <title>, "Public/Everyone
            # Events on ...") -- unlike Amherst College's calendar, which
            # mixes public and students-only/internal events on one page
            # with no way to filter except a client-side flag, no
            # require_selector is needed here at all -- the URL itself
            # already does that filtering.
            events_url=(
                "https://events.umass.edu/calendar/day?order=date&days=365"
                "&experience=&event_types%5B%5D=50764271776749"
            ),
            source_type="html",
            # Confirmed server-rendered (a same-origin fetch(location.href)
            # found real event text in the raw response, no headless
            # browser needed) -- a "Localist"-style university-calendar
            # CMS ("em-"-prefixed class names throughout), built on a
            # genuinely clean date source: each event's
            # .em-card_event-date holds two <em-local-time start="..."/>
            # custom elements (start, then end), each carrying a full ISO
            # 8601 timestamp with an explicit UTC offset (e.g.
            # "2026-09-01T07:30:00-04:00") -- date_attr="start" reads that
            # directly off the *first* one via select_one() (the start
            # time), no free-text date parsing needed at all. Cleanest
            # date source of any venue in this project so far.
            #
            # Real numbered pagination ("Page 1 ... 5, Next page"), but
            # the page number lives in the URL *path*
            # (/calendar/day/2, /calendar/day/3, ...), not a query
            # string -- html_generic.py's existing page_param can't
            # express that, so this session added page_url_template (see
            # that module's own docstring) specifically for this case.
            # max_pages=5 covers every page confirmed live (21 events/page,
            # 100+ total across the year-ahead window the days=365 param
            # requests).
            #
            # No genre_selector -- each card does carry its own category
            # tag (".em-card_text > a.em-card_tag", deliberately scoped to
            # exclude the separate "New" badge "span.em-new-tag", which
            # shares the em-card_tag class) but it's a campus-event
            # category ("Reception/Open House", "Lecture", etc.), not a
            # music genre -- same call as Amherst College's own entry
            # above, left for an admin to tag by hand via Review instead
            # of mislabeling it as a music genre.
            #
            # No default_event_type -- like Amherst College/CitySpace,
            # "public" here spans lectures, receptions, athletics, and
            # exhibits as well as actual music/performance events, so
            # everything lands untagged in the Review queue.
            scrape_config=(
                '{"item_selector": ".em-card", '
                '"title_selector": ".em-card_title a", '
                '"date_selector": ".em-card_event-date em-local-time", '
                '"date_attr": "start", '
                '"image_selector": ".em-card_image img.img_card", '
                '"page_url_template": "https://events.umass.edu/calendar/day/'
                '{page}?order=date&days=365&experience=&event_types%5B%5D='
                '50764271776749", '
                '"max_pages": 5}'
            ),
        )

        amherst_cinema = get_or_create_venue(
            name="Amherst Cinema",
            slug="amherst-cinema",
            address="28 Amity St.",
            city="Amherst",
            state="MA",
            website_url="https://amherstcinema.org",
            # The homepage's own showtimes calendar is a hover-to-preview
            # widget, but that's purely a UI skin -- confirmed via live
            # browser inspection that every day cell links to its own
            # real, plain server-rendered URL:
            # amherstcinema.org/calendar/month/<YYYY-MM-DD>, showing the
            # exact same markup the hover popup does. events_url here is
            # that URL's *base* (with no trailing date) -- app/scrapers/
            # amherst_cinema.py's fetch_raw() appends "/<date>" once per
            # day across its scrape window. No headless browser needed at
            # all, unlike Quonk/Heavy Culture/CitySpace/Bombyx -- a plain
            # requests.get() of each day-page returns full server-rendered
            # HTML.
            events_url="https://amherstcinema.org/calendar/month",
            source_type="amherst_cinema",
            # One Event row per film per *day* (not per showtime) -- a
            # single film can run 3-4 showtimes a day for a week or more,
            # and modeling each showtime as its own row would flood the
            # calendar with 15-20 entries a day for this one venue alone.
            # All of a day's times land in the Event's description
            # instead (see amherst_cinema.py's _build_description()); the
            # calendar card itself only reflects the day's first showing.
            # Known, deliberate simplification -- not a bug.
            scrape_config='{"days_ahead": 21}',
            # Every listing here is a film screening -- auto-tag "Film"
            # the same way music venues auto-tag "Music".
            default_event_type=film_tag,
        )

        one_amber_lane = get_or_create_venue(
            name="One Amber Lane",
            slug="one-amber-lane",
            # The site itself (oneamberlane.com) never prints its own
            # street address -- its nav "location" link points straight
            # to a Google Maps place, and Maps currently has this
            # location filed under an older name, "Iconica Social Club"
            # (same building/coordinates). The listing's own displayed
            # business name and address there are "Amber Lane Café &
            # Pub" / "1 Amber Ln, Northampton, MA 01060", which is what's
            # used here.
            address="1 Amber Ln",
            city="Northampton",
            state="MA",
            website_url="https://oneamberlane.com",
            # Confirmed Squarespace (body class fingerprint) -- reuses
            # the same app/scrapers/squarespace_json.py module already
            # built for Iron Horse, completely unmodified aside from
            # this session's assetUrl -> image_url addition (see that
            # module's parse() -- Squarespace's own image CDN serves
            # these asset URLs already-absolute with no hotlink
            # protection, unlike Amherst Cinema's poster images, so no
            # re-hosting step is needed here). Confirmed via a live
            # ?format=json fetch: 36 real event candidates came back in
            # the exact shape squarespace_json.py's _find_event_
            # candidates() already expects (title/startDate/endDate/
            # fullUrl/assetUrl/etc.), all live-music listings (e.g.
            # "Andie Arel Live Music", "B-Town Trio Live Music").
            events_url="https://oneamberlane.com/events",
            source_type="squarespace_json",
            # Every listing seen here is a live-music show -- auto-tag
            # "Music" the same way Iron Horse/Parlor Room/etc. do.
            default_event_type=music_tag,
        )

        diy_venue = get_or_create_venue(
            name="DIY",
            slug="diy",
            # Deliberately no address/city/state -- this is a shared
            # catch-all "venue" for one-off DIY shows (house shows,
            # backyard sets, basement gigs) submitted through the public
            # "Submit your show" form (app/routes/gigs.py), not a real
            # physical location of its own. The actual address/location
            # each submitter enters isn't lost, though -- per David's call,
            # it's copied into the converted Event's own description field
            # during conversion (see events.py's _gig_prefill()) rather
            # than given its own Venue/Event column, since the specific
            # place is different every time but should still all read as
            # "DIY" in venue listings/filtering.
            source_type="manual",
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
        # These four placeholder shows exist so the calendar/featured-artist
        # spotlight have something to show before any real scrape/manual add
        # has happened. They used to be inserted with a one-time "only if no
        # row with this title exists yet" guard, which meant their
        # start_datetime was set exactly once, the very first time seed.py
        # ran on a given database, and never touched again. That's exactly
        # why the homepage's featured-artist spotlight can quietly vanish
        # weeks/months later: _pick_featured_artist() (main.py) only
        # considers an artist with a *future* approved show, and these
        # placeholder shows' "N days from now" dates were computed relative
        # to whatever "now" was on that first run -- once enough real time
        # passes, every one of them silently rolls into the past and no
        # longer counts, with nothing surfacing an error since an empty
        # candidate list is a perfectly normal, silent "don't show the
        # spotlight" state. Fetching by title and refreshing start_datetime
        # (and is_approved, in case one was manually rejected while testing)
        # on every run -- same "reapply on every seed.py run" pattern
        # already used above for artist_3/artist_4's tags -- keeps these
        # useful as an always-current demo instead of a one-time snapshot.
        #
        # That refresh-on-every-run behavior had its own bug, though: once
        # an admin actually deletes one of these placeholder rows for real
        # (e.g. "Sample Show -- Haze", once real Haze content is in), the
        # very next `python seed.py` run saw "no row with this title" and
        # cheerfully recreated it from scratch -- indistinguishable, from
        # seed.py's point of view, from a genuinely fresh install that's
        # never seen this title before. A deleted-on-purpose placeholder
        # kept coming back forever. `_SAMPLE_SHOWS_MARKER` (a plain empty
        # file next to the sqlite db, in the same `instance/` folder that
        # already holds per-install state) records "seed.py has placed
        # these placeholders at least once on this install" -- once that
        # marker exists, a missing title means "deleted on purpose," not
        # "never created," and _upsert_sample_show leaves it alone instead
        # of recreating it. Existing rows still get their dates refreshed
        # every run exactly as before; only the recreate-if-missing branch
        # is now gated on this being a genuinely first-ever run.
        _SAMPLE_SHOWS_MARKER = os.path.join(app.instance_path, ".sample_shows_seeded")
        _sample_shows_seeded_before = os.path.exists(_SAMPLE_SHOWS_MARKER)

        def _upsert_sample_show(title, venue_id, days_out, hours_out, artists, event_types=None):
            event = Event.query.filter_by(title=title).first()
            if not event:
                if _sample_shows_seeded_before:
                    # Already placed once on this install and no longer
                    # exists -- an admin deleted it on purpose. Respect that.
                    return None
                event = Event(title=title, venue_id=venue_id, source="manual")
                db.session.add(event)
            # local_now(), not datetime.utcnow() -- keeps these placeholder
            # shows' "N days from now" dates consistent with how every real
            # start_datetime is compared elsewhere (see app/utils.py's
            # SITE_TIMEZONE docstring).
            event.start_datetime = local_now() + timedelta(days=days_out, hours=hours_out)
            event.description = event.description or "Placeholder show, added manually. Delete once real scraped/added shows are in."
            event.is_approved = True
            event.artists = artists
            if event_types is not None:
                event.event_types = event_types
            return event

        _upsert_sample_show("Sample Show -- Iron Horse", iron_horse.id, 5, 2, [artist_1])
        _upsert_sample_show("Sample Show -- Parlor Room", parlor_room.id, 9, 3, [artist_2])
        # Upcoming shows for the two tagged-artist placeholders -- without
        # one of these, artist_3/artist_4 wouldn't show up under the Local
        # Artists page's "upcoming shows" toggle or be eligible for the
        # homepage's featured-artist spotlight.
        _upsert_sample_show("Sample Show -- Academy of Music", academy_of_music.id, 3, 4, [artist_3], [music_tag])
        _upsert_sample_show("Sample Show -- Haze", haze.id, 6, 5, [artist_4], [music_tag])

        os.makedirs(app.instance_path, exist_ok=True)
        open(_SAMPLE_SHOWS_MARKER, "a").close()

        db.session.commit()
        print("Seed complete:")
        print(f"  Venues: {Venue.query.count()}")
        print(f"  Artists: {Artist.query.count()}")
        print(f"  Events: {Event.query.count()}")


if __name__ == "__main__":
    main()
