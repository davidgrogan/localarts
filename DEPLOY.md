# Deploying to a DigitalOcean Droplet

This documents how this project is *actually* deployed: onto an existing
shared droplet (`waveyvibe.dev`) that already runs other apps behind
**Caddy**, mounted at a path (`/localarts`) rather than owning its own
domain, with Postgres installed directly on the droplet rather than a
separate managed database. If you're deploying to a brand new droplet
instead, the shape is similar but simpler -- skip anything below that
references "the existing app" or path-mounting, use nginx or Caddy per
your own preference, and consider a DO Managed Database for automatic
backups.

## 0. Push this project to GitHub (one time)

From your own machine, in this project folder:

```bash
git init
git add -A
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<you>/<repo-name>.git
git push -u origin main
```

(This repo is public -- `github.com/davidgrogan/localarts` -- since
nothing in it is secret; real secrets live only in `deploy/local-music.env`
on the droplet, which is gitignored and never committed.)

## 1. The droplet itself

Already existed: Ubuntu 24.04, running **Caddy** (not nginx) as the
reverse proxy in front of several small apps, each its own `server {}` /
path block in `/etc/caddy/Caddyfile`. Apps on this box live under
`/var/www/<name>` and run as **root** (matching the existing
`yt-playlist-podcaster` app) rather than a dedicated per-app user --
DEPLOY.md's systemd units follow that same convention.

Before doing anything else, check what's already running so you don't
collide with it:

```bash
sudo ss -tlnp                        # ports already in use
sudo cat /etc/caddy/Caddyfile        # how existing apps are wired in
```

## 2. Install Postgres directly on the droplet

No separate managed database for this deployment -- one more Postgres
instance was simpler than a second paid resource for a low-traffic hobby
box already running several such apps.

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib libpq-dev
sudo -u postgres psql -c "CREATE USER localarts WITH PASSWORD 'CHOOSE_A_REAL_PASSWORD';"
sudo -u postgres psql -c "CREATE DATABASE localarts OWNER localarts;"
```

Postgres listens on localhost only by default, so there's nothing to open
in the firewall for it.

## 3. Add swap space

This droplet has ~1GB RAM and no swap by default. Two of this app's
venues (Iron Horse, The Parlor Room) require launching headless Chromium
via Playwright to scrape, which can spike memory hard enough to make the
*entire droplet* (including unrelated apps) grind to a near-halt with no
swap to fall back on -- this actually happened once during setup. Don't
skip this step:

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -m   # confirm swap shows ~2048
```

## 4. Clone the repo and install dependencies

As root, under `/var/www/<name>` per this droplet's convention:

```bash
cd /var/www
git clone https://github.com/<you>/<repo-name>.git localarts
cd localarts

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Playwright needs its own browser binary plus some system libraries
# (fonts, libgbm, etc.) -- this installs both in one go.
playwright install --with-deps chromium
```

If `python3 -m venv` fails with an `ensurepip`/`externally-managed-environment`
error, install the matching venv package first (`sudo apt install
python3.12-venv` on Ubuntu 24.04) and try again.

## 5. Configure environment variables

```bash
cp deploy/local-music.env.example deploy/local-music.env
nano deploy/local-music.env
```

Fill in:
- `DATABASE_URL=postgresql://localarts:YOUR_PASSWORD@localhost:5432/localarts`
- `SECRET_KEY` -- generate one: `python3 -c "import secrets; print(secrets.token_hex(32))"`
- `SESSION_COOKIE_PATH` / `SESSION_COOKIE_NAME` -- already set to
  `/localarts` / `localarts_session` in the template. Only matters
  because this app shares a domain with sibling apps; leave these unset
  entirely if an app gets its own (sub)domain instead.
- `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` -- gates every management
  route behind a login. Generate the hash: `python3 -c "from
  werkzeug.security import generate_password_hash as g; print(g('your-real-password'))"`.
  Don't skip this one -- leaving it unset falls back to `admin`/`admin`.
- `MAIL_USERNAME` / `MAIL_PASSWORD` -- Gmail SMTP credentials for the
  contact form (a Gmail App Password, not the real password -- see
  README.md). `CONTACT_EMAIL` defaults to davidbgrogan@gmail.com if unset.

This file holds real secrets and is gitignored -- never commit it.

## 6. Create the tables and seed starter data

```bash
set -a; source deploy/local-music.env; set +a
python3 -c "from app import create_app; create_app()"   # creates tables
python3 seed.py                                          # seeds venues/artists
deactivate
```

Note for any *future* one-off debugging commands on this droplet (e.g.
inspecting the DB or a scraper directly): a plain interactive shell does
**not** automatically have `DATABASE_URL` set -- that only happens for
the actual systemd service via `EnvironmentFile=`. Re-run `set -a; source
deploy/local-music.env; set +a` in any new shell session before running
ad-hoc scripts, or they'll silently fall back to a fresh empty SQLite
database instead of erroring loudly.

## 7. Install the systemd services

```bash
cp /var/www/localarts/deploy/local-music.service /etc/systemd/system/
cp /var/www/localarts/deploy/scrape.service /etc/systemd/system/
cp /var/www/localarts/deploy/scrape.timer /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now local-music.service
systemctl enable --now scrape.timer

# Sanity checks
systemctl status local-music.service --no-pager
curl -I http://127.0.0.1:8000
```

If `local-music.service` fails to start, `journalctl -u local-music -n 50`
usually shows why (bad `DATABASE_URL`, missing package, etc.).

## 8. Wire it into Caddy at a path

See `deploy/Caddyfile.snippet.example` for the exact block and why it's
needed: `wsgi.py` wraps the app in Werkzeug's `ProxyFix` (`x_prefix=1`),
which is what makes `url_for()`/static links come out correctly prefixed
with `/localarts` once Caddy proxies that path segment to gunicorn --
without it, the app would generate root-relative links assuming it owned
the whole domain.

```bash
nano /etc/caddy/Caddyfile
```

Add, inside the relevant domain's block, **above** any `file_server`/
catch-all directive (Caddy tries blocks top-to-bottom):

```
handle_path /localarts/* {
    reverse_proxy localhost:8000 {
        header_up X-Forwarded-Prefix "/localarts"
    }
}
```

Then:

```bash
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
curl -sI https://YOUR_DOMAIN/localarts/
```

## 9. Add a homepage card (if applicable)

If, like `waveyvibe.dev`, the parent site is a separate static homepage
listing links to each app, add a card there pointing to `/localarts` --
see that project's own repo for its card markup/style.

## Moving locally-accumulated data to the droplet

If you keep adding venues and running scrapes on your local machine and
want that data on the droplet without re-entering it or re-scraping
everything there, use `migrate_to_postgres.py` (project root). It copies
every row from your local SQLite database into the droplet's Postgres,
preserving IDs and foreign keys.

It does **not** open Postgres to the internet -- the droplet's Postgres
stays localhost-only (as it should). Instead, tunnel to it over SSH:

```
ssh -L 5433:localhost:5432 root@YOUR_DROPLET_IP -N
```

Leave that running in its own terminal tab. Then, in another tab, from
this project folder with your local `.venv` active:

```
python3 migrate_to_postgres.py "postgresql://localarts:YOUR_PG_PASSWORD@localhost:5433/localarts"
```

It'll print row counts for each table and ask you to type `yes` before
doing anything.

**This is destructive to the target**: it truncates the droplet's
`venue`/`artist`/`event`/`event_artists`/`scrape_run` tables and replaces
them with your local data. Fine to run any time the droplet only has
whatever `seed.py` put there originally; don't run it if you've since
added real data on the live site's admin screens that isn't also in your
local database, or you'll lose it.

No need to restart `local-music.service` afterward -- it reads from the
database fresh on every request -- but reload the site to confirm it
looks right.

## Known gotchas hit during this deployment

- **Headless Chromium + low RAM + no swap = the whole box can hang.**
  Playwright-based scrapes (`elfsight_jsonld`, any `rendered_html` venue)
  can spike memory enough to make an unrelated app on the same droplet
  become unresponsive, without anything actually crashing or restarting
  (check `systemctl status caddy`'s uptime if this happens again --
  continuous uptime there means the droplet itself didn't reboot, it was
  just starved for memory). The swap file in step 3 is the fix; if it
  keeps happening, consider bumping the droplet's RAM instead.
- **Elfsight's widget can render a UTC-offset annotation inside the
  visible time element** (e.g. `7:00 PM<span> UTC-4</span>`), seemingly
  triggered by the rendering browser's system timezone not matching the
  venue's (a fresh Ubuntu droplet defaults to UTC; a Mac is usually
  already set to Eastern) -- this silently broke Iron Horse's scraped
  times to all show midnight once moved to the droplet. Fixed in
  `elfsight_jsonld.py` by regex-extracting the time substring instead of
  parsing the element's full text exactly; see that file's docstring.
- **`ensurepip`/`externally-managed-environment` errors** creating the
  venv mean the OS's `python3-venv` package isn't installed yet (Ubuntu
  24.04 ships Python without it) -- `apt install python3.12-venv` and
  retry, no need to work around it with `--break-system-packages`.

## Redeploying after future changes

```bash
cd /var/www/localarts
git pull
source .venv/bin/activate
pip install -r requirements.txt   # only if requirements.txt changed
deactivate
systemctl restart local-music.service
```

**If a change adds a new column to an existing model** (like `Event`),
that's *not* covered by the above on the droplet. Locally/SQLite,
`app/__init__.py`'s `_run_sqlite_column_migrations()` adds missing
columns automatically on startup -- but it explicitly skips Postgres
("use a real migration tool instead"), so on the droplet you need one
manual `ALTER TABLE` per new column before restarting the service, e.g.:

```bash
sudo -u postgres psql -d localarts -c 'ALTER TABLE event ADD COLUMN last_seen_at TIMESTAMP;'
sudo -u postgres psql -d localarts -c 'ALTER TABLE event ADD COLUMN missing_streak INTEGER DEFAULT 0 NOT NULL;'
sudo -u postgres psql -d localarts -c 'ALTER TABLE event ADD COLUMN needs_review BOOLEAN DEFAULT FALSE NOT NULL;'
sudo -u postgres psql -d localarts -c 'ALTER TABLE event ADD COLUMN review_note TEXT;'
sudo -u postgres psql -d localarts -c 'ALTER TABLE venue ADD COLUMN default_event_type_id INTEGER REFERENCES event_type(id);'
```

The event type tags feature also adds two brand-new tables (`event_type`,
`event_event_types`) -- those *are* covered by the normal `db.create_all()`
that runs on every app startup (it only skips *existing* tables' missing
columns, not missing tables entirely), so no manual step needed for those two.

(Check `app/__init__.py`'s `_COLUMN_MIGRATIONS` dict for the current
list of pending columns and their SQLite types -- Postgres types are
usually the same or close; `VARCHAR(n)`/`TEXT` are identical, `DATETIME`
becomes `TIMESTAMP`, `BOOLEAN DEFAULT 0` becomes `BOOLEAN DEFAULT FALSE`.)

## Admin login

The admin routes (`/venues/*`, `/events/new`, `/events/review`, artist
add/edit/delete) sit behind a single-admin login now -- see README.md's
"Admin login" section. The one thing that matters for deployment: set
real `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` values in
`deploy/local-music.env` (step 5) rather than leaving them unset, which
falls back to `admin`/`admin`. Generate the hash with:

```bash
python3 -c "from werkzeug.security import generate_password_hash as g; print(g('your-real-password'))"
```
