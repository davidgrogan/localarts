"""Run a scrape for every active, non-manual venue.

Used by the systemd timer in deploy/scrape.timer so scraping happens on a
schedule instead of only when someone clicks "Run scrape now" in the UI.
Safe to run by hand too:

    python3 scrape_all.py

New events land with is_approved=False (same review gate as the UI) --
this script never auto-publishes anything.
"""
from app import create_app
from app.models import Venue
from app.scrapers.base import run_scrape, ScrapeError


def main():
    app = create_app()
    with app.app_context():
        venues = Venue.query.filter(
            Venue.is_active.is_(True), Venue.source_type != "manual"
        ).all()

        if not venues:
            print("No active, scrapable venues found.")
            return

        for venue in venues:
            print(f"Scraping {venue.name} ({venue.source_type})...")
            try:
                run = run_scrape(venue, approve_new=False)
                print(
                    f"  {run.events_found} found, {run.events_created} new, "
                    f"{run.events_updated} updated."
                )
            except ScrapeError as exc:
                print(f"  FAILED: {exc}")
            except Exception as exc:  # noqa: BLE001 -- keep going on one bad venue
                print(f"  FAILED unexpectedly: {exc}")


if __name__ == "__main__":
    main()
