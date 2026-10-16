# Selector Strategy - Building Resilient Extraction

Selectors break. Sites redesign, A/B tests roll out, ad-hoc class names get refactored. A scraper that depends on `.product-price-current-v2-final-final` will work for six weeks and then mysteriously return nulls. This page is about building extraction that survives.

The principle: **never depend on a single selector for a critical field**. Layered fallbacks, structured data anchors, and post-extraction validation together produce scrapers that survive site changes long enough to notice and react.

---

## The selector hierarchy (most to least stable)

When extracting a field, prefer selectors in this order:

### 1. Schema.org / OpenGraph / structured data

The most stable source. Sites that publish structured data have a contract with search engines and will think twice before changing it.

```ts
// JSON-LD (most common)
const ldJson = $('script[type="application/ld+json"]').toArray()
  .map(el => {
    try { return JSON.parse($(el).html()); } catch { return null; }
  })
  .filter(Boolean);

const product = ldJson.find(d => d['@type'] === 'Product');
const price = product?.offers?.price ?? product?.offers?.[0]?.price;

// OpenGraph meta tags
const ogTitle = $('meta[property="og:title"]').attr('content');
const ogImage = $('meta[property="og:image"]').attr('content');

// Microdata (older sites)
const microdataPrice = $('[itemprop="price"]').attr('content') ?? $('[itemprop="price"]').text();
```

Use this as your primary extraction source whenever it's available. It rarely breaks.

### 2. ID selectors (`#unique-id`)

IDs are usually meaningful and intentional. They survive most refactors.

```ts
const title = $('#product-title').text().trim();
```

### 3. Stable class names with semantic meaning

Classes that describe what the element *is* (not what it looks like) tend to be stable.

Stable: `.product-name`, `.review-author`, `.price-amount`
Volatile: `.text-lg.font-bold.mt-4`, `.css-1a2b3c4d`, `.MuiTypography-root`

Tailwind/CSS-in-JS sites generate volatile classes - avoid them as primary anchors.

### 4. `data-*` attributes

Often used by frontend frameworks for testing or analytics. Generally stable because they're decoupled from styling.

```ts
const price = $('[data-price]').attr('data-price') ?? $('[data-product-price]').attr('data-product-price');
```

### 5. Element + attribute combinations

When all else fails, use semantic HTML structure.

```ts
const author = $('article header .byline a').first().text().trim();
```

### 6. XPath for hierarchy-dependent extraction

XPath is more powerful than CSS for navigating relationships ("the next sibling of," "ancestor of"). Use sparingly; XPath is harder to read and debug.

```ts
// Cheerio doesn't have XPath built-in; use cheerio-without-node-native or convert via DOM utilities.
// In Playwright:
const sibling = await page.locator('xpath=//h2[text()="Reviews"]/following-sibling::p[1]').textContent();
```

---

## The fallback pattern

For every critical field, write a fallback chain. The chain stops at the first non-null result.

```ts
function extractPrice($: cheerio.CheerioAPI): string | null {
  // 1. Structured data first
  const ldPrice = extractFromLdJson($)?.offers?.price;
  if (ldPrice) return String(ldPrice);
  
  // 2. data-* attribute
  const dataPrice = $('[data-price]').attr('data-price');
  if (dataPrice) return dataPrice;
  
  // 3. semantic class
  const semanticPrice = $('.price-current').text().trim();
  if (semanticPrice) return semanticPrice;
  
  // 4. fallback class (still semantic-ish)
  const altPrice = $('.product-price').text().trim();
  if (altPrice) return altPrice;
  
  // 5. last-resort heuristic - find any element that looks like a price
  const heuristic = $('*').filter((_, el) => /^\$\d+(\.\d{2})?$/.test($(el).text().trim())).first().text().trim();
  if (heuristic) return heuristic;
  
  return null;
}
```

The chain reads top-down by stability. The first selector you hit that returns a non-null value wins.

For Playwright, the same pattern uses chained locators with `.or()`:

```ts
const price = await page.locator('[data-price]')
  .or(page.locator('.price-current'))
  .or(page.locator('.product-price'))
  .first()
  .textContent();
```

---

## Schema validation as a safety net

Every extracted record passes through a Zod (TypeScript) or Pydantic (Python) schema before being pushed to the dataset. The schema is the contract; failures are caught explicitly, not silently.

```ts
import { z } from 'zod';

const WineRecord = z.object({
  id: z.string(),
  name: z.string().min(1),
  vintage: z.number().int().min(1900).max(2030).optional(),
  rating: z.number().min(0).max(5).optional(),
  price: z.object({
    amount: z.number().positive(),
    currency: z.string().length(3),
  }).optional(),
  url: z.string().url(),
});

type WineRecord = z.infer<typeof WineRecord>;

function buildRecord(raw: any): WineRecord | { error: true; errorType: string; details: unknown } {
  const result = WineRecord.safeParse(raw);
  if (!result.success) {
    return {
      error: true,
      errorType: 'SCHEMA_VALIDATION_FAILED',
      details: result.error.flatten(),
    };
  }
  return result.data;
}
```

Schema validation gives you four things:

1. **Early detection of site changes.** If the price selector starts returning a string instead of a number, the schema rejects it immediately, the failure is logged with the reason, and the dataset gets an error record instead of garbage data.
2. **Type safety for downstream consumers.** Whoever uses the dataset can rely on the shape.
3. **Documentation in code.** The schema is also the README of what the scraper produces.
4. **Anti-bot canary.** This is the critical one and often missed: anti-bot vendors (especially Akamai and PerimeterX) sometimes return HTTP 200 with empty or fake content rather than a 403. The HTTP status looks fine; the data is junk. Schema validation catches this - required fields missing means either the site changed or you're being silently blocked. The diagnosis isn't immediate, but the failure surfaces, which is the whole point. **Treat schema validation failures with the same urgency as outright HTTP errors.**

A useful pattern: tag schema-failed records with a `suspect_silent_block: true` flag in the error output when the page loaded successfully (200 OK) but no critical fields parsed. This trains operators to recognize the pattern.

## Validating your own browser fingerprint

When configuring a browser-based scraper (Playwright, Camoufox), validate the fingerprint it produces before deploying. Two free tools in your browser do this:

- **CreepJS** (`creepjs.com`) - runs the most rigorous open-source fingerprint test. Reports a stability and trust score, lists every detection vector it caught, and shows what an anti-bot vendor would see.
- **FingerprintJS demo** - commercial-grade fingerprinting library; test page shows the visitorId and the contributing signals.

How to use them in practice:

1. Configure the scraper with the browser, fingerprint generator settings, proxy, and humanization layer you intend to deploy.
2. Have it visit `creepjs.com` or `fingerprint.com/demo` (these are diagnostic pages, fine to hit) instead of the target.
3. Capture the page result.
4. Inspect: is the trust score reasonable? Does the OS match the User-Agent? Are Canvas, WebGL, AudioContext outputs internally consistent?
5. If anomalies surface, fix them in configuration before pointing at the real target.

This is one of the few cases where running the scraper against a non-fixture URL is acceptable - the test pages are public diagnostic tools that exist precisely for this purpose, and you're not extracting data from them.

---

## Per-field strategies

### Numeric fields (prices, ratings, counts)

Always parse, never store as strings. Strip currency symbols, separators, whitespace.

```ts
function parsePrice(raw: string): { amount: number; currency: string } | null {
  // "$1,299.99" → { amount: 1299.99, currency: 'USD' }
  // "€ 199" → { amount: 199, currency: 'EUR' }
  // "1.299,99 €" (European format) → { amount: 1299.99, currency: 'EUR' }
  
  const currencyMatch = raw.match(/[\$€£¥]|USD|EUR|GBP|JPY/);
  const currency = currencyMatch ? currencyToISO(currencyMatch[0]) : 'USD';
  
  const numStr = raw
    .replace(/[\$€£¥]|USD|EUR|GBP|JPY/g, '')
    .replace(/\s/g, '')
    .replace(/(\d{1,3})\.(\d{3})(?:\.|,|$)/g, '$1$2$3')  // collapse thousand-dot separator
    .replace(',', '.');                                    // decimal comma → dot
  
  const amount = parseFloat(numStr);
  if (Number.isNaN(amount)) return null;
  return { amount, currency };
}
```

The European-format fallback (`1.299,99` style) is non-trivial. Test against a fixture with both formats.

### Text fields

Trim, normalize whitespace, but preserve content:

```ts
function clean(text: string | undefined): string | undefined {
  if (!text) return undefined;
  return text.replace(/\s+/g, ' ').trim() || undefined;
}
```

Don't lowercase or strip punctuation - you may need it later.

### Date fields

Parse to ISO 8601 (YYYY-MM-DD or full ISO with time). Use a robust parser like `date-fns` or `dayjs`. Test with multiple input formats.

```ts
import { parse } from 'date-fns';

const formats = [
  'yyyy-MM-dd',
  'MM/dd/yyyy',
  'dd/MM/yyyy',
  'd MMMM yyyy',
  'MMM d, yyyy',
];

function parseDate(raw: string): Date | null {
  for (const fmt of formats) {
    const d = parse(raw.trim(), fmt, new Date());
    if (!Number.isNaN(d.getTime())) return d;
  }
  return null;
}
```

### URLs

Always resolve to absolute URLs. Cheerio gives you relative URLs by default.

```ts
const href = $('a.next').attr('href');
const absolute = href ? new URL(href, request.loadedUrl).toString() : null;
```

For request enqueueing, Crawlee's `enqueueLinks` handles this automatically. For data fields, do it explicitly.

### Image URLs

Same - resolve to absolute. Also strip tracking parameters and resize hints when normalization helps:

```ts
function normalizeImage(url: string): string {
  const u = new URL(url);
  // strip common tracking params
  ['utm_source', 'utm_medium', 'fbclid'].forEach(p => u.searchParams.delete(p));
  // strip Akamai/Cloudinary resize segments to get the original
  u.pathname = u.pathname.replace(/\/w_\d+,h_\d+(,\w_\w+)*\//, '/');
  return u.toString();
}
```

### Lists / arrays

When extracting repeated structures (review list, product list), iterate and apply per-item extraction.

```ts
const reviews = $('article.review').toArray().map(el => {
  const $el = $(el);
  return {
    author: clean($el.find('.author').text()),
    rating: parseInt($el.find('[data-rating]').attr('data-rating') ?? '0'),
    text: clean($el.find('.review-body').text()),
    date: parseDate($el.find('time').attr('datetime') ?? ''),
  };
}).filter(r => r.author && r.text);  // drop incomplete records
```

---

## Honeypot avoidance

Honeypots are hidden links/elements designed to trap bots. A bot that follows every `<a>` triggers them.

Crawlee's link extractor checks visibility by default. For manual implementations:

```ts
$('a').each((_, el) => {
  const $el = $(el);
  const style = $el.attr('style') ?? '';
  if (style.includes('display:none') || style.includes('display: none')) return;
  if (style.includes('visibility:hidden') || style.includes('visibility: hidden')) return;
  // also check classes that imply hidden
  if ($el.attr('class')?.match(/(hidden|invisible|display-none)/)) return;
  
  const href = $el.attr('href');
  if (href) candidates.push(href);
});
```

For browser-based crawlers, use `page.locator('a:visible')` or check `element.boundingBox()` (zero-size elements are honeypots).

---

## Per-Actor selector documentation

For non-trivial scrapers, maintain a reference document listing all selectors used, their purpose, and their fallbacks. This lives in `{site}-selectors.md` in the scraper's repo:

```markdown
# Wine-Searcher selectors

## Listing page (search results)

| Field | Primary selector | Fallback 1 | Fallback 2 | Notes |
|---|---|---|---|---|
| Result item | `tr.row` | `.search-result` | - | Wine-Searcher uses table-based listings |
| Wine name | `h3.wine-card__name a` | `.title-name` | - | |
| Vintage | `.wine-card__vintage` | extracted from name regex `\b(19\|20)\d{2}\b` | - | |
| Price | `.wine-card__price` | `[data-price]` | regex `\$\d+(\.\d{2})?` | Currency varies by ?Xc= param |
| Merchant | `.wine-card__merchant` | `.merchant-name` | - | |

## Detail page

[...]
```

This file becomes the maintenance manual. When the site changes, you update one place.

---

## When selectors break: the recovery protocol

You'll find out from a sudden drop in success rate. Steps:

1. **Capture a fresh fixture.** Run the scraper in DEBUG mode with HTML snapshotting on failure (Crawlee: `enableSnapshots: true` saves HTML snapshots automatically; standalone: write the failing page's HTML to disk or your storage). Retrieve the snapshot.
2. **Diff against the previous fixture.** What changed in the structure? Are the classes different? Did the page layout change entirely?
3. **Update the selector chain.** Add the new selector as the primary; demote the old one to a fallback (or delete if fully gone).
4. **Update the fixture in the test suite.** Replace the stale fixture with the fresh one. Re-run tests.
5. **Document in the per-scraper selector reference.** Note the date of the change and what changed.
6. **Push and observe.** Success rate should recover within hours.

If the site changed structurally (e.g., switched from table-based to card-based layout), the recovery may require rewriting the route handler, not just updating selectors. Plan for this - schema validation catching the shape change is what triggers the alert.

---

## API responses are also "selectors"

When you scrape an internal API, the JSON path is your selector. Same principles apply:

- Validate the response with a Zod/Pydantic schema.
- Don't trust nested optional paths blindly: `data?.results?.[0]?.price?.amount`.
- Document the expected shape in `{source}-api.md` in the scraper's repo.
- Cache responses (a cache layer: KV Store on Apify; Redis / Postgres / disk on a standalone host) so a transient API change doesn't take you offline immediately.

The API equivalent of a "selector change" is a schema migration. The alert mechanism - schema validation rejecting the response - is the same.

---

## Recognizing defender patterns: encrypted rotating form fields

When extracting from a form-driven workflow (search, filter, login flows that you have legitimate access to), you may encounter form field names that change every page load. Indicators:

- The HTML form has inputs with apparently random names (`<input name="a4f9e2b1c8...">`)
- Refreshing the page produces different input names each time
- The form values are also encrypted/encoded, not plain
- Heavy obfuscated JavaScript runs before the form submission
- The script injects additional hidden fields only after a CAPTCHA or behavioral check passes

This is a deliberate defender pattern: encrypt form field names per session with a server-side secret, deliver the names via JS only after passing checks, and reject any submission with predictable/static field names. The intent is explicit: "automation is not welcome here."

Approach when you encounter this:

1. **First, ask if you should.** A defender who built rotating encrypted form fields is sending a clear signal. Re-read `legal-ethics.md` for the principles. For most public-data scraping, this is a sign to walk away.
2. **If the use case is genuinely legitimate** (e.g., the user has explicit permission, the data is genuinely public, the protection is over-aggressive vs. fair use), the only viable approach is a real browser running the actual JavaScript. Don't try to decrypt the obfuscated code - defenders rotate the encryption regularly to defeat reverse engineering. Live execution is faster, more stable, and more ethically defensible (you're running the page as it was meant to run).
3. **Camoufox or PlaywrightCrawler with Crawlee fingerprints** is the right tool. Let the browser load the form, fill it normally via Playwright's input methods, submit it. The browser's JS engine handles encryption and dynamic field injection.
4. **Token replay won't help here.** The encrypted fields are tied to the specific page load and don't survive across sessions.

This pattern is not common - most sites don't go this far - but recognizing it tells you exactly what tier of effort the defender invested. Calibrate your response (or your decision to walk away) accordingly.

---

## The discipline summary

For every field you extract:
- Use the most stable selector available (structured data > id > semantic class > attribute > XPath).
- Write a fallback chain.
- Validate the parsed output against a schema.
- Document the selector in a per-scraper reference.

For every scraper:
- Snapshot the HTML on failure (Crawlee: `enableSnapshots`; Apify: `Actor.setValue('snapshot.html', html)`; standalone: write it to disk or your storage).
- Monitor success rate; investigate drops within a day.
- Maintain fixtures alongside selectors; they evolve together.

This is what production scrapers do. The selector itself is the smallest part - the discipline around it is what makes the difference.
