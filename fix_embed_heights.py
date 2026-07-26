"""One-time fix for the real cause of "a lot of white space between the
embedded player and Upcoming shows" on an artist's profile page.

Root cause: the "Import from Bandcamp" bookmarklet (app/static/
bandcamp_bookmarklet.js) was hardcoding `height: 470px` on the generated
<iframe> -- that's the right height for Bandcamp's full "large, with
tracklist" embed player, but the bookmarklet actually points the iframe at
Bandcamp's og:video URL, which is Bandcamp's own compact social-share/
link-preview embed (just cover art + a play button, no tracklist). Wrapping
that short player in a 470px-tall box left a large empty void *inside the
iframe itself*, below the actual player -- not a CSS margin/spacing issue
at all, which is why the earlier CSS-only attempts at this didn't help.

The bookmarklet itself is already fixed to generate `height: 120px` going
forward (matching the height already used by seed.py's own placeholder
embed, which points at the same compact Bandcamp embed style). This script
retroactively fixes any artist whose embed_code was saved *before* that
fix, by rewriting "height: 470px" to "height: 120px" wherever it appears.
Safe to run more than once -- it only touches rows that still say 470px.

Run it the same way you run seed.py:

    python3 fix_embed_heights.py
"""
import sys

sys.path.insert(0, ".")

from app import create_app
from app.models import db, Artist

OLD = "height: 470px"
NEW = "height: 120px"

app = create_app()
with app.app_context():
    affected = Artist.query.filter(Artist.embed_code.ilike(f"%{OLD}%")).all()
    if not affected:
        print("No artists found with the old 470px embed height -- nothing to do.")
    else:
        for artist in affected:
            before = artist.embed_code
            artist.embed_code = before.replace(OLD, NEW)
            print(f"Fixed embed_code for {artist.name!r} (artist id={artist.id})")
        db.session.commit()
        print(f"\nDone -- updated {len(affected)} artist(s).")
