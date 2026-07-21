"""One-off diagnostic: print the raw HTML around a couple of event items on
Smith College's events listing page, so we can see the actual CSS classes
Drupal is using (my sandbox can't reach smith.edu directly -- your machine
can). Run this locally:

    python3 check_smith.py

then paste the full output back into the chat.
"""
import re

import requests

URL = "https://www.smith.edu/news-events/events?page=0"

resp = requests.get(URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
print(f"status={resp.status_code} length={len(resp.text)}\n")

html = resp.text

# Find each occurrence of a title we saw in the rendered page, and print a
# window of raw HTML around it so we can see the surrounding tags/classes.
needle = "Holley Flagg"
positions = [m.start() for m in re.finditer(re.escape(needle), html)]
print(f"Found {len(positions)} occurrence(s) of {needle!r}\n")

for i, pos in enumerate(positions[:2]):
    start = max(0, pos - 900)
    end = min(len(html), pos + 900)
    print(f"----- window {i} (around char {pos}) -----")
    print(html[start:end])
    print()

# Also print the pagination markup so we can see the query param name.
pag_pos = html.find("page=1")
if pag_pos != -1:
    print("----- pagination markup -----")
    print(html[max(0, pag_pos - 400) : pag_pos + 200])
