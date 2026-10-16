# Testing - Fixtures, Mocks, No Live Network

The local test suite never makes a real network request. Not to the target site, not to your platform or runtime, not to any third-party API. This holds regardless of where the scraper runs - standalone script, VPS cron, serverless function, web-app backend, or Apify Actor. Treat it as non-negotiable - and once you live with it, you discover it's also better engineering.

This page is the testing doctrine.

---

## Why fixtures, not live calls

Three reasons:

1. **Local IP protection.** A test that hits the target site exposes the local IP to the target's anti-bot. Even one flagged request can poison the IP for that domain. Fixtures sidestep this entirely.
2. **Determinism.** Live tests fail randomly when the target is slow, blocks the request, or changes content. Fixture-based tests have one cause of failure: the code. They are reliable.
3. **Speed.** A network round trip is 200ms minimum, 5+ seconds with browsers. A fixture-based test runs in milliseconds. The full test suite runs in seconds, not minutes.

The trade-off is real: fixtures go stale. The mitigation is to refresh fixtures from real runs (in whatever environment is permitted to scrape - an Apify run, a sanctioned remote host) and version them alongside the code.

---

## What gets mocked

Mock everything that crosses the process boundary - your HTTP client, your platform/runtime SDK, and any third-party SDKs. On a standalone scraper that means your DB client, cache, and queue; on an Apify Actor it means the Apify SDK. Same discipline either way.

| Component | Mock with |
|---|---|
| `got-scraping` (your HTTP client) | Vitest mock returning fixture HTML/JSON |
| `firecrawl` (Firecrawl SDK) | Mock returning fixture markdown |
| Your output sink (DB client, cache, queue) | In-memory mock / spy that captures writes |
| Anthropic SDK / OpenAI SDK | Mock returning fixture responses |
| `fs` (when reading user files) | Use a temp file or in-memory buffer |

Platform-SDK boundary (Apify example):

| Component | Mock with |
|---|---|
| `Actor.charge()` | Spy that records calls; returns `{ eventChargeLimitReached: false }` by default |
| `Actor.openKeyValueStore()` | In-memory map mock |
| `Actor.pushData()` | Spy that captures pushed records |
| `Actor.getInput()` | Returns test input directly |
| `Actor.setStatusMessage()` | Spy |
| Apify Client | Mock |

The pattern is the same in all cases: **the test sets up the world, calls the function under test, asserts on the outputs**.

---

## Stack: Vitest

The recommended default is Vitest. Reasons: faster than Jest, native ESM/TS support, compatible API.

`vitest.config.ts`:

```ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',
    globals: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
    },
    setupFiles: ['./tests/setup.ts'],
    // tests must never need network
    testTimeout: 10000,
  },
});
```

`tests/setup.ts`:

```ts
import { beforeEach, vi } from 'vitest';

// Block all unmocked HTTP. If a test forgets to mock something,
// it fails fast instead of hitting the real network.
beforeEach(() => {
  vi.spyOn(global, 'fetch').mockImplementation(() => {
    throw new Error('fetch() called in tests - mock it.');
  });
});
```

Run: `npx vitest run` for one-shot, `npx vitest` for watch mode (local only, never CI).

---

## Capturing fixtures

Fixtures come from real runs in an environment permitted to scrape - never from local scraping. The capture flow (Apify shown; on a standalone host, swap the KV Store write for a write to local disk or object storage):

1. Deploy a debug build that saves the raw HTML or JSON response to durable storage (Apify KV Store, a file, an S3 bucket) on every request.
2. Run it on a representative input.
3. Download the saved snapshots.
4. **Scrub PII**: remove emails, phone numbers, names, internal IDs that aren't necessary for the test.
5. Save to `tests/fixtures/` with descriptive names: `wine-search-opus-one.json`, `wine-detail-margaux-2015.html`.

```ts
// In the scraper (debug build - Apify example):
import { Actor } from 'apify';

async function captureForFixture(name: string, content: string | object) {
  if (process.env.CAPTURE_FIXTURES === 'true') {
    const store = await Actor.openKeyValueStore('fixtures');
    const value = typeof content === 'string' ? content : JSON.stringify(content, null, 2);
    await store.setValue(name, value);
  }
}

// then in your route handler:
const html = await response.text();
await captureForFixture(`page-${request.userData.label}-${Date.now()}`, html);
```

The CAPTURE_FIXTURES env var is set only on the runs whose purpose is fixture capture.

---

## Test structure

A typical extractor test:

```ts
// tests/extractors/wine.test.ts

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import * as cheerio from 'cheerio';
import { extractWine } from '../../src/extractors/wine';

describe('extractWine', () => {
  it('extracts all fields from a complete wine page', () => {
    const html = readFileSync('tests/fixtures/wine-detail-margaux-2015.html', 'utf-8');
    const $ = cheerio.load(html);
    
    const result = extractWine($, 'https://www.example.com/wine/12345');
    
    expect(result).toMatchObject({
      id: '12345',
      name: 'Château Margaux',
      vintage: 2015,
      rating: expect.any(Number),
      price: {
        amount: expect.any(Number),
        currency: 'EUR',
      },
    });
  });
  
  it('returns partial result when price is missing', () => {
    const html = readFileSync('tests/fixtures/wine-detail-no-price.html', 'utf-8');
    const $ = cheerio.load(html);
    
    const result = extractWine($, 'https://www.example.com/wine/789');
    
    expect(result.price).toBeUndefined();
    expect(result.name).toBe('Some Wine'); // other fields still extract
  });
  
  it('falls back to LD-JSON when CSS selectors fail', () => {
    const html = readFileSync('tests/fixtures/wine-detail-redesigned.html', 'utf-8');
    const $ = cheerio.load(html);
    
    // The 'redesigned' fixture has LD-JSON but the old class names removed
    const result = extractWine($, 'https://www.example.com/wine/456');
    
    expect(result.name).toBeDefined();
    expect(result.price?.amount).toBeGreaterThan(0);
  });
  
  it('returns error record when HTML is malformed', () => {
    const html = '<html>broken';
    const $ = cheerio.load(html);
    
    const result = extractWine($, 'https://www.example.com/wine/0');
    
    expect(result).toMatchObject({
      error: true,
      errorType: expect.any(String),
    });
  });
});
```

Coverage targets per extractor:
- Happy path: all fields extracted from a complete fixture.
- Missing fields: each optional field independently missing.
- Selector fallback: a fixture where primary selectors fail and fallbacks engage.
- Malformed input: garbage HTML, returns clean error record.

---

## Mocking the Apify SDK

Example: mocking the Apify SDK (mock your runtime's SDK the same way elsewhere). The Apify SDK has many touch points. Mock at the boundary:

```ts
// tests/__mocks__/apify.ts

import { vi } from 'vitest';

export const Actor = {
  init: vi.fn(),
  exit: vi.fn(),
  fail: vi.fn(),
  getInput: vi.fn(),
  pushData: vi.fn(),
  setStatusMessage: vi.fn(),
  charge: vi.fn(() => Promise.resolve({ eventChargeLimitReached: false })),
  openKeyValueStore: vi.fn(() => Promise.resolve({
    getValue: vi.fn(),
    setValue: vi.fn(),
  })),
  createProxyConfiguration: vi.fn(() => Promise.resolve({
    newUrl: vi.fn(() => 'http://mock-proxy:8080'),
  })),
};

export const log = {
  info: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  debug: vi.fn(),
};
```

In the Vitest config or per-test setup:

```ts
import { vi } from 'vitest';
vi.mock('apify');  // uses tests/__mocks__/apify.ts automatically
```

Then in tests (Apify example; the general analogue is asserting that the irreversible side effect - a DB commit, a charge - fires only on the success path):

```ts
import { Actor } from 'apify';
import { runMain } from '../../src/main';

it('charges PPE on success', async () => {
  vi.mocked(Actor.getInput).mockResolvedValue({ wineId: 12345 });
  // ... mock got-scraping to return fixture data
  
  await runMain();
  
  expect(Actor.charge).toHaveBeenCalledWith({
    eventName: 'wine-fetched',
    count: 1,
  });
});

it('does not charge on error', async () => {
  vi.mocked(Actor.getInput).mockResolvedValue({ wineId: null });  // invalid input
  
  await runMain();
  
  expect(Actor.charge).not.toHaveBeenCalled();
  expect(Actor.pushData).toHaveBeenCalledWith(
    expect.objectContaining({ error: true, errorType: 'INVALID_INPUT' })
  );
});
```

---

## Mocking got-scraping

```ts
import { vi } from 'vitest';

// Module-level mock
vi.mock('got-scraping', () => ({
  gotScraping: vi.fn(),
}));

// Per-test setup
import { gotScraping } from 'got-scraping';
import { readFileSync } from 'fs';

beforeEach(() => {
  vi.mocked(gotScraping).mockImplementation(async (options: any) => {
    const url = typeof options === 'string' ? options : options.url;
    
    // Return different fixtures based on URL
    if (url.includes('/api/explore/explore')) {
      return {
        statusCode: 200,
        body: JSON.parse(readFileSync('tests/fixtures/vivino-explore.json', 'utf-8')),
      } as any;
    }
    if (url.includes('/api/wines/')) {
      return {
        statusCode: 200,
        body: JSON.parse(readFileSync('tests/fixtures/vivino-wine.json', 'utf-8')),
      } as any;
    }
    
    throw new Error(`Unexpected URL in test: ${url}`);
  });
});
```

The "throw on unexpected URL" rule prevents tests from accidentally hitting unmocked endpoints.

---

## Mocking Crawlee crawlers

When testing route handlers in isolation, you don't need a real crawler. Construct the context manually:

```ts
import { type CheerioCrawlingContext } from 'crawlee';
import * as cheerio from 'cheerio';

function makeCheerioContext(html: string, url: string): CheerioCrawlingContext {
  return {
    $: cheerio.load(html),
    request: { url, loadedUrl: url, userData: {}, retryCount: 0 } as any,
    response: { statusCode: 200, headers: {} } as any,
    body: html,
    log: { info: vi.fn(), warning: vi.fn(), error: vi.fn(), debug: vi.fn() } as any,
    enqueueLinks: vi.fn(),
    pushData: vi.fn(),
    addRequests: vi.fn(),
    sendRequest: vi.fn(),
    proxyInfo: undefined,
    session: undefined,
    crawler: {} as any,
  } as any;
}

// then:
it('extracts product list', async () => {
  const html = readFileSync('tests/fixtures/products-list.html', 'utf-8');
  const ctx = makeCheerioContext(html, 'https://example.com/products');
  
  await listingHandler(ctx);
  
  expect(ctx.pushData).toHaveBeenCalledTimes(20);
  expect(ctx.enqueueLinks).toHaveBeenCalledWith(expect.objectContaining({
    label: 'DETAIL',
  }));
});
```

This isolates the handler from Crawlee's full lifecycle. You're testing your code, not the framework.

---

## Coverage targets

For a scraper of moderate complexity, aim for:

| Area | Coverage |
|---|---|
| Extractors (pure functions) | ≥ 90% line coverage; every field tested |
| Route handlers | ≥ 80%; happy path + 2 error paths each |
| URL builders / filter builders | 100% (they're combinatorial; all branches exercisable) |
| Error handler / graceful exit | Every error type produces correct shape and status |
| Billing / charging (Apify PPE, or any metered side effect) | Every charge condition tested (success, retry, partial, error) |
| Schema validation | Both pass and fail paths |

100% coverage is not the goal. **Critical-path coverage is the goal.** A line of glue code that only runs once at startup doesn't need a test. The price parser does.

---

## Tests for resilience

Beyond happy-path tests, write resilience tests. These prove the scraper handles real-world conditions.

```ts
describe('resilience', () => {
  it('handles target site returning 403 on every request', async () => {
    vi.mocked(gotScraping).mockResolvedValue({ statusCode: 403, body: '' } as any);
    
    await runMain({ wineId: 12345 });
    
    expect(Actor.pushData).toHaveBeenCalledWith(expect.objectContaining({
      error: true,
      errorType: 'TARGET_BLOCKED',
    }));
    expect(Actor.charge).not.toHaveBeenCalled();
  });
  
  it('handles Claude API rate limit with retry', async () => {
    let callCount = 0;
    vi.mocked(anthropic.messages.create).mockImplementation(() => {
      callCount++;
      if (callCount === 1) throw new RateLimitError(...);
      return Promise.resolve({ content: [...] });  // succeeds on retry
    });
    
    await runMain(...);
    
    expect(callCount).toBe(2);
    expect(Actor.charge).toHaveBeenCalledTimes(1);  // charged once on success
  });
  
  it('handles malformed PDF input', async () => { /* ... */ });
  it('handles selector failure (site changed)', async () => { /* ... */ });
  it('handles partial multi-agent failure (3 of 6 succeed)', async () => { /* ... */ });
  it('respects eventChargeLimitReached gracefully', async () => { /* ... */ });
});
```

The resilience suite encodes the error decision table from `error-handling-ppe.md`. Every row in that table should have a corresponding test.

---

## Continuous integration

Tests run on every commit (locally, before push). Since they don't hit the network, they pass anywhere - no API keys needed, no Apify account needed, no proxy access needed.

For a private GitHub repo (or GitLab), a basic CI config:

```yaml
# .github/workflows/test.yml (in a private repo)
name: Test

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - run: npm ci
      - run: npm run test
      - run: npm run typecheck
      - run: npm run lint
```

The CI environment must have no scraping tools running and no network access to target sites. If the test suite tries to reach a target, it fails - which is correct.

---

## Fixture freshness

Fixtures go stale. A six-month-old fixture from a site that changed three months ago tests against a phantom. Discipline:

- **Refresh fixtures every 30–90 days** for active scrapers (set a calendar reminder).
- **Refresh on every selector change** - when you update a selector, capture a fresh fixture during the same sanctioned run (Apify or otherwise) that proved the new selector worked.
- **Keep the previous fixture** for one cycle. If the new fixture causes test failures and the new selector also fails on production, you can compare diffs to find what changed.

Fixtures are part of the codebase, version-controlled. Every change to a fixture is a commit with a message explaining what changed and why.

---

## What never goes in tests

- Live network calls. Anywhere. Ever.
- API keys, credentials, or production secrets. Tests use mocks; mocks don't need real keys.
- Real PII. Fixtures must be scrubbed.
- Long-running tests (>10 seconds). If a test takes that long, it's doing something wrong.
- `--watch` mode in CI. Watch is a development convenience.
