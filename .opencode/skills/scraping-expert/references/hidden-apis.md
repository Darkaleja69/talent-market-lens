# Hidden APIs - The First Move

The single highest-leverage technique in scraping is discovering that the site you are about to scrape is already serving its own data via a clean JSON endpoint. Every modern web app talks to a backend; that traffic is observable. Spending 20 minutes finding the internal API beats 200 hours fighting anti-bot to scrape the rendered HTML.

This applies even when an official API exists. Public APIs are rate-limited, paywalled, or feature-poor. The internal endpoints used by the site's own frontend are usually richer, faster, and free.

---

## Why this matters

Three concrete examples:

- **Vivino** - endpoints `/api/explore/explore`, `/api/wines/{id}`, `/api/wines/{id}/reviews`, `/api/wines/{id}/tastes`, `/api/wines/{id}/prices`. No HTML scraping needed for 95% of the data. Runs as a lightweight Cheerio scraper. Without this discovery, the project would be a 4 GB Camoufox scraper at 10× the cost.
- **Most e-commerce sites** - product detail pages call `/api/v1/products/{id}` returning the full product JSON, including stock, prices, variants. The HTML is a thin shell over this.
- **Mobile app APIs** - many sites have a mobile-app backend on a different subdomain (`m.example.com/api/...`, `mobile-api.example.com/...`) with looser anti-bot.

The principle: **scrape the source of truth, not the rendering of it**.

---

## Discovery procedure

The procedure runs in your browser. The local shell never makes a request. The output is a list of endpoints with their parameters.

### Step 1 - Open DevTools on the target

Open the site in Chrome/Firefox, open DevTools (F12), and switch to the **Network** tab. Filter by **Fetch/XHR**. This hides static assets and shows only the API traffic.

### Step 2 - Reproduce the user-facing action

Perform the action you want to scrape: search a term, filter results, paginate, open a detail page. Each user action should generate one or more `Fetch/XHR` requests. These are your candidate endpoints.

For paginated lists, scroll/click to page 2 - the pagination parameter (`page`, `offset`, `cursor`, `after`) reveals itself in the diff between page 1 and page 2 requests.

### Step 3 - Identify the canonical endpoint

For a given user action, you usually see 5–20 requests. Most are tracking, ads, analytics. Filter mentally for endpoints that:
- Return JSON (Response tab → look for `{...}`)
- Have a path that suggests data (`/api/`, `/graphql`, `/_next/data/`, `/wp-json/`, `/services/`)
- Carry the data you actually want (look at the Response panel)

Right-click the request → **Copy as cURL**. This gives you the full request including headers, cookies, and parameters - verbatim. It is the starting point for your client implementation.

### Step 4 - Strip to minimum viable headers

Most browser requests carry 15–30 headers, most of which are unnecessary. Reduce the cURL to the minimum set that still works. Test by removing headers one at a time (do this in your browser with a tool like Postman or Insomnia, not from the local shell).

The headers that almost always matter:
- `User-Agent` - must be present and realistic
- `Accept` - usually `application/json`
- `Accept-Language` - affects content language and sometimes behavior
- `Referer` - many APIs check it; copy it from the real request

The headers that often matter:
- `X-Requested-With: XMLHttpRequest` - flags the request as AJAX, sometimes required
- `Authorization: Bearer ...` - if present, you need the auth flow
- Site-specific headers (`X-API-Key`, `X-CSRF-Token`, `X-Vivino-Client`) - investigate origin

The headers that almost never matter (drop them):
- `Sec-Fetch-*`
- `Sec-Ch-Ua-*`
- `DNT`, `Upgrade-Insecure-Requests`
- Most cookies (test with a clean session)

### Step 5 - Document the endpoint

Capture the spec like this:

```
ENDPOINT: GET /api/explore/explore
BASE URL: https://www.vivino.com
DESCRIPTION: Search/filter wines by criteria

REQUIRED HEADERS:
  User-Agent: Vivino/8.18.12 CFNetwork/1404.0.5 Darwin/22.3.0  (or modern browser UA)
  Accept: application/json
  Accept-Language: en

QUERY PARAMETERS:
  country_code      string   ISO-2, drives prices and availability
  currency_code     string   ISO-3, e.g. EUR, USD
  min_rating        float    1.0–5.0
  max_rating        float    1.0–5.0
  price_range_min   float    in currency_code units
  price_range_max   float
  wine_type_ids[]   int[]    1=Red, 2=White, 3=Rosé, 4=Sparkling, 7=Dessert, 24=Fortified
  region_ids[]      int[]    e.g. 383=Bordeaux
  grape_ids[]       int[]    e.g. 1=Cabernet Sauvignon
  page              int      1-indexed
  per_page          int      max ~50
  order_by          string   ratings_count | price | ratings_average
  order             string   asc | desc

RESPONSE SHAPE:
  { "explore_vintage": { "matches": [ { "vintage": {...}, "price": {...} } ], "records_matched": int } }

PROTECTION: lightweight, no JS challenge, datacenter proxies sufficient at moderate volume.

CACHE TTL: 30 days (cache key pattern: vivino-explore:{md5(query_string)})
```

This is the artifact that goes into the project. It belongs in `references/` of the scraper's repo.

---

## Patterns by site stack

### Next.js sites

Next.js sites embed prefetched data in `<script id="__NEXT_DATA__" type="application/json">`. To extract:

```js
const $ = cheerio.load(html);
const data = JSON.parse($('#__NEXT_DATA__').html());
const props = data.props.pageProps;
```

This often contains the full server-side data without a separate API call. For paginated/dynamic data, also check for `/_next/data/<buildId>/<route>.json` - the same endpoint Next.js uses internally for client-side navigation.

### Nuxt.js sites

Nuxt embeds data in `window.__NUXT__`. Less standardized than Next.js but the principle is the same: the data is in the page, just not in the visible DOM.

### React without SSR

Pure client-rendered React apps fetch from real APIs after page load. The DevTools approach (Step 2) finds these. Look for axios/fetch calls. Many use a single GraphQL endpoint.

### GraphQL endpoints

Detection: requests to `/graphql` with a POST body containing `{"query": "...", "variables": {...}}`.

Approach: copy the exact query string and variables from a successful browser request. GraphQL queries are persistable - once you have a working query, you have it forever (until the schema changes). Many sites also accept persisted queries by hash, which is even more stable.

### WordPress sites

WordPress exposes a REST API at `/wp-json/wp/v2/posts`, `/wp-json/wp/v2/pages`, etc. Most WP sites have this enabled by default. Look for `<link rel="https://api.w.org/" href="..."/>` in the page head.

### Shopify, Magento, WooCommerce

Each has a documented public API surface that's often left open. Shopify: `/products.json`. Magento: `/rest/V1/products`. WooCommerce: `/wp-json/wc/v3/products` (often requires keys, sometimes not).

### Mobile apps

The mobile apps of major sites talk to APIs that are usually less protected than the web frontend. Tools like `mitmproxy` can intercept this traffic (do this on your own machine, not the local agent shell). Once intercepted, the headers and endpoints are usable from any HTTP client.

The Vivino headers in the example above are mobile-app headers - `Vivino/8.18.12 CFNetwork/...` - extracted from the iOS app traffic.

---

## When there is no internal API

Sometimes the site really does render everything server-side and there is no JSON anywhere. In that case, fall back to HTML scraping with `CheerioCrawler` or `PlaywrightCrawler`. Confirm this conclusion before accepting it - most modern sites have *something* JSON-flavored even when it's not obvious.

Also check:
- The mobile site (`m.example.com`)
- The mobile-app backend (different subdomain)
- The SPA's source maps (sometimes leaked, reveal the API client code)
- Old or deprecated API versions (`/api/v1/`, `/old-api/`) that may still respond
- Search the site's GitHub if open-source (some companies publish their internal API client)

---

## Risks of internal API scraping

Internal APIs are not contracts. They can change without notice, break compatibility, or add anti-bot protection. Mitigations:

- **Cache aggressively.** A cache layer (KV Store on Apify; Redis / Postgres / disk elsewhere) with a 30-day TTL means you don't notice an API change for 30 days, which is enough time to react.
- **Schema validation.** Validate the JSON against a Zod (TS) or Pydantic (Python) schema. When the schema fails, you have a clear signal that the API changed; that becomes a dataset error record, not a silent corruption.
- **Have an HTML fallback.** For business-critical scrapers, keep a Cheerio fallback that parses the rendered HTML. Slower but resilient to API changes.
- **Monitor success rate.** Sudden drops in success rate are the leading indicator of an API change. A monitoring alert on success rate < N% (Apify monitoring, or any external uptime/metrics tool) catches this.

The risks are real but the leverage gain is so large that internal APIs remain the right default. Most stay stable for years.

---

## Authentication-gated APIs

Some internal APIs require a session cookie, a CSRF token, or a Bearer token. These add complexity but rarely block the project. Strategies:

### Cookie-based auth

Capture the session cookie from a logged-in browser. For the user's own account, this is fine and legal. For scraping data behind a login that isn't theirs, that crosses into authorized-access territory - see `legal-ethics.md`. Don't.

The cookie expires; refresh it via a login flow (using credentials stored as environment variables / secrets, never committed). The refresh flow uses `PlaywrightCrawler` (or a headless browser) to log in and extract the session cookie, which is then reused by `CheerioCrawler` (or a plain HTTP client) for the actual scraping.

### CSRF tokens

Often embedded in a `<meta name="csrf-token" content="...">` tag or in a hidden form field. Fetch the page once, extract the token, use it in subsequent POSTs.

### Bearer tokens (OAuth)

If the site uses a public OAuth flow (e.g., a developer API that the user has signed up for), use it normally. Don't try to extract Bearer tokens from someone else's session.

---

## The mindset shift

The instinctive question when scraping is "How do I get this HTML and parse it?". The expert question is "Where is this data actually stored, and what is the cleanest way to read it?". Internal APIs are usually that clean way. Always ask the expert question first.
