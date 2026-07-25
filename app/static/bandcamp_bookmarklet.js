/*
 * "Import from Bandcamp" bookmarklet -- readable source.
 *
 * This is NOT loaded by the app itself (Bandcamp CAPTCHA-walls automated
 * fetches -- see README.md's "Import from Bandcamp URL" section for the
 * full story of why the server-side version of this feature was
 * abandoned). Instead, this same logic runs inside the admin's own real
 * browser session while they're already looking at a band's Bandcamp
 * page: click the bookmarklet, it fetches the band's pages using the
 * browser's own same-origin fetch() (real cookies, real browsing
 * fingerprint -- nothing about this looks automated to Bandcamp, because
 * it isn't), extracts the same fields the old server-side module did,
 * and copies the result to the clipboard as JSON for pasting into the
 * "Paste from Bandcamp" box on the artist form (see artists/form.html).
 *
 * app/bandcamp_bookmarklet.py minifies and URL-encodes this same logic
 * into the actual "javascript:" bookmarklet link rendered on the artist
 * form -- keep the two in sync if this file changes. (Deliberately kept
 * as a plain .js file here, not run through any build step -- this
 * project has no JS bundler/build pipeline, and one bookmarklet doesn't
 * need one.)
 */
(async function () {
  function originOf(href) {
    try {
      return new URL(href).origin;
    } catch (e) {
      return null;
    }
  }

  async function fetchDoc(url) {
    try {
      const resp = await fetch(url);
      if (!resp.ok) return null;
      const text = await resp.text();
      return new DOMParser().parseFromString(text, "text/html");
    } catch (e) {
      return null;
    }
  }

  // Mirrors app/bandcamp_import.py's _extract_band_info() -- same
  // selectors, same "more"/"less" toggle-link stripping logic (those
  // ship as plain class-less <a>more</a> elements, confirmed against
  // real Bandcamp markup, so matched by text rather than any selector).
  function extractBandInfo(doc) {
    const info = { name: null, location: null, bio: null, tags: [] };

    const bnl = doc.querySelector("#band-name-location");
    if (bnl) {
      const titleEl = bnl.querySelector(".title");
      const locationEl = bnl.querySelector(".location");
      if (titleEl) info.name = titleEl.textContent.trim() || null;
      if (locationEl) info.location = locationEl.textContent.trim() || null;
    }

    const bioEl = doc.querySelector("div.signed-out-artists-bio-text");
    if (bioEl) {
      const clone = bioEl.cloneNode(true);
      clone.querySelectorAll("script").forEach((s) => s.remove());
      clone.querySelectorAll("a").forEach((a) => {
        const txt = (a.textContent || "").trim().toLowerCase();
        if (txt === "more" || txt === "less" || txt === "") a.remove();
      });
      const bioText = clone.textContent.replace(/\s+/g, " ").trim();
      info.bio = bioText || null;
    }

    const tagEls = doc.querySelectorAll(".tralbum-tags a.tag");
    info.tags = Array.from(tagEls)
      .map((t) => t.textContent.trim())
      .filter(Boolean);

    return info;
  }

  // Mirrors _read_tralbum() -- reads the same <script data-tralbum="...">
  // JSON blob every Bandcamp release page embeds.
  function readTralbum(doc) {
    const script = doc.querySelector("script[data-tralbum]");
    if (!script) return null;
    let data;
    try {
      data = JSON.parse(script.getAttribute("data-tralbum"));
    } catch (e) {
      return null;
    }
    const current = data.current || {};
    const raw = data.album_release_date || current.publish_date;
    let releaseDate = null;
    if (raw) {
      const d = new Date(raw);
      if (!isNaN(d.getTime())) releaseDate = d;
    }
    return { item_type: data.item_type, release_date: releaseDate, title: current.title };
  }

  // Mirrors _find_album_urls() -- every /album/... link in the band's
  // /music grid, standalone /track/... links excluded.
  function findAlbumUrls(doc, origin) {
    const grid = doc.querySelector("#music-grid");
    if (!grid) return [];
    const urls = [];
    const seen = new Set();
    grid.querySelectorAll("a[href]").forEach((a) => {
      const href = a.getAttribute("href");
      if (!href || href.indexOf("/album/") === -1) return;
      let full;
      try {
        full = new URL(href, origin + "/").href;
      } catch (e) {
        return;
      }
      if (!seen.has(full)) {
        seen.add(full);
        urls.push(full);
      }
    });
    return urls;
  }

  const MAX_RELEASES_TO_CHECK = 25;

  const origin = originOf(location.href);
  if (!origin || origin.indexOf(".bandcamp.com") === -1) {
    alert("This bookmarklet only works on a Bandcamp band page (a *.bandcamp.com URL).");
    return;
  }

  const rootDoc = await fetchDoc(origin + "/");
  if (!rootDoc) {
    alert("Couldn't load " + origin + " -- try again from the band's Bandcamp page.");
    return;
  }
  const bandInfo = extractBandInfo(rootDoc);

  let releaseUrls = [];
  const musicDoc = await fetchDoc(origin + "/music");
  if (musicDoc) releaseUrls = findAlbumUrls(musicDoc, origin);
  if (releaseUrls.length === 0) {
    // Some single-release bands have no separate /music grid at all --
    // the domain root redirects straight to their one release.
    const rootTralbum = readTralbum(rootDoc);
    if (rootTralbum && rootTralbum.item_type === "album") {
      releaseUrls = [origin + "/"];
    }
  }

  let earliest = null;
  let earliestDoc = null;
  const checkCount = Math.min(releaseUrls.length, MAX_RELEASES_TO_CHECK);
  for (let i = 0; i < checkCount; i++) {
    const url = releaseUrls[i];
    const doc = url === origin + "/" ? rootDoc : await fetchDoc(url);
    if (!doc) continue;
    const tralbum = readTralbum(doc);
    if (!tralbum || tralbum.item_type !== "album" || !tralbum.release_date) continue;
    if (!earliest || tralbum.release_date < earliest.release_date) {
      const ogUrlEl = doc.querySelector('meta[property="og:url"]');
      const canonicalUrl = (ogUrlEl && ogUrlEl.getAttribute("content")) || url;
      earliest = Object.assign({}, tralbum, { url: canonicalUrl });
      earliestDoc = doc;
    }
  }

  const result = {
    name: bandInfo.name,
    location: bandInfo.location,
    bio: bandInfo.bio,
    suggested_tags: bandInfo.tags,
    image_url: null,
    embed_code: null,
    album_title: null,
    album_url: null,
    band_url: origin + "/",
  };

  if (earliest && earliestDoc) {
    const albumInfo = extractBandInfo(earliestDoc);
    if (albumInfo.tags && albumInfo.tags.length) result.suggested_tags = albumInfo.tags;
    if (!result.bio && albumInfo.bio) result.bio = albumInfo.bio;
    if (!result.name && albumInfo.name) result.name = albumInfo.name;
    if (!result.location && albumInfo.location) result.location = albumInfo.location;

    const ogImage = earliestDoc.querySelector('meta[property="og:image"]');
    const ogVideo = earliestDoc.querySelector('meta[property="og:video"]');
    if (ogImage && ogImage.getAttribute("content")) result.image_url = ogImage.getAttribute("content");
    if (ogVideo && ogVideo.getAttribute("content")) {
      const albumTitle = earliest.title || "Listen on Bandcamp";
      result.embed_code =
        '<iframe style="border: 0; width: 350px; height: 470px;" src="' +
        ogVideo.getAttribute("content") +
        '" seamless><a href="' +
        earliest.url +
        '">' +
        albumTitle +
        "</a></iframe>";
    }
    result.album_title = earliest.title;
    result.album_url = earliest.url;
  }

  const json = JSON.stringify(result);
  try {
    await navigator.clipboard.writeText(json);
    alert(
      (result.name ? 'Copied "' + result.name + '" ' : "Copied ") +
        'from Bandcamp! Paste it into the "Paste from Bandcamp" box on the artist form.'
    );
  } catch (e) {
    window.prompt("Clipboard copy failed -- copy this manually (Ctrl/Cmd+C), then paste into the artist form:", json);
  }
})();
