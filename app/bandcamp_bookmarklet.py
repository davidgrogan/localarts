"""Builds the "Import from Bandcamp" bookmarklet's `javascript:` href.

See app/static/bandcamp_bookmarklet.js for the actual (readable) logic
and the full story of why this runs in the admin's own browser instead of
being a server-side fetch -- short version: Bandcamp serves a real CAPTCHA
to automated requests (confirmed live, not just a fingerprint check a
User-Agent/navigator.webdriver tweak could get past), so the only way to
pull this data at all is from a genuine, human-driven browser session.

BOOKMARKLET_JS below is the minified form of that same file (minified with
terser, `npx terser app/static/bandcamp_bookmarklet.js --compress --mangle`
-- re-run that and paste the result here if the readable source ever
changes; keeping a build step for one small file felt like more machinery
than it's worth for a project with no other JS at all). Verified
byte-for-byte behavior-equivalent to the readable source via a jsdom-based
test harness before being pasted in here.
"""
from urllib.parse import quote

BOOKMARKLET_JS = (
    r'''!async function(){async function t(t){try{const e=await fetch(t);if(!e.ok)return null;const n=await e.text();return(new DOMParser).parseFromString(n,"text/html")}catch(t){return null}}function e(t){const e={name:null,location:null,bio:null,tags:[]},n=t.querySelector("#band-name-location");if(n){const t=n.querySelector(".title"),r=n.querySelector(".location");t&&(e.name=t.textContent.trim()||null),r&&(e.location=r.textContent.trim()||null)}const r=t.querySelector("div.signed-out-artists-bio-text");if(r){const t=r.cloneNode(!0);t.querySelectorAll("script").forEach(t=>t.remove()),t.querySelectorAll("a").forEach(t=>{const e=(t.textContent||"").trim().toLowerCase();"more"!==e&&"less"!==e&&""!==e||t.remove()});const n=t.textContent.replace(/\s+/g," ").trim();e.bio=n||null}const a=t.querySelectorAll(".tralbum-tags a.tag");return e.tags=Array.from(a).map(t=>t.textContent.trim()).filter(Boolean),e}function n(t){const e=t.querySelector("script[data-tralbum]");if(!e)return null;let n;try{n=JSON.parse(e.getAttribute("data-tralbum"))}catch(t){return null}const r=n.current||{},a=n.album_release_date||r.publish_date;let o=null;if(a){const t=new Date(a);isNaN(t.getTime())||(o=t)}return{item_type:n.item_type,release_date:o,title:r.title}}const r=function(t){try{return new URL(t).origin}catch(t){return null}}(location.href);if(!r||-1===r.indexOf(".bandcamp.com"))return void alert("This bookmarklet only works on a Bandcamp band page (a *.bandcamp.com URL).");const a=await t(r+"/");if(!a)return void alert("Couldn't load "+r+" -- try again from the band's Bandcamp page.");const o=e(a);let l=[];const i=await t(r+"/music");if(i&&(l=function(t,e){const n=t.querySelector("#music-grid");if(!n)return[];const r=[],a=new Set;return n.querySelectorAll("a[href]").forEach(t=>{const n=t.getAttribute("href");if(!n||-1===n.indexOf("/album/"))return;let o;try{o=new URL(n,e+"/").href}catch(t){return}a.has(o)||(a.add(o),r.push(o))}),r}(i,r)),0===l.length){const t=n(a);t&&"album"===t.item_type&&(l=[r+"/"])}let c=null,u=null;const s=Math.min(l.length,25);for(let e=0;e<s;e++){const o=l[e],i=o===r+"/"?a:await t(o);if(!i)continue;const s=n(i);if(s&&"album"===s.item_type&&s.release_date&&(!c||s.release_date<c.release_date)){const t=i.querySelector('meta[property="og:url"]'),e=t&&t.getAttribute("content")||o;c=Object.assign({},s,{url:e}),u=i}}const m={name:o.name,location:o.location,bio:o.bio,suggested_tags:o.tags,image_url:null,embed_code:null,album_title:null,album_url:null,band_url:r+"/"};if(c&&u){const t=e(u);t.tags&&t.tags.length&&(m.suggested_tags=t.tags),!m.bio&&t.bio&&(m.bio=t.bio),!m.name&&t.name&&(m.name=t.name),!m.location&&t.location&&(m.location=t.location);const n=u.querySelector('meta[property="og:image"]'),r=u.querySelector('meta[property="og:video"]');if(n&&n.getAttribute("content")&&(m.image_url=n.getAttribute("content")),r&&r.getAttribute("content")){const t=c.title||"Listen on Bandcamp";m.embed_code='<iframe style="border: 0; width: 350px; height: 470px;" src="'+r.getAttribute("content")+'" seamless><a href="'+c.url+'">'+t+"</a></iframe>"}m.album_title=c.title,m.album_url=c.url}const d=JSON.stringify(m);try{await navigator.clipboard.writeText(d),alert((m.name?'Copied "'+m.name+'" ':"Copied ")+'from Bandcamp! Paste it into the "Paste from Bandcamp" box on the artist form.')}catch(t){window.prompt("Clipboard copy failed -- copy this manually (Ctrl/Cmd+C), then paste into the artist form:",d)}}();'''
)


def bookmarklet_href():
    """A "javascript:" URI safe to drop directly into an <a href="...">
    with no further HTML-escaping needed -- urllib.parse.quote() percent-
    encodes every character that isn't alphanumeric/`_.-~/` (by default),
    which covers all of `"`, `'`, `<`, `>`, `&`, `{`, `}`, spaces, etc.
    -- so the result contains no characters Jinja's autoescaping would
    otherwise need to touch."""
    return "javascript:" + quote(BOOKMARKLET_JS)
