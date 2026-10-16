# Diagnostic - Identifying the target's defenses

Before writing any scraper, you must know what you are scraping and what protects it. The diagnostic phase typically takes 15–30 minutes and saves days of wrong-tool implementation. This page is the procedure.

The ground rule, again: **the diagnostic happens without exposing the local IP**. Use the user's browser DevTools (their machine, their session, their problem if anything goes wrong), a managed API as a probe, or fixtures captured during a previous deployed run. Never `curl` or `fetch` the target from the local shell.

---

## What you are looking for

The diagnostic answers six questions. Get all six before choosing a tool.

1. Does an internal API exist?
2. Is content static (server-rendered HTML) or dynamic (JS-rendered)?
3. What anti-bot vendor (if any) protects the site?
4. What proxy type will work? (Datacenter / ISP / residential / mobile)
5. What is the request-volume budget?
6. What is the data shape and crawl topology?

## Diagnostic toolbox

Before opening DevTools, install or open these. They turn 30 minutes of guessing into 5 minutes of reading.

- **Wappalyzer** browser extension - detects the tech stack of any site you visit, including anti-bot vendors. One click on the extension icon and you see "Cloudflare Bot Management", "Akamai Bot Manager", "DataDome", "PerimeterX/HUMAN", along with the CDN, framework, and analytics. This is the fastest possible vendor identification. Install it once; use it every time.
- **The user's own browser DevTools** - Network tab for endpoint discovery, Application tab for cookies/storage state, Console for JavaScript errors that reveal challenge logic.
- **CreepJS** (`creepjs.com`) and **FingerprintJS demo** - these test your *own* fingerprint. Useful in two ways: (1) understand what data anti-bot collects from you; (2) when you build a custom browser config, validate it produces a believable fingerprint by running it through these test pages.
- **Firehol IP lists** (`iplists.firehol.org`) and **PixelScan** - verify that a proxy IP isn't already on a public spam blocklist. Useful when a proxy provider sells "fresh" IPs that turn out to be poisoned.
- **DNS leak test** (`dnsleaktest.com`) - confirms that a proxy isn't leaking your real DNS server. A proxy that hides your IP but leaks DNS queries is half-broken.

## The "watch yourself do it first" principle

Before writing any goal or scraper, navigate to the target as a human. Click around. Notice what loads instantly, what loads after scrolling, what triggers the cookie banner, where the data appears, how pagination works. The mental script of "how would a human accomplish this task?" is also the script of how the scraper should behave.

This applies especially to behavioral-detection-heavy sites. The first time the user sees Wine-Searcher fail with a direct deep link, the fix isn't more proxies - it's recognizing that a human starts at the homepage and searches. Reproducing the human path is the bypass.

Three minutes of human navigation reveal:
- The actual entry point (homepage vs. category vs. search)
- The cookie banner behavior (does it block content until dismissed?)
- The order of network requests (what loads first? what's deferred?)
- Whether content needs interaction (scroll, click, hover) to appear
- What real navigation timing looks like (humans don't load pages in 50 ms)

Document this as the "human script" and use it as the reference for scraper behavior.

---

## Question 1 - Does an internal API exist?

**This is the most important question.** It changes the project from "scraper" to "API client" and reduces cost by 10–100×. See `hidden-apis.md` for the full discovery procedure. The short version:

In the user's browser, open DevTools → Network tab → filter by `XHR` / `Fetch`. Reproduce the user-facing action (search, filter, paginate). If you see a request to `/api/...` returning JSON, you have your endpoint. The Vivino pseudo-API (`/api/explore/explore`, `/api/wines/{id}/reviews`) was found this way and replaced what would have been a Camoufox scraper with a lightweight Cheerio scraper.

Common patterns that indicate an internal API:
- Requests to `/api/v1/...`, `/graphql`, `/_next/data/...`, `/wp-json/...`
- Embedded JSON in `<script id="__NEXT_DATA__">`, `window.__INITIAL_STATE__`, `window.__APOLLO_STATE__`
- Mobile app endpoints (often `/m.example.com/api/...` or different subdomain)

Check the mobile site too. Mobile endpoints are often less protected than desktop ones.

---

## Question 2 - Static or dynamic?

Test: fetch the URL with a basic HTTP client (via a managed API or fixture) and search the returned HTML for the data you want.

- **Found in raw HTML** → static. Use HTTP-only crawling.
- **Not found, but found in `__NEXT_DATA__` or similar** → still static, just embedded. Parse the script tag's JSON.
- **Not found anywhere in the initial HTML** → dynamic. Browser required.

Many sites that look dynamic are actually static for the data you need. Modern Next.js / Nuxt sites server-render the critical content even when the page feels SPA-like. Always test before assuming you need a browser.

---

## Question 3 - Anti-bot vendor identification

Each vendor leaves signatures. Identify them in this order:

### Cloudflare

Signatures (any one is sufficient):
- `Set-Cookie: __cf_bm=...` or `cf_clearance=...` in response headers
- `Server: cloudflare` in response headers
- HTML page containing "Checking your browser" or "Just a moment..."
- Requests to `challenges.cloudflare.com`
- 5-second delay before content loads
- HTTP 403 with body referencing Cloudflare

Configurations vary widely. Same vendor, very different difficulty:
- "I'm under attack mode" - hard, requires a real browser + clean IP
- "Bot fight mode" - moderate, browser + residential usually enough
- Default WAF - light, sometimes HTTP+headers is enough

### Akamai Bot Manager

Signatures:
- `Set-Cookie: _abck=...` or `bm_sz=...` or `ak_bmsc=...`
- HTML "Pardon Our Interruption" page
- Sensor data POSTs to `/_bm/...` or `/akamai/...`

Akamai is harder than baseline Cloudflare. Real browser + residential is the baseline; advanced configurations need engine-level stealth.

### PerimeterX / HUMAN Security

Signatures:
- `Set-Cookie: _px=...`, `_pxhd=...`, `_pxvid=...`
- POST to `/_pxapi/`, `/_pxhd/`, `/captcha.px-cdn.net`
- HTML page with PerimeterX challenge
- Heavy reliance on behavioral analysis (mouse, scroll timing)

Used by LinkedIn, Indeed, Wine-Searcher (at the time of writing). Very hard. Camoufox or managed API.

### DataDome

Signatures:
- `Set-Cookie: datadome=...`
- `X-DataDome-CID` response header
- Challenge page from `geo.captcha-delivery.com`
- HTTP 403 with JSON body containing `{"url": "...captcha..."}`

DataDome behavioral analysis is sophisticated. Browser + residential + behavioral mimicry, or managed API.

### AWS WAF

Signatures:
- `Server: awselb` or `Server: cloudfront`
- `aws-waf-token` cookie
- Challenge page hosted on `awswaf.com`

Generally lighter than the dedicated vendors, but configurations vary.

### Imperva (Incapsula)

Signatures:
- `Set-Cookie: incap_ses_*` or `visid_incap_*`
- `X-Iinfo` response header
- `X-CDN: Incapsula` header

### Kasada

Signatures:
- `Set-Cookie: KP_UIDz=...` or `x-kpsdk-ct=...` headers
- Heavy obfuscated JavaScript loaded from `*.kpsdk.io` or similar
- HTTP 429 with body containing Kasada-specific challenge code
- A `proof.js` or similarly-named challenge script that blocks subsequent requests until executed

Bank, ticketing, and high-value e-commerce. Real browser execution is mandatory; simple HTTP fails immediately.

### Shape Security (F5 Distributed Cloud)

Signatures:
- Headers like `X-D-Token`, `X-CD-Token` on auth/checkout endpoints
- Sensor data POSTs to obfuscated paths (often randomized per deployment)
- Used heavily on banking, airline booking, ticketing

Most active around login and payment flows; public-content scraping faces a lighter version.

### Regional vendors

- **YandexCaptcha / Yandex SmartCaptcha** - Russian sites (`.ru`). Cookie names like `_ym_uid`, integration with `passport.yandex.*`.
- **GeeTest** - Chinese sites (`.cn`). Slider/click puzzles. Script loaded from `*.geetest.com`.
- **Qrator** - Russian DDoS/anti-bot. `Server: QRATOR` or similar in response headers.
- **FriendlyCaptcha** - privacy-focused. Cryptographic puzzle, lightweight. Common on EU sites.

### "Naive" rate limiting (no vendor)

Signatures:
- HTTP 429 after N requests with `Retry-After` header
- HTTP 403 from same IP after burst
- No challenge cookies, no JS challenge page
- Often a single load balancer (`Server: nginx`, `Server: Apache`)

This is the easy case. Datacenter proxies + sessions + reasonable concurrency solves it.

### How to probe without exposing the local IP

Three valid approaches:

1. **Have the user open the site in their own browser** (their session, their IP). They check DevTools and report what they see. Best for first-time targets.

2. **Use a managed API** (Firecrawl, ZenRows) to fetch the URL once and return the headers + body. The managed API exposes its own IP, not the user's. This is a one-shot diagnostic call, not the production scraper.

3. **Use a previously captured fixture.** If the user has run a similar scraper before and saved an HTML response, that fixture contains all the signatures you need.

Never `curl` from the local shell. Even one request flagged by the target's anti-bot can poison the user's home IP for that domain for weeks.

### Browser preview → likely cause table

When the user (or a managed API) shows you what the rendered page looks like, the visible content is a strong vendor signal:

| What the rendered page shows | Likely cause |
|---|---|
| "Checking your browser" interstitial, ~5 second delay | Cloudflare JS challenge (default WAF) |
| "Just a moment..." with cf-mitigated header | Cloudflare Bot Fight Mode or higher |
| Popup or redirect to `geo.captcha-delivery.com` | DataDome challenge |
| "Pardon Our Interruption" full-page block | Akamai sensor data check |
| Blank page with infinite spinner | JS fingerprint detection or bot-blocking JS that prevents render |
| reCAPTCHA / hCaptcha / Cloudflare Turnstile widget | Challenge gate; effectively a hard limit unless solved |
| HTTP 403 with plain "Access Denied" body | IP-level block or User-Agent block |
| HTTP 429 with Retry-After header | Naive rate limit |
| Login page when content was expected | Session-based bot detection |
| Page loaded but empty result fields, no error | Silent block (Akamai or PerimeterX returning fake content) - schema validation in the scraper catches this |
| Content present in user's browser, missing in scraper response | Server-side cloaking based on header/TLS fingerprint |

Important consequence: **a successful HTTP run (status 200) is not a successful scrape.** Anti-bot systems often return 200 with empty/fake content rather than a 403. The scraper must validate that the expected fields are present (schema check) and treat empty-but-200 the same as a block.

---

## Question 4 - Proxy type

| Anti-bot detected | Proxy needed |
|---|---|
| None / naive rate limiting | Datacenter (on Apify: `SHADER` group) |
| Cloudflare default WAF, light Akamai, light DataDome | Datacenter often works; escalate to ISP or residential on first 403 |
| Cloudflare aggressive, Akamai standard, PerimeterX, DataDome standard | Residential (Apify: `RESIDENTIAL` group) with country matching |
| LinkedIn, Amazon, banking, ticketing | Residential premium or ISP/mobile via specialized provider |
| Google SERP | A SERP-routed proxy pool (Apify: `GOOGLE_SERP` group - specifically routed and warm) |

The proxy tiers, from cheapest and most-blocked to most-expensive and least-blocked:

- **Datacenter** - IPs owned by cloud providers (AWS, GCP, DigitalOcean). Cheap, fast, abundant. Whole subnets and ASNs are publicly known and pre-blacklisted by major anti-bot vendors. Even brand-new datacenter IPs can be flagged on arrival.
- **ISP proxies** - static residential IPs hosted in a datacenter. Halfway between datacenter and residential: they look like residential to most checks but are stable enough for sticky sessions. Less common but cheaper than full residential at moderate trust levels.
- **Residential** - IPs from real consumer ISPs, served via consenting users on a peer-to-peer network. Look like normal household traffic. Slower, more expensive (usually billed per GB rather than per IP). The default for protected targets.
- **Mobile** - IPs from carrier networks with carrier-grade NAT. Thousands of users share the same public address; abusive traffic affects everyone on it, but blocking it is also rare because of the collateral. Most expensive and least predictable.

Country code matters. Geo-restricted content (prices, listings) requires the proxy to be in the target country. Mismatched country between proxy and request often triggers blocks even when the IP is otherwise clean. Geographic consistency is also checked across the session: an IP that appears in Canada then Singapore mid-session is itself a flag.

---

## Question 5 - Volume budget

Volume drives every other choice. Calculate before architecting:

- **Up to 100 requests/day**: any tool works. Pick the cheapest.
- **100–10 000 requests/day**: HTTP-only is strongly preferred. Browser overhead becomes painful at this volume.
- **10 000+ requests/day**: HTTP-only is mandatory unless target requires a browser. If browser is unavoidable, plan for residential proxy budget and concurrency tuning.
- **100 000+/day on hard targets**: managed API economics start to dominate DIY. Compare per-call cost.

Know the unit cost per result before architecting. If a search call costs $0.005 in compute + proxy, that is your break-even floor. If you monetize the scraper (e.g. on Apify), price with margin above that floor - aim for 3–5× the underlying cost.

---

## Question 6 - Data shape and crawl topology

Three common topologies:

### Single-page extraction

Input: a list of URLs. Output: one record per URL.
Examples: product detail pages, profile pages, article pages.
Crawler: simple `requestHandler`, no `enqueueLinks`.

### Search → detail (two-level)

Input: a search query. Output: records from listing pages, optionally enriched from detail pages.
Crawler: two route handlers (`SEARCH`, `DETAIL`), `enqueueLinks` from search results.

### Site-wide crawl

Input: a starting URL. Output: every page matching a pattern.
Crawler: aggressive `enqueueLinks` with globs, sitemap parsing if available.
Honeypot risk: highest here. Always check for `display:none` / `visibility:hidden` links and skip them. Crawlee's link extractor handles this; manual implementations must replicate the check.

The topology determines the Crawlee class, the routing structure, and the request queue strategy. Get this right before writing any code.

---

## The diagnostic output

After 15–30 minutes you should have a one-page summary like this:

```
Target: example.com
Internal API: NO (no XHR returning JSON for the data we need)
Rendering: SERVER-SIDE (data found in initial HTML)
Anti-bot vendor: Cloudflare default WAF (cf_clearance cookie, no challenge page)
Proxy needed: datacenter, escalate to residential on 403
Volume target: 5 000 results/day
Topology: search → detail (two routes)
Tool: CheerioCrawler with sessions + datacenter proxies, escalation plan to residential
Risk: Cloudflare may upgrade to Bot Fight Mode → fallback to PlaywrightCrawler with default fingerprints
Estimated cost: ~$0.0008 per result (if monetizing on Apify, price with margin)
```

This is the artifact that goes into the project's docs before any source file is written. It is also what tells you, three months later, why you made each choice.

---

## When the diagnostic is wrong

Diagnostics are educated guesses, not certainties. The first deployed run is also a diagnostic. If the deployed run fails:

- 403 immediately on first request → IP-level block. Switch proxy group up one rung.
- 200 then 403 after N requests → rate limit or session burn. Reduce concurrency, rotate sessions more aggressively.
- 200 with empty body → silent block (especially Akamai). Look for missing critical sections in the HTML. Switch to browser.
- 200 with fake data → behavioral block (rare but real). Switch to browser with humanization.
- 429 with `Retry-After` → naive rate limit. Honor the header, reduce volume.
- Timeout → could be naive (overloaded site) or sophisticated (silent drop). Add retries with backoff first; if persistent, escalate.

Update the diagnostic after each failure. Rebuilding from a wrong diagnostic is faster than patching forever.
