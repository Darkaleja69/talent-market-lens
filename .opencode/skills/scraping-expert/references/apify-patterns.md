# Apify Patterns - Production Configuration

This page documents the Apify-specific patterns that turn a scraper script into a production Actor. It is not a substitute for the official Apify Academy - read that first if you've never built an Actor. This is the *opinionated layer above*, encoding decisions that the documentation leaves to you.

**Scope.** This file is the **scraping-engineering configuration layer** (proxy, session pool, KV cache, autoscaling, status messages, monitoring). The Actor *scaffolding itself* - project structure, `actor.json`, `input_schema.json`, build images, and the Crawlee templates - and the MCP-server Standby setup are covered in depth by the Apify Academy and the Crawlee/Apify templates; the patterns here are the scraping-doctrine view of the same files. For the scaffolding mechanics, defer to the official docs and keep this file for the scraping-specific configuration on top.

---

## Project structure

Every Actor follows this structure. Deviations require a reason.

```
my-actor/
├── .actor/
│   ├── actor.json           # Actor metadata, build config, run constraints
│   ├── input_schema.json    # Input validation, Console UI generation
│   ├── dataset_schema.json  # Output validation (optional but valuable)
│   └── Dockerfile           # Build instructions (often inherited from a template)
├── docs/                    # Project docs (architecture, constraints, workflows)
│   ├── ARCHITECTURE.md      # Architecture, error taxonomy, decisions
│   └── resources/           # Per-project references (selectors, fixtures index)
├── src/
│   ├── main.ts              # Entry point. Apify init, input read, orchestration.
│   ├── routes.ts            # Crawler route handlers (LIST, DETAIL, etc.)
│   ├── extractors/          # Pure functions: HTML/JSON → typed objects
│   ├── filters/             # Input → URL/query parameter mapping
│   ├── parsers/             # If site-specific parsing is non-trivial
│   ├── ppe/
│   │   └── charge.ts        # Wraps Actor.charge() with safety checks
│   ├── output/              # Report generators (HTML, Markdown) for AI Actors
│   ├── utils/
│   │   ├── error-handler.ts # Graceful exit logic
│   │   ├── cache.ts         # KV Store wrapper with TTL
│   │   └── status.ts        # Wrapper around Actor.setStatusMessage()
│   ├── types.ts             # TypeScript interfaces
│   └── constants.ts         # Selectors, defaults, enums
├── tests/
│   ├── fixtures/            # Captured HTML/JSON, scrubbed of PII
│   ├── extractors.test.ts
│   ├── routes.test.ts
│   └── ...
├── package.json
├── tsconfig.json
├── vitest.config.ts
└── README.md
```

The `docs/` directory is written **before** any source code - it captures the architecture, error taxonomy, and workflow to follow when implementing. This is strongly recommended for non-trivial Actors.

---

## actor.json

The minimal viable `actor.json` for a standard run-mode Actor:

```json
{
  "actorSpecification": 1,
  "name": "my-actor",
  "title": "My Actor - Concise Tagline",
  "description": "One sentence with primary keywords.",
  "version": "0.1",
  "buildTag": "latest",
  "input": "./input_schema.json",
  "dockerfile": "./Dockerfile",
  "categories": ["E_COMMERCE", "MARKETING"],
  "minMemoryMbytes": 1024,
  "maxMemoryMbytes": 4096,
  "defaultRunOptions": {
    "build": "latest",
    "memoryMbytes": 2048,
    "timeoutSecs": 3600
  }
}
```

For an MCP Standby server, additional fields are required:

```json
{
  "usesStandbyMode": true,
  "webServerSchema": "./web_server_schema.json",
  "webServerMcpPath": "/mcp",
  "minMemoryMbytes": 256,
  "maxMemoryMbytes": 512,
  "defaultRunOptions": {
    "memoryMbytes": 256,
    "timeoutSecs": 300
  }
}
```

`usesStandbyMode: true` and `webServerMcpPath: '/mcp'` are non-negotiable for MCP. Without these, the Actor runs in standard mode and the persistent HTTP connection that MCP requires won't work.

---

## input_schema.json

The input schema validates user input AND generates the Console UI. Get it right; users see this directly.

Key fields per property:

```json
{
  "title": "Search query",
  "type": "string",
  "description": "User-visible help text. Be specific.",
  "editor": "textfield",
  "minLength": 1,
  "maxLength": 200,
  "prefill": "opus one 2015"
}
```

Common editors:
- `textfield`, `textarea` - for strings
- `stringList` - array of strings (one per line in UI)
- `requestListSources` - array of URLs/objects, the standard for crawler start URLs
- `proxy` - for `proxyConfiguration` field (renders the standard proxy widget)
- `select` - enum dropdown (also requires `enum` and optionally `enumTitles`)
- `number`, `integer`, `boolean` - direct mappings
- `json` - raw JSON for advanced users

Patterns to use:

- **Always provide a `prefill`** for required fields. It's the example the user sees first.
- **Use `sectionCaption` and `sectionDescription`** to group related fields. The Console UI renders them as collapsible sections.
- **`editor: "hidden"`** for fields that should accept programmatic input but not appear in the Console UI.

Example for a wine scraper:

```json
{
  "title": "Wine Scraper Input",
  "type": "object",
  "schemaVersion": 1,
  "properties": {
    "queries": {
      "title": "Wine search queries",
      "type": "array",
      "editor": "stringList",
      "description": "One wine name per line. Include vintage if known.",
      "prefill": ["opus one 2015", "chateau margaux 2010"]
    },
    "maxResultsPerQuery": {
      "title": "Max results per query",
      "type": "integer",
      "default": 10,
      "minimum": 1,
      "maximum": 50
    },
    "proxyConfiguration": {
      "title": "Proxy configuration",
      "type": "object",
      "editor": "proxy",
      "description": "Datacenter is sufficient for Vivino API. Use residential for Wine-Searcher.",
      "default": {
        "useApifyProxy": true,
        "apifyProxyGroups": ["SHADER"]
      },
      "sectionCaption": "Advanced settings"
    }
  },
  "required": ["queries"]
}
```

---

## Proxy configuration

Apify's proxy groups are the foundation. Three groups cover 95% of cases:

| Group | Use for | Cost (relative) |
|---|---|---|
| `SHADER` | Datacenter proxies. Default for low-protection targets. | 1× |
| `RESIDENTIAL` | Real ISP IPs. For sites with anti-bot or geo-restrictions. | ~10× |
| `GOOGLE_SERP` | Specialized for Google SERP. Pre-warmed, geographic routing. | 1.5× |

For ISP proxies (the middle tier between datacenter and residential - static residential IPs hosted in datacenter infrastructure), Apify does not have a dedicated group at the time of writing; either use `RESIDENTIAL` (with sticky sessions to keep the same IP for the duration of a session) or pass an external ISP proxy provider's URLs via `proxyUrls`. ISP is worth considering when datacenter is too hot but residential bandwidth costs are unjustified - typical sweet spot for moderate-protection e-commerce.

Configuration:

```ts
const proxyConfiguration = await Actor.createProxyConfiguration({
  groups: ['RESIDENTIAL'],
  countryCode: 'FR',
});
```

`countryCode` matters for geo-restricted content (prices, listings, language). Mismatched country between proxy and request often triggers blocks even when the IP is otherwise clean.

For per-session sticky proxies (recommended with sessions):

```ts
const url = await proxyConfiguration.newUrl(sessionId);
```

The same `sessionId` returns the same proxy for the session's lifetime. Crawlee's `useSessionPool: true` handles this automatically.

For external proxy providers, pass the URL directly:

```ts
const proxyConfiguration = await Actor.createProxyConfiguration({
  proxyUrls: [
    'http://username:password@residential-pool.brightdata.com:22225',
    // ... more URLs
  ],
});
```

---

## Session pool

Sessions are units of "user identity": one set of cookies, one proxy IP, one fingerprint. Crawlee's session pool manages them.

```ts
const crawler = new CheerioCrawler({
  proxyConfiguration,
  useSessionPool: true,
  persistCookiesPerSession: true,
  sessionPoolOptions: {
    maxPoolSize: 20,
    sessionOptions: {
      maxUsageCount: 50,    // retire after 50 successful uses
      maxErrorScore: 3,     // retire after 3 errors
    },
  },
  retryOnBlocked: true,
  maxSessionRotations: 10,
  maxRequestRetries: 5,
});
```

`retryOnBlocked: true` is the highest-value flag. Crawlee detects 403/429/503 (and browser-equivalent signals) and rotates the session automatically. Without it, you're writing rotation logic by hand.

Tuning:

- **High-volume, light protection**: pool size 20–50, `maxUsageCount` 50–100, `maxErrorScore` 3.
- **Moderate volume, moderate protection**: pool size 10–20, `maxUsageCount` 20–30, `maxErrorScore` 1–2.
- **Hard targets**: pool size 5–10, `maxUsageCount` 5–10, `maxErrorScore` 1.

The smaller the pool and shorter the session lifetime, the more "human-like" each session looks - but the more proxy credits you burn. There is a real trade-off.

---

## KV Store cache pattern

The recommended default: cache every external response in the KV Store with a 30-day TTL. This is unconditional except for live-data sources (live sports scores, real-time prices on volatile markets). For everything else, default to caching.

Implementation pattern:

```ts
import { Actor } from 'apify';
import crypto from 'crypto';

const CACHE_TTL_MS = 30 * 24 * 60 * 60 * 1000; // 30 days

interface CacheEntry<T> {
  data: T;
  timestamp: number;
}

async function withCache<T>(
  key: string,
  fetcher: () => Promise<T>,
  ttlMs: number = CACHE_TTL_MS,
): Promise<T> {
  const store = await Actor.openKeyValueStore();
  const cached = await store.getValue<CacheEntry<T>>(key);
  
  if (cached && (Date.now() - cached.timestamp) < ttlMs) {
    return cached.data;
  }
  
  const fresh = await fetcher();
  await store.setValue(key, { data: fresh, timestamp: Date.now() });
  return fresh;
}

// Usage
const cacheKey = `vivino-explore:${md5(JSON.stringify(query))}`;
const results = await withCache(cacheKey, () => fetchVivinoSearch(query));
```

Key naming convention: `{source}-{operation}:{hash_or_id}`. Examples:
- `vivino-wine:{wineId}`
- `vivino-explore:{md5(queryString)}`
- `wine-searcher-cache:{hash}`
- `redfin-listing:{listingId}`

The KV Store TTL is implemented in your code, not by the platform. Apify's KV Store has no native TTL; entries persist until explicitly overwritten. The `timestamp` check above is what enforces the TTL.

For named stores (cross-Actor sharing) vs. unnamed (per-run):
- Use **named stores** for caches that should persist across runs and be shared between Actors in the same suite.
- Use **unnamed stores** (the default) for per-run state.

---

## Autoscaling and concurrency

Crawlee's autoscaling adjusts concurrency based on system signals (CPU, memory, event loop pressure). Default behavior is usually correct, but tuning matters at scale.

```ts
const crawler = new CheerioCrawler({
  // hard upper bound (set this!)
  maxConcurrency: 20,
  // hard lower bound (rarely needed)
  minConcurrency: 1,
  // alternative: bound by request rate, not parallelism
  maxRequestsPerMinute: 600,
  // memory budget for the autoscaling decision
  autoscaledPoolOptions: {
    desiredConcurrency: 10,
    systemStatusOptions: {
      maxMemoryOverloadedRatio: 0.8,
      maxEventLoopOverloadedRatio: 0.7,
    },
  },
});
```

Practical guidance:

- **Always set `maxConcurrency`** - defaults are too aggressive for most production cases.
- **`maxRequestsPerMinute`** is often more useful than `maxConcurrency`. It enforces rate regardless of concurrency choices, which is what most anti-bot rate limiters care about.
- **Browser crawlers**: `maxConcurrency: 5–10` is the upper bound for 4 GB Actors. Each browser instance is heavy.
- **HTTP crawlers**: `maxConcurrency: 20–50` is reasonable for static sites.

---

## Status messages

`Actor.setStatusMessage()` is what shows in the Apify Console while the run is in progress. It's the user's primary feedback channel. Use it frequently.

```ts
import { Actor } from 'apify';

await Actor.setStatusMessage('Starting wine search...');
// ...
await Actor.setStatusMessage(`Searched ${count}/${total} wines`);
// ...
await Actor.setStatusMessage('Done. Pushing results to dataset.');
```

Pattern: update at every major phase boundary (start, per-batch progress, completion, error). The `isStatusMessageTerminal: true` flag freezes the message at the end of the run:

```ts
await Actor.setStatusMessage(
  `Completed: ${succeeded} successful, ${failed} failed`,
  { isStatusMessageTerminal: true },
);
```

For Actors charging PPE, the terminal status message is part of the user experience - it should explain what happened in plain terms ("Analyzed 5 wines successfully - see dataset for results").

---

## Standby mode (MCP servers)

> The full MCP Standby build (transports, `webServerMcpPath`, cold start) is covered by the Apify MCP-server docs and templates. The summary below is the scraping-side orientation; consult those resources to actually build an MCP server.

Standby mode keeps the Actor process alive between requests, exposing an HTTP server that handles incoming traffic. This is required for MCP servers because MCP clients maintain a persistent connection (SSE or stdio); a standard run mode would terminate after the first request.

Configuration in `actor.json`:

```json
{
  "usesStandbyMode": true,
  "webServerSchema": "./web_server_schema.json",
  "webServerMcpPath": "/mcp",
  "minMemoryMbytes": 256,
  "defaultRunOptions": {
    "memoryMbytes": 256,
    "timeoutSecs": 300
  }
}
```

`web_server_schema.json` documents the HTTP API for the Apify console UI and for MCP discovery:

```json
{
  "schemaVersion": 1,
  "title": "MCP Server",
  "description": "Endpoints for AI clients via Model Context Protocol.",
  "openapi": {
    "openapi": "3.1.0",
    "info": {
      "title": "MCP Server",
      "version": "1.0.0"
    },
    "paths": {
      "/mcp": {
        "post": {
          "summary": "MCP JSON-RPC endpoint",
          "responses": {"200": {"description": "MCP response"}}
        }
      }
    }
  }
}
```

Server pattern: Express + the official MCP SDK.

```ts
import { Actor } from 'apify';
import express from 'express';
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { SSEServerTransport } from '@modelcontextprotocol/sdk/server/sse.js';

await Actor.init();

const app = express();
const port = parseInt(process.env.ACTOR_WEB_SERVER_PORT ?? '4321');

const mcpServer = new Server({ name: 'example-mcp', version: '1.0.0' }, { capabilities: { tools: {} } });

// register tools...

app.post('/mcp', async (req, res) => {
  const transport = new SSEServerTransport('/mcp', res);
  await mcpServer.connect(transport);
});

app.listen(port, () => console.log(`MCP server listening on ${port}`));
```

Three rules for MCP servers:

1. **Memory: 256 MB minimum.** MCP servers handle many small requests; per-request memory is low. The 256 MB tier is correct.
2. **Idle timeout matters.** Apify shuts down idle Standby Actors after a configurable period - the field is `webServerIdleTimeoutSecs` in `actor.json` (the `webServer` prefix is required; a bare `idleTimeoutSecs` is silently ignored). The default is fine for most cases; 300 seconds (5 min) is a healthy explicit value. The trade-off is cold-start latency vs idle cost - a longer idle timeout keeps the server warm (faster responses) but bills idle compute.
3. **No `Actor.main()` pattern.** Don't wrap your code in `Actor.main()`. The Express server runs as the main process; `Actor.init()` is called once at startup.

---

## PPE billing setup

Pay-per-Event is configured in Apify Console (Monetization → Pricing) but referenced in code via `Actor.charge()`. Full details in `error-handling-ppe.md`. Quick orientation here:

```ts
// Inside the Actor, after a successful operation:
const result = await Actor.charge({
  eventName: 'search-wine',
  count: 1,
});

if (result.eventChargeLimitReached) {
  // user is on a limited plan; complete operation but don't charge again
  await Actor.setStatusMessage('Free plan limit reached for this run.');
}
```

Event names are configured in the Apify Console for the Actor; they must match exactly. The standard pattern is one event per *user-visible operation* (one search, one detail fetch, one analysis) - not one per HTTP request.

The synthetic event `apify-actor-start` fires on every run start and should be left at the default price. Don't customize it.

---

## Build images and dependencies

Apify provides several base Docker images. Pick the lightest one that includes what you need.

| Image | Includes | Use for |
|---|---|---|
| `apify/actor-node:20` | Node.js only | got-scraping, CheerioCrawler, MCP servers |
| `apify/actor-node-playwright:20` | Node + Playwright (Chrome + Firefox) | Mixed Playwright work |
| `apify/actor-node-playwright-chrome:20` | Node + Playwright Chrome only | Chrome-based scraping |
| `apify/actor-node-playwright-firefox:20` | Node + Playwright Firefox only | Camoufox base |
| `apify/actor-node-puppeteer-chrome:20` | Node + Puppeteer | Legacy Puppeteer projects |
| `apify/actor-python:3.13` | Python only | Python-based scrapers |
| `apify/actor-python-playwright:3.13` | Python + Playwright | Python browser scraping |

For Camoufox, use the Firefox base and add a build step to install Camoufox:

```Dockerfile
FROM apify/actor-node-playwright-firefox:20
RUN npm install camoufox-js
COPY . .
RUN npm install --omit=dev
CMD ["node", "src/main.js"]
```

Image size affects build time and cold-start latency. The Node-only image starts in seconds; Playwright images take longer. Don't pull Playwright if you don't need it.

---

## Memory tiers and cost

Apify pricing scales with memory × wall time. Picking the right tier matters.

| Memory | Use for |
|---|---|
| 128 MB | Trivial scripts, MCP-Standby per-request handlers (split off heavy work) |
| 256 MB | got-scraping, simple HTTP scraping, MCP servers |
| 512 MB | CheerioCrawler at moderate scale |
| 1024 MB | CheerioCrawler at high scale, light Playwright |
| 2048 MB | Standard PlaywrightCrawler, multi-agent AI Actors |
| 4096 MB | Heavy Playwright, Camoufox, multi-agent with parallel calls |
| 8192 MB | Rare. Heavy parallel browsers or massive in-memory datasets |

A reasonable default is **4096 MB for multi-agent AI Actors** and **256 MB for MCP Standby servers**. Deviations should be justified by observed memory pressure.

---

## Monitoring

Apify provides built-in monitoring via the Console:

- **Run status distribution**: ratio of SUCCEEDED / FAILED / TIMED-OUT / ABORTED runs.
- **Per-Actor success rate**: hover over any Actor to see the recent success rate.
- **Email alerts**: configurable per Actor for failed runs, low success rate, etc.

Target success rate: ≥ 95%. Below that, the Actor is flagged in the Apify Store and visibility drops.

The single most impactful action on success rate: **always exit SUCCEEDED**. See `error-handling-ppe.md`. Most Actors with 50% success rate have that score because they call `Actor.fail()` on business logic errors. Switching to graceful exit lifts the score to 90%+ without changing any scraping logic.
