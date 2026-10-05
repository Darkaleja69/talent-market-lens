# Managed Scraping APIs - When to Pay Someone Else

When DIY economics break down - hardest targets, low volume, or when speed-to-market matters more than per-call cost - managed scraping APIs are the right answer. This page covers the major vendors, when to pick which, how to integrate them safely, and how to fail over between them.

The decision is not "DIY vs. managed." It's "which tool for which target." A production scraper portfolio uses both, often within the same scraper.

---

## When managed beats DIY

Use a managed API when:

1. **The target is pathological.** LinkedIn, Amazon at scale, ticketing sites, sneaker drops, banking. The arms race is full-time work; pay someone else to do it.
2. **Volume is low and DIY amortization is bad.** A scraper that runs 20 times a month doesn't justify maintaining a Camoufox + residential setup.
3. **Speed-to-market matters.** A managed API delivers a working scraper in hours; DIY for a hard target takes days.
4. **The user has burned credits.** Firecrawl, Bright Data, ZenRows credits sitting unused are sunk cost - use them.
5. **Compliance / liability concerns.** Some managed APIs have explicit ToS provisions for scraping; using them shifts some risk.

Avoid managed APIs when:

1. **High volume + cheap target.** Per-call costs add up. A 1M-call/month scraper at $0.005/call (managed) is $5K/month; at DIY datacenter cost it's $200.
2. **Internal API exists.** Don't pay a managed API to render HTML when the site serves JSON natively.
3. **Latency-sensitive workflows.** Most managed APIs add 5–60 seconds per call; for live use cases (chat agents, real-time pricing), this is too slow.
4. **Vendor lock-in matters.** Managed APIs change pricing and policy. A scraper built around vendor X's specific API is more painful to migrate than one using `got-scraping`.

---

## The vendor landscape (2026)

Five vendors cover most production cases. Each has a specialty.

### Firecrawl

**Specialty**: AI/LLM content extraction. Markdown output by default, schema-based structured extraction, crawling and mapping included.

**Strengths**:
- Best-in-class output for AI pipelines (clean markdown, no junk).
- Schema-based extraction with LLM under the hood.
- `/scrape`, `/crawl`, `/map`, `/extract`, `/search` - broad surface.
- MCP-ready (Firecrawl ships an official MCP server).

**Weaknesses**:
- Pricing is per-page-credit; complex extraction = more credits.
- Less control over the underlying browser config.
- Occasionally fails on extreme anti-bot (LinkedIn, ticketing).

**Use when**: building RAG/AI pipelines, need clean structured data from arbitrary URLs, doing content discovery + extraction in one step.

**Integration**:

```ts
// Firecrawl v2 SDK (package `firecrawl`). The v1 `@mendable/firecrawl-js`
// package and the `formats: ['extract']` + `extract:{}` shape are retired - // structured extraction is now the `json` format + `jsonOptions`.
import { Firecrawl } from 'firecrawl';

const firecrawl = new Firecrawl({ apiKey: process.env.FIRECRAWL_API_KEY });

// url is the first positional arg; options is the second.
const result = await firecrawl.scrape('https://www.example.com/article', {
  formats: ['markdown', 'json'],
  jsonOptions: {
    schema: {
      type: 'object',
      properties: {
        title: { type: 'string' },
        author: { type: 'string' },
        publishedDate: { type: 'string', format: 'date' },
      },
      required: ['title'],
    },
  },
});
// result.markdown, result.json
```

For batch operations, Firecrawl's `/crawl` returns a job ID; poll for completion. Cache the final result (KV Store on Apify; Redis / Postgres / disk on a standalone host).

### ZenRows

**Specialty**: General-purpose unblocking, especially Cloudflare and DataDome. Per-call pricing.

**Strengths**:
- Reliable on most anti-bot vendors.
- Simple HTTP API: pass a URL, get HTML.
- "Premium proxies" tier for hardest targets.
- Reasonable free tier for testing.

**Weaknesses**:
- Output is raw HTML (you parse). No built-in extraction.
- Premium proxies cost noticeably more than standard.
- Per-call rate-limited (varies by plan).

**Use when**: target has Cloudflare/DataDome and you want simple HTML pass-through, then parse with Cheerio.

**Integration**:

```ts
import { gotScraping } from 'got-scraping';

const response = await gotScraping({
  url: 'https://api.zenrows.com/v1/',
  searchParams: {
    apikey: process.env.ZENROWS_API_KEY,
    url: targetUrl,
    js_render: 'true',
    premium_proxy: 'true',
    proxy_country: 'us',
    wait_for: '.product-grid',  // wait for selector before returning
  },
});
const html = response.body;
const $ = cheerio.load(html);
// extract as usual
```

### ScrapFly

**Specialty**: Generalist, strong residential pool, ASP (anti-scraping protection) bypass tier.

**Strengths**:
- Detailed control over scraping config (country, ASP level, screenshot, etc.).
- Stable on Cloudflare, Akamai, DataDome.
- Good documentation.

**Weaknesses**:
- Pricing more complex (different rates per feature combination).
- Slightly higher latency than ZenRows on simple requests.

**Use when**: hard targets where you want fine control over the scrape config without managing browsers yourself.

### ScraperAPI

**Specialty**: Generalist, simple API, generous free tier.

**Strengths**:
- Lowest barrier to entry.
- Reasonable for moderate-difficulty targets.
- Good free-tier limits for prototyping.

**Weaknesses**:
- Less effective on the hardest targets compared to ZenRows/ScrapFly.
- Documentation is shallower.

**Use when**: prototyping, low-volume scrapes on moderate targets, or as a fallback in a multi-vendor failover.

### Bright Data Scraping Browser

**Specialty**: Premium managed Chromium accessible via CDP. The most expensive but most capable option.

**Strengths**:
- Connect via CDP - your Playwright/Puppeteer code runs unchanged.
- Built-in unblocking (proxies, fingerprints, CAPTCHA all handled by Bright Data).
- Reliable on the absolute hardest targets (LinkedIn, sneakers, tickets).

**Weaknesses**:
- Expensive per-call.
- Connection-based pricing model takes some understanding.
- Latency can be high (you're remote-driving a browser).

**Use when**: nothing else works. The target is genuinely hard and per-call cost is acceptable.

**Integration**:

```ts
import { chromium } from 'playwright';

const browser = await chromium.connectOverCDP(
  `wss://${process.env.BRIGHTDATA_USER}:${process.env.BRIGHTDATA_PASS}@brd.superproxy.io:9222`
);

const page = await browser.newPage();
await page.goto('https://www.linkedin.com/in/someone');
const html = await page.content();
await browser.close();
```

The session is full Playwright - selectors, screenshots, evaluation - but the browser runs on Bright Data's infrastructure with their unblocking layer.

### Other notable mentions

- **Apify Store** - for many targets (Google Maps, Instagram, LinkedIn), there are pre-built Apify Actors maintained by Apify or community publishers. They handle the protection arms race so you don't have to. When the target is "common enough" to have a maintained Actor, that's often the right answer.
- **SerpApi, Serper** - specialized for Google SERP, Bing, etc. Better than DIY for SERP scraping at any volume.
- **Oxylabs Web Scraper API** - premium, similar tier to Bright Data. Less well-known but effective.

---

## Choosing between vendors

A decision matrix for the common cases:

| Scenario | Best fit | Reason |
|---|---|---|
| AI/LLM RAG pipeline (clean content) | Firecrawl | Markdown + extract, MCP-ready |
| Cloudflare-protected e-commerce | ZenRows or ScrapFly | Reliable bypass at moderate cost |
| LinkedIn, Amazon at scale | Bright Data Scraping Browser | Only consistently reliable option |
| LinkedIn occasional / low-volume | Apify Store actor | Maintained, pay-per-use |
| Google SERP at any volume | SerpApi or Apify GOOGLE_SERP proxy | Specialized = cheaper |
| Prototype / occasional scraping | ScraperAPI | Free tier covers it |
| Maximum control over scrape config | ScrapFly | Most knobs |
| Hardest of hard (sneakers, tickets) | Bright Data | Last resort that works |

If you already have a Firecrawl connection in your stack, default to Firecrawl unless the target's profile pushes you elsewhere.

---

## Integration patterns

### Wrap the API in a thin adapter

Don't scatter `axios.post('https://api.zenrows.com/...')` calls across your codebase. Wrap once.

```ts
// src/clients/scraping.ts

interface ScrapeResult {
  html: string;
  statusCode: number;
  finalUrl: string;
}

export interface ScrapingClient {
  scrape(url: string, options?: ScrapeOptions): Promise<ScrapeResult>;
}

export class ZenrowsClient implements ScrapingClient {
  constructor(private apiKey: string) {}
  
  async scrape(url: string, options: ScrapeOptions = {}): Promise<ScrapeResult> {
    const response = await gotScraping({
      url: 'https://api.zenrows.com/v1/',
      searchParams: {
        apikey: this.apiKey,
        url,
        js_render: options.jsRender ? 'true' : 'false',
        premium_proxy: options.premium ? 'true' : 'false',
        proxy_country: options.country ?? 'us',
      },
      retry: { limit: 2 },
      timeout: { request: 90_000 },
    });
    return {
      html: response.body,
      statusCode: response.statusCode,
      finalUrl: response.url,
    };
  }
}

export class FirecrawlClient implements ScrapingClient { /* ... */ }
export class ScrapflyClient implements ScrapingClient { /* ... */ }
```

Your route handler / entry point depends on `ScrapingClient`, not on the specific vendor. Swapping vendors is a one-line change in `main.ts`.

### Cache aggressively

Use the same cache pattern (KV Store on Apify; Redis / Postgres / disk on a standalone host) as for any external API. A 30-day TTL on managed API responses saves real money - every cache hit is a vendor call you didn't make.

```ts
const cached = await withCache(
  `scraping-${vendor}:${md5(url + JSON.stringify(options))}`,
  () => client.scrape(url, options),
  30 * 24 * 60 * 60 * 1000,
);
```

For data that updates frequently (live prices, real-time scores), cache for shorter (hours instead of days). But default to long.

### Failover patterns

For business-critical scrapers, have a fallback chain. When the primary vendor fails, try the secondary.

```ts
export class FailoverClient implements ScrapingClient {
  constructor(private primary: ScrapingClient, private fallback: ScrapingClient) {}
  
  async scrape(url: string, options: ScrapeOptions = {}): Promise<ScrapeResult> {
    try {
      const result = await this.primary.scrape(url, options);
      if (result.statusCode === 200) return result;
      // primary returned a non-200; try fallback
    } catch (e) {
      log.warning(`Primary vendor failed: ${e.message}`);
    }
    return this.fallback.scrape(url, options);
  }
}

// Usage
const client = new FailoverClient(
  new ZenrowsClient(process.env.ZENROWS_API_KEY!),
  new ScrapflyClient(process.env.SCRAPFLY_API_KEY!),
);
```

Use this sparingly - every failover doubles your potential cost on hard pages. But for scrapers where uptime matters more than per-call cost (alerting, monitoring), it's the right pattern.

### Cost monitoring

Track vendor spend per scraper by persisting it to your output sink (Apify dataset, a DB table, etc.):

```ts
// persist to your output sink (Apify dataset, a DB table, etc.)
await output.push({
  ...result,
  meta: {
    vendor: 'zenrows',
    vendorCost: 0.005,  // approximate
    cached: false,
  },
});
```

This makes the cost visible at the record level. The user can sum vendor cost across runs and decide whether to switch vendors or invest in DIY.

---

## When the managed API blocks you too

Even managed APIs fail. The provider's IPs get burned, the target adds a new layer, the vendor's bypass falls behind. Symptoms:

- 403/429 from the vendor (not from the target - the vendor is rate-limiting your account).
- Empty/incomplete content where there was rich content before.
- Redirect loops, fake error pages, suspicious "challenge" content in responses.

Response:

1. **Try the vendor's premium tier or different proxy options.** Most have residential vs. datacenter, country selection, JS-rendering toggle. Try the next-up tier.
2. **Switch vendors temporarily.** A failover client (above) does this automatically.
3. **Open a support ticket with the vendor.** Most vendors track per-target success rates internally and will fix common issues within days.
4. **Consider DIY.** If the target is now important enough that managed APIs can't keep up, the economics may have shifted.

---

## Combining managed + DIY in one Actor

Common pattern for multi-source aggregators (e.g. a wine-price aggregator, a real estate aggregator):

```
Scraper: scrape multiple sources
  ├── source A (Vivino API): got-scraping, internal API
  ├── source B (Wine.com): CheerioCrawler, light protection
  ├── source C (Wine-Searcher): Camoufox or Firecrawl
  └── source D (Total Wine): managed API
```

Each source uses the appropriate level of the tool ladder. The scraper orchestrates them via a unified interface (`SourceClient`) and an aggregation step that merges the results.

```ts
interface SourceClient {
  search(query: string): Promise<WineResult[]>;
}

const sources: SourceClient[] = [
  new VivinoSource(),       // internal API
  new WineComSource(),      // CheerioCrawler
  new WineSearcherSource(), // Firecrawl
  new TotalWineSource(),    // ZenRows
];

const allResults = await Promise.allSettled(
  sources.map(s => s.search(query))
);
// merge, dedupe, rank
```

Use `Promise.allSettled` here so one source failing doesn't abort the run (see `error-handling-ppe.md`).

---

## Vendor-agnostic principles

Three rules that apply regardless of which vendor you use:

1. **Cache by default.** Vendor calls are expensive; cached calls are free.
2. **Wrap in a thin adapter.** Vendors change pricing and APIs; your code shouldn't have to.
3. **Treat vendor failures like target failures.** Same error taxonomy, same graceful exit, same SUCCEEDED-with-error-record pattern.

Vendors are infrastructure. Treat them with the same skepticism you'd treat any external service: validate responses, cache aggressively, fail gracefully, monitor cost.
