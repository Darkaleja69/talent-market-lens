---
name: scraping-expert
description: Senior-level doctrine for designing, building, and debugging web scrapers on any platform: diagnose anti-bot before touching code, find hidden JSON APIs, choose the cheapest viable tool (Cheerio/Playwright/Camoufox/managed APIs), configure proxies and sessions, build resilient selectors, categorize 403/429 and silent blocks, cache responses, and test with fixtures. Use whenever a scraper must be built, modified, or debugged; when selector rot or rate limits appear; or as soon as a target URL or site name shows up in a scraping context. Platform-agnostic; Apify-specific deployment details live in references.
---

# Scraping Expert

Senior-level doctrine for shipping scrapers that actually run for months. Scraping is engineering, not selectors - and the difference between a script that works once and a scraper that survives in production is a sequence of decisions made before a single line of code is written.

This skill is opinionated and **platform-agnostic**. It encodes patterns proven across scrapers with millions of runs, and integrates the 2026 anti-bot landscape. The doctrine here - diagnosis, tool choice, anti-bot strategy, resilience, failure handling, testing - applies to **any** scraper: a standalone Python/Node script, a cron job on a VPS, a serverless function, a web-app backend, or an Apify Actor. Apify is one deployment target among others; where a pattern is Apify-specific (PPE billing, `actor.json`, KV Store, Standby) it is called out as such.

The skill operates from one foundational frame: **anti-bot is an economic cost function, not an absolute wall.** No system that operates over the network is fully closed; the goal is not to be undetectable but to keep the cost-per-result below the value-per-result while staying under the trust threshold the target has set. There's a corollary to internalize early - when a target deploys government-ID-and-selfie verification (IDV), that is no longer a cost function; it is a hard limit. We don't engineer around IDV. See `references/legal-ethics.md` for the boundaries.

---

## Two recommended operating rules

These two rules reflect a safe production deployment model. They are strong defaults rather than universal law, but following them prevents the most common operational mistakes.

### Rule 1 - Don't scrape from your local/dev machine

All HTTP requests to target sites should run on **remote infrastructure** - Apify (Actors, proxies, browsers), your own deployed servers/VPS, or a managed scraping API (Firecrawl, ZenRows, ScrapFly, ScraperAPI). The local machine where you develop should never make a request to a target site. This protects your home/office IP from reputation damage and matches the production deployment.

The practical consequence is large: **local development uses fixtures, not the network**. Saved HTML/JSON responses (scrubbed of PII) live in `tests/fixtures/` and are loaded by the test suite via mocks. The first time you actually hit the target is in a deployed run (on Apify or your own infra). See `references/testing-fixtures.md` for the pattern.

When you want to "test this on the live site," the correct answer is: run it on the remote infrastructure, not locally. Avoid `curl` or `fetch` to the target from the local shell, even to "just check the page structure" - use existing scraped fixtures, the deployed environment, or a managed API if a probe is genuinely needed.

### Rule 2 - Keep scraper repositories private

Scraper code generally belongs in private repositories. *On Apify specifically:* deploy via the Apify CLI (`apify push`) or the API directly. Be deliberate about what ends up in publicly visible source files.

---

## Boundaries - the doctrine layer vs. implementation

This skill is the **doctrine and architecture layer**: diagnosis, tool choice, anti-bot strategy, resilience patterns, platform-agnostic failure handling, and testing doctrine. It decides **what to build and why**. It does NOT own the code scaffolding, the audit of an existing scraper, MCP-server implementation, monetization/PPE doctrine, content/marketing artifacts, or generic SEO. The Apify-specific *deployment* doctrine it does carry (graceful-exit + PPE engineering, `actor.json`/proxy/KV/Standby config) is segregated into clearly-labeled references.

When you want to actually build, audit, or wrap a scraper, the diagnosis/tool-ladder/anti-bot decisions here still apply - pair them with the relevant implementation tooling:

| Trigger | Where the implementation lives |
|---|---|
| Write/scaffold the actual scraper Actor **code** - Crawlee crawler setup, `input_schema.json`, `Dockerfile`, RequestQueue, no-code or AI-extraction templates | A dedicated Apify scraper-builder tool/skill (consumes this skill's tool-ladder + anti-bot doctrine) |
| **Audit an existing scraper** - coverage gaps, data-quality, selector rot, success-rate, cost-per-record, schema drift | A dedicated scraper-audit tool/skill (uses this skill as the doctrine layer for the fixes it recommends) |
| Build / modify / debug an **MCP server** Actor - Standby mode, transports (stdio / SSE / Streamable HTTP), `webServerMcpPath`, MCP server code | A dedicated Apify MCP-server-builder tool/skill |
| Which pricing model should I use? / How do I implement `Actor.charge()` correctly (full doctrine)? / Should I migrate off Rental? / `eventChargeLimitReached` handling doctrine | A dedicated Apify monetization tool/skill |
| README copy / Store description / SEO meta description / Actor display name + slug / pricing-section template / changelog format / Apify Store categories | A dedicated Apify content tool/skill |
| Generic SEO audit of a web project (Next.js / Astro / SvelteKit / WordPress / static site) - NOT Apify-specific | A dedicated SEO-audit tool/skill |

This file's `references/error-handling-ppe.md` documents the **scraping-engineering layer** of the graceful-exit + PPE-billing pattern; the canonical PPE doctrine belongs to a dedicated monetization tool. On any contradiction, the canonical monetization doctrine wins.

---

## The expert workflow

Every scraping project moves through these five phases in order. Skipping a phase is the root cause of scrapers that "worked yesterday" and don't today.

### Phase 1 - Diagnose the target (before writing any code)

Before choosing a tool, identify what you are up against. The diagnostic determines everything downstream - tool, proxy type, budget, timeline. A wrong diagnosis costs days. See `references/diagnostic.md` for the full procedure (including the Wappalyzer-first identification trick that turns 30 minutes of guessing into 5 minutes of reading).

The questions to answer:
1. **Does an internal API exist?** This is the first and most important question. If the site's frontend calls a JSON endpoint, scraping HTML is almost always wasted work. See `references/hidden-apis.md`.
2. **What anti-bot vendor protects the site?** Cloudflare, DataDome, PerimeterX/HUMAN, Akamai, AWS WAF, or just naive rate limiting. Each has a signature and a different tool ladder. The detection model is the same across all of them - four pillars (IP reputation, fingerprinting, behavior, active challenges) - but the emphasis differs. See `references/anti-bot-strategies.md`.
3. **Is the content rendered server-side or client-side?** Static HTML allows HTTP-only crawling (10-100× cheaper). SPAs require a browser.
4. **What is the volume target?** Ten URLs per day is not a thousand per minute. Volume drives proxy strategy and concurrency.
5. **What is the data shape?** A search-results page with pagination is not a single product detail page. Crawler topology depends on this.

**Actively probe the anti-bot, don't just identify it.** Beyond reading signatures, fire test requests at the target through the research/scraping APIs and MCP tools available - Firecrawl, ZenRows, ScrapFly, ScraperAPI, Tavily/Exa, the Apify console - to observe *how the protection actually behaves*: does a plain fetch get a 403 or a silent-200? does a residential-proxy managed call get through where datacenter fails? does JS rendering change the result? Each probe (always off the local IP) narrows the tool/proxy choice with evidence instead of a guess. A real browser (Wappalyzer + DevTools) is the best surface for first-time recon.

**The objective of Phase 1 is not just "which vendor protects this."** It is to define the full plan that will let you scrape reliably: the tool rung, the proxy tier and country, the pacing/timing and concurrency budget, the session/token-replay strategy, and the escalation plan if the first bet fails. Phase 1 ends with that one-page plan (see the diagnostic output in `references/diagnostic.md`) - not with a vendor name.

### Phase 2 - Choose the cheapest viable tool

The default is the cheapest tool that works. Browsers are 10-100× more expensive in compute, memory, and proxy credits than HTTP. Camoufox specifically averages 200 MB RAM per instance and tens of seconds per Cloudflare challenge - usable, but not for commodity volumes. The full decision tree lives in `references/tool-ladder.md`. The compressed version:

| Target characteristics | Tool | Why |
|---|---|---|
| Internal JSON API exists | `got-scraping` + Cheerio (or just `got-scraping`) | Fastest, cheapest, returns structured data |
| Static HTML, no anti-bot | `CheerioCrawler` | 20× faster than browser, no JS overhead |
| Static HTML, basic rate-limit/header checks | `CheerioCrawler` + datacenter proxies + sessions | Crawlee handles fingerprints automatically |
| Mixed static/dynamic (multi-site crawl) | `AdaptivePlaywrightCrawler` | Switches HTTP↔browser per page |
| JS-rendered, no major anti-bot | `PlaywrightCrawler` + default fingerprints | Real browser TLS, automatic stealth |
| Cloudflare (basic), Akamai (basic), DataDome | `PlaywrightCrawler` + residential proxies + fingerprints | Crawlee's built-in fingerprint injection |
| Cloudflare Enterprise, HUMAN Security, hard targets | Camoufox + residential proxies, or managed API | Engine-level stealth or paid bypass |
| LinkedIn, Amazon, SERP at scale | Managed API (ZenRows, ScrapFly, Bright Data Scraping Browser) | DIY economics break - pay for someone else's arms race |

Camoufox is currently the only open-source tool achieving 0% headless-detection scores - but at real cost. Use it deliberately, not by default.

### Phase 3 - Implement with resilience baked in

Resilient scrapers are not scrapers with extra try/catch. Resilience is structural. The non-negotiables:

- **Selectors with fallbacks.** Never depend on a single CSS selector for a critical field. Layered fallbacks (`.price-current ?? [data-price] ?? .price`) survive A/B tests and minor redesigns. See `references/selector-strategy.md`.
- **Sessions and proxy rotation are configured, not coded by hand.** Crawlee's `useSessionPool: true` plus a proxy configuration is enough for 80% of cases (and Crawlee runs anywhere, not just on Apify). On Apify, see `references/apify-patterns.md` for the concrete config.
- **Cache external responses by default.** Put a cache layer in front of every third-party response - KV Store on Apify, Redis/Postgres/disk on a standalone host - with a ~30-day TTL. Pattern: `{source}-cache:{hash}`. Saves money and rate-limit pressure.
- **Retries with exponential backoff at the right layer.** Crawlee handles request-level retries. You handle business-logic retries (LLM/API rate limits, third-party API errors).
- **Surface progress and degraded states from inside the run.** Whatever the operator watches - `Actor.setStatusMessage()` in the Apify console, structured logs, a run-status row in your DB - keep it updated so a run's health is visible without reading raw logs.

### Phase 4 - Handle errors as a first-class output

The principle is platform-agnostic: **treat blocks and errors as a categorized, structured output, not as exceptions that crash the run.** A business-logic failure - invalid input, blocked by target, third-party API down, file corrupted, no results found - is emitted as a structured error record to whatever your output sink is (an Apify dataset row, a Postgres error column/table, a JSON line in a log), so the operator sees *what happened* without reading raw logs. The run-level success signal (process exit code, or the platform's run status) is reserved for genuine infrastructure failure (OOM, crash).

Two parts of this are doctrine you carry everywhere:
- **The failure taxonomy.** `TARGET_BLOCKED` ≠ `EXTRACTION_FAILED` ≠ rate-limit ≠ `TARGET_NOT_FOUND` ≠ bad-input - each gets different handling. Critically, **a 200 with an empty or fake body is a silent block, not a success**; schema-validate the result and treat a validation miss as a block. The full taxonomy and decision table are in `references/error-handling-ppe.md` (the Apify implementation; the taxonomy itself is general).
- **Commit irreversible side effects only on confirmed success.** On Apify this is the graceful-exit + PPE-billing pattern (`Actor.charge()` only after the data is committed - never on retry/partial/error); on a standalone host it's "COMMIT the DB transaction / fire the webhook only after the scrape succeeded." Same rule, different mechanism. Apify deployment specifics (exit SUCCEEDED, `Actor.charge`, free-plan handling) live in `references/error-handling-ppe.md`.

### Phase 5 - Test without hitting the network

Tests use Vitest (or pytest equivalent) with fixture-based mocks. The test suite must run with no network access at all. Mock everything that crosses the process boundary:
- `got-scraping`, `firecrawl` (the Firecrawl SDK), any HTTP client used at runtime
- your platform/runtime SDK (on Apify: `Actor.charge()`, `Actor.openKeyValueStore()`, `Actor.pushData()`; elsewhere: your DB client, cache, queue)
- any third-party SDK (Anthropic, OpenAI, etc.)

Fixtures (HTML or JSON snapshots from the target, scrubbed of PII) live in `tests/fixtures/`. The parser is tested against the fixture, not the live site. Coverage targets are: every field extracted, every error path, every selector fallback. See `references/testing-fixtures.md`.

Running tests: `npx vitest run` (or `pytest`). Never `--watch` mode in CI, and never with live network calls.

---

## The golden rules

Distilled from production experience, platform-agnostic. When in doubt, return to these.

1. **Internal API first.** Spend twenty minutes in the browser DevTools Network panel before writing a selector. Nine times out of ten there is a JSON endpoint. Vivino's public-facing pseudo-API (`/api/explore/explore`, `/api/wines/{id}/reviews`) is the canonical example - what could have been a Camoufox-grade scraper is a lightweight HTTP client hitting JSON.

2. **Cheapest viable tool first.** Start one rung lower than you think you need on the tool ladder. Escalate only on observed failure. A Playwright scraper for a static site burns 10× the budget for nothing.

3. **Categorize errors and blocks before handling them.** A 403 from anti-bot is not the same problem as a 403 from a missing token. A 429 with `Retry-After` is not the same as a 429 from IP burn. A 200 with an empty/fake body is a silent block, not a success. Treat each class separately. See the taxonomy in `references/error-handling-ppe.md`.

4. **Cache external calls by default.** Every external response goes through a cache layer (KV Store, Redis, Postgres, or disk) with a ~30-day TTL. Cache reads happen before any external request. The pattern is unconditional - opting out is the exception, not the rule.

**On Apify specifically**, two more rules apply at the deployment layer: *always exit SUCCEEDED* (business errors go in the dataset, never `Actor.fail()`) and *PPE charges fire only on confirmed success* (`Actor.charge()` last, never on retry/partial/error). Both live in `references/error-handling-ppe.md`.

---

## Quick decision flow

When the user asks to scrape site X:

```
1. Identify the site. Have you scraped a similar target before?
   YES → Reuse the established pattern from prior work on that target.
   NO  → Continue.

2. Probe (without local IP):
   - Open the site in DevTools or use a managed API to fetch.
   - Check for internal JSON endpoints.
   - Check Set-Cookie headers and challenge pages for anti-bot signatures.

3. Map to tool:
   - Internal API found? → got-scraping + Cheerio.
   - Static HTML, light protection? → CheerioCrawler.
   - JS-heavy? → PlaywrightCrawler.
   - Cloudflare/DataDome/HUMAN? → Camoufox or managed API.

4. Estimate cost (unit economics):
   - cost-per-result (compute + proxy + managed-API fees) vs. value-per-result.
   - Volume × cost-per-call → does it work? (If monetizing on Apify, the PPE price must absorb this with margin.)

5. Architecture & deployment shape:
   - Single job or a suite (e.g., search + detail + price)?
   - Where does it run - standalone script/cron, serverless, web-app backend, Apify Actor, or MCP server?

6. Implement Phase 3 → 4 → 5.
```

---

## When to read each reference

The body above gives you the operational answer 80% of the time. Reach for `references/` when:

**Core scraping doctrine (any platform):**

| Read this | When you need to… |
|---|---|
| `references/diagnostic.md` | Identify what anti-bot protects an unknown site, probe its behavior, decode HTTP responses, and define the full plan (tool + proxy + pacing + escalation) |
| `references/hidden-apis.md` | Find and use internal JSON endpoints (Vivino-style), reverse-engineer GraphQL, extract embedded `__NEXT_DATA__` |
| `references/tool-ladder.md` | Choose between Cheerio / Playwright / Camoufox / managed APIs with full decision criteria |
| `references/anti-bot-strategies.md` | Configure escalation: HTTP+headers → sessions → throttling → browser → managed API. Know exactly what to try next |
| `references/selector-strategy.md` | Build resilient selectors, layered fallbacks, schema validation, deal with A/B tests and SPA rerenders |
| `references/managed-apis.md` | Compare Firecrawl, ZenRows, ScrapFly, ScraperAPI, Bright Data Scraping Browser. Choose, integrate, fail over |
| `references/testing-fixtures.md` | Test without the network: fixtures, mock-at-the-boundary doctrine, resilience tests |
| `references/legal-ethics.md` | Address ToS, robots.txt, GDPR, copyright, IDV limits, defensible scraping practices |

**Apify deployment doctrine** (Apify-specific - read for Apify-deployment specifics. Note: the **failure taxonomy** inside `error-handling-ppe.md` is platform-agnostic and is summarized in Phase 4, so a non-Apify reader takes the taxonomy from Phase 4 and skips the rest of that file):

| Read this | When you need to… |
|---|---|
| `references/apify-patterns.md` | Set up `actor.json`, input_schema, Apify proxy/session config, KV Store cache, autoscaling, memory tiers, MCP Standby mode |
| `references/error-handling-ppe.md` | Implement the Apify graceful-exit pattern, structure dataset error output, configure PPE events, handle free-plan users (the failure *taxonomy* it documents is platform-agnostic; the *implementation* is Apify) |

---

## What this skill is not

- **Not a copy of the platform/library docs.** Use the Crawlee docs, Playwright docs, or Apify Academy directly when you need API specs. This skill is the *opinionated layer above* - what to do, in what order, with what trade-offs.
- **Not a replacement for site-specific knowledge.** When working on a scraper for a known target (Vivino, Wine-Searcher, Instagram), check your prior notes and the target's existing scraper source first.
- **Not a license to hit unfamiliar sites without checking ToS.** See `references/legal-ethics.md`.
