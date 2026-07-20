# Deploying to a DigitalOcean Droplet

This assumes: you already have a DigitalOcean account, and this project is
(or will be) pushed to a GitHub repo you control. Steps 1-2 happen in your
own GitHub/DO accounts -- I can't create accounts, add payment methods, or
authenticate as you, so those parts are on you. Everything after that is a
straight copy/paste-able walkthrough.

## 0. Push this project to GitHub (one time)

From your own machine, in this project folder:

```bash
git init
git add .
git commit -m "Initial commit"
```

Then on github.com, create a new **empty** repo (no README/license -- this
project already has one), and push:

```bash
git remote add origin https://github.com/<you>/<repo-name>.git
git branch -M main
git push -u origin main
```

## 1. Create the droplet

DigitalOcean dashboard -> **Create -> Droplets**:
- Image: **Ubuntu 24.04 LTS**
- Plan: cheapest "Basic" shared-CPU droplet ($6/mo tier is plenty for this
  traffic level) to start -- you can resize later if it gets slow.
- Region: pick one near Northampton, MA (e.g. New York) for lower latency.
- Auth: SSH key (upload your public key) rather than a password.

Note the droplet's public IPv4 address once it's created.

## 2. Create a managed Postgres database

DigitalOcean dashboard -> **Create -> Databases** -> PostgreSQL, cheapest
plan, same region as the droplet. Once it's provisioned, DO gives you a
full connection string under **Connection details** -- copy it, you'll
need it in step 6. Under the database's **Settings -> Trusted Sources**,
add your droplet so only it (not the whole internet) can connect.

(A managed database costs a bit more than just running Postgres on the
droplet itself, but you get automatic backups and don't have to maintain
it -- worth it for a low-traffic proof of concept that you don't want to
lose data on.)

## 3. Point a domain at it (optional but recommended)

If you have a domain, add an **A record** pointing it (or a subdomain
like `shows.yourdomain.com`) at the droplet's IP. Skip this and use the
raw IP if you don't have a domain yet -- you can add HTTPS later once you
do.

## 4. SSH in and do basic setup

```bash
ssh root@YOUR_DROPLET_IP

apt update && apt upgrade -y
apt install -y python3-venv python3-pip git nginx libpq-dev ufw

# Basic firewall: only SSH, HTTP, HTTPS
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw enable

# A dedicated non-root user to run the app under (matches the systemd
# units in deploy/, which have User=localmusic)
adduser --disabled-password --gecos "" localmusic
su - localmusic
```

## 5. Clone the repo and install dependencies

Still as the `localmusic` user:

```bash
git clone https://github.com/<you>/<repo-name>.git local-music-poc
cd local-music-poc

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Playwright needs its own browser binary plus some system libraries
# (fonts, libgbm, etc.) -- this installs both in one go.
playwright install --with-deps chromium
```

## 6. Configure environment variables

```bash
cp deploy/local-music.env.example deploy/local-music.env
nano deploy/local-music.env
```

Fill in:
- `DATABASE_URL` -- the connection string from step 2 (DO's format is
  `postgresql://user:password@host:port/dbname?sslmode=require` --
  copy it exactly, including `?sslmode=require`).
- `SECRET_KEY` -- generate one: `python3 -c "import secrets; print(secrets.token_hex(32))"`

This file holds real secrets and is already gitignored -- never commit it.

## 7. Create the tables and seed starter data

```bash
set -a; source deploy/local-music.env; set +a
python3 -c "from app import create_app; create_app()"   # creates tables
python3 seed.py                                          # seeds venues/artists
deactivate
exit   # back to root
```

## 8. Install the systemd services

Back as `root`:

```bash
cp /home/localmusic/local-music-poc/deploy/local-music.service /etc/systemd/system/
cp /home/localmusic/local-music-poc/deploy/scrape.service /etc/systemd/system/
cp /home/localmusic/local-music-poc/deploy/scrape.timer /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now local-music.service
systemctl enable --now scrape.timer

# Sanity checks
systemctl status local-music.service
curl -I http://127.0.0.1:8000
```

If `local-music.service` fails to start, `journalctl -u local-music -n 50`
will show the actual error (usually a bad DATABASE_URL or a missing
package).

## 9. Set up nginx (and HTTPS if you have a domain)

```bash
cp /home/localmusic/local-music-poc/deploy/nginx.conf.example /etc/nginx/sites-available/local-music
nano /etc/nginx/sites-available/local-music   # replace YOUR_DOMAIN_HERE

ln -s /etc/nginx/sites-available/local-music /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

If you have a domain pointed at the droplet:

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d YOUR_DOMAIN_HERE
```

Certbot rewrites the nginx config to add HTTPS and sets up auto-renewal.
Without a domain, skip this and just browse to `http://YOUR_DROPLET_IP`.

## Redeploying after future changes

```bash
su - localmusic
cd local-music-poc
git pull
source .venv/bin/activate
pip install -r requirements.txt   # only if requirements.txt changed
deactivate
exit
systemctl restart local-music.service
```

## Before this is truly public

The admin routes (`/venues/*`, `/events/new`, `/events/review`, etc.) have
no authentication at all right now -- anyone who finds the URL can
add/edit/delete venues, artists, and shows. Worth adding basic auth (or a
real login) in front of those routes before sharing the URL widely; this
was already flagged as a next step in README.md and matters more now that
it's live on the internet rather than just running on your laptop.
