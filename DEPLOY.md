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

## Before this is truly public

The admin routes (`/venues/*`, `/events/new`, `/events/review`, etc.) have
no authentication at all right now -- anyone who finds the URL can
add/edit/delete venues, artists, and shows. Worth adding basic auth (or a
real login) in front of those routes before sharing the URL widely; this
was already flagged as a next step in README.md and matters more now that
it's live on the internet rather than just running on your laptop.
