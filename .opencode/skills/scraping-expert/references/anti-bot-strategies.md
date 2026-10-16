# Anti-Bot Strategies - The Escalation Ladder

This page is the playbook for getting past anti-bot detection without overpaying. The model is a four-level ladder. You always start at the lowest level that fits the diagnostic and escalate one level at a time on observed failure. Each level adds cost; never skip levels you haven't tried.

The fundamental insight: anti-bot is a **trust score**, not a binary check. Each request accumulates signals - IP reputation, TLS fingerprint, browser fingerprint, header consistency, behavioral patterns. A few suspicious signals are tolerated; many are not. Each level of this ladder reduces the number of suspicious signals, until you cross the threshold to "trusted enough".

---

## The realism principle

Before any tactic, internalize the framing. **No system that operates over the network is fully closed.** Anything transmitted between client and server can be observed, replicated, and replayed if someone is willing to spend enough effort. Anti-bot is not a wall; it is a *cost function*. The defender's goal is not to make scraping impossible but to make it expensive enough that most attackers give up. The scraper's goal is the same in reverse: not to be undetectable, but to be cheap to operate while staying under the trust threshold the target has set.

Three corollaries follow from this:

1. **Economic asymmetry decides outcomes.** A scraper that costs $0.10 per request to a target whose data is worth $0.01 has lost. A scraper that costs $0.001 to data worth $0.05 wins easily. Diagnostic-driven tool selection is fundamentally an economics exercise - pick the cheapest level of the ladder that produces results, not the most stealthy.
2. **There is no permanent bypass.** Defenders update; the techniques that worked last quarter degrade. The scraper that survives is the one with monitoring (success rate dashboards, error categorization) and rapid response, not the one with the cleverest one-shot stealth trick.
3. **The defender is also constrained.** Aggressive anti-bot creates false positives that block real users - costing the target conversions and revenue. This is why most sites don't run "I'm Under Attack" mode permanently. The defender has to keep their own threshold tunable; you operate in the gap they leave open for legitimate users.

Treat anti-bot as the cost function it is, not as an arms race to be won decisively. Pick your tools to keep the cost low and the success rate above threshold; expect to revisit the configuration every few months.

---

## The four pillars of detection

Before the escalation ladder, internalize the detection model. Every anti-bot vendor - Cloudflare, Akamai, DataDome, PerimeterX, AWS WAF - combines these four pillars. Knowing which pillar a given vendor emphasizes tells you which pillar to harden.

### Pillar 1 - IP reputation and analysis

The first thing the server learns about you is your IP. It is the cheapest signal to evaluate and the cheapest defense layer. Vendors maintain live reputation databases scored on:

- **IP type** (datacenter / ISP / residential / mobile) - datacenter is most suspicious by default.
- **ASN ownership** - Cloudflare's ASN 13335 is one entity, AWS uses several. Whole ASNs can be flagged or blocked per site policy.
- **Subnet membership** - `/24` blocks of misbehaving IPs get blanket-blocked. A "fresh" IP in a poisoned subnet is born suspicious.
- **Anonymity markers** - known VPN IPs, Tor exit nodes, open proxies are pre-flagged.
- **Abuse history** - the IP's past activity follows it forever.
- **Request velocity** - hundreds of requests per second from one address can't be human.
- **Geographic consistency** - the IP location must match the browser's claimed timezone, locale, and stay stable within a session.

Defense at this layer: proxy rotation with the right tier (see `diagnostic.md` Question 4), country alignment, request-rate budget per IP.

### Pillar 2 - Browser and connection fingerprinting

Once the IP passes, the system inspects how you appear at three layers:

- **TCP/IP layer** (the deepest, least-considered): TTL value, MSS, initial window size, TCP options. Each OS has a signature here. If you claim to be Chrome on Windows but your TTL is 64 (Linux default) instead of 128 (Windows default) because you proxy through a Linux box, the inconsistency is detected. This is below the application layer entirely; libraries like got-scraping handle the application layer but the underlying OS still leaks.
- **TLS layer** (JA3, JA4, JA4+): the TLS handshake's cipher list, extensions, curves, and ordering produce a fingerprint specific to the implementation. Python `requests` has a distinct JA3; Chrome has another. Vendors maintain blocklists of automation library JA3s.
- **Browser layer** (HTTP headers + JavaScript-collected attributes): User-Agent, header set and order, sec-ch-ua values, viewport, screen, timezone, locale, fonts, Canvas/WebGL/AudioContext outputs, navigator.webdriver, plugin and media device counts.

The detection mode is **consistency**, not absolute values. A Chrome User-Agent with a Firefox JA3 fingerprint is detected. A Windows User-Agent with macOS-only fonts is detected. A Chrome 120 declaring support for an API that only exists in Chrome 125 is detected. Internally consistent fingerprints survive; cobbled-together ones don't.

Defense at this layer: a real browser (Playwright/Camoufox) for the TLS+browser layers, or a TLS-impersonating HTTP client (`got-scraping`, `curl-cffi`, `tls-client`) for HTTP-only crawling. **Never hand-craft fingerprints**; use Crawlee's generator, which produces internally consistent sets.

### Pillar 3 - Behavioral analysis

Even if IP and fingerprint look right, the system watches *how* you behave:

- **Mouse movements**: human paths have hesitation, jitter, corrections. Bots either skip the events entirely or produce robotic straight lines.
- **Scrolling**: humans scroll unevenly, sometimes back, sometimes pausing mid-page. Bots scroll in linear, perfectly-uniform patterns or skip scrolling entirely.
- **Typing rhythm** (keystroke dynamics): humans type in bursts with natural pauses. Bots fill fields instantly or at a perfectly steady rhythm.
- **Navigation flow**: humans usually enter at the homepage, browse a category, then arrive at a detail page. Bots jump straight to deep URLs.
- **Cookie banner interaction**: humans see and dismiss the banner before content. Bots that go straight to data without acknowledging the banner are flagged.
- **Session duration variance**: humans linger inconsistently. Bots leave instantly after extracting.
- **Continuous validation**: PerimeterX/HUMAN, in particular, runs sensors throughout the session, not once at start. Passing the entry check is not enough - every action must look human.

Defense at this layer: humanization in the browser (gaussian delays, scroll, hover, dismiss banners), front-door navigation flow, persistent cookies/sessions to look like a returning user.

### Pillar 4 - Active challenges

When pillars 1–3 produce a borderline trust score, the site issues a challenge:

- **Visible CAPTCHAs**: reCAPTCHA v2 checkbox, image grids ("select all traffic lights"), hCaptcha, Cloudflare Turnstile when escalated.
- **Invisible challenges**: reCAPTCHA v3 (silent score), Turnstile in default mode (background JS check), Akamai sensor data POSTs.
- **Proof-of-Work / Crypto challenges** (Akamai, Cloudflare): the browser is forced to spend CPU cycles solving a cryptographic puzzle. Slows scrapers without affecting humans.
- **JavaScript execution challenges**: small scripts that compute something only a real browser can compute, then submit a token. No JS runtime = no token = no access.

Defense at this layer: real browser (it solves invisible challenges automatically), or managed API (handles solving). For visible CAPTCHAs, the right answer is almost always to fix pillars 1–3 so the CAPTCHA isn't triggered in the first place; CAPTCHA-solving services are a last resort.

### What's coming next

The CAPTCHA model is being replaced by **Private Access Tokens** (Privacy Pass standard, deployed by Apple and Cloudflare). Trusted devices present a cryptographic token issued by their platform; sites verify the token without exposing identity. For scrapers, this means: legitimate users no longer see CAPTCHAs, but scrapers still face them more often. The contrast widens.

---

## Level 1 - HTTP with consistent fingerprint

**Cost**: minimal. **Solves**: rate limiting, naive header checks, basic IP reputation.

This is the default. Most sites in the wild are at this level of protection.

### What you change

- Use `got-scraping` (TLS fingerprint matches a real browser).
- Let it generate consistent header sets (don't hand-roll headers).
- Use datacenter proxies (on Apify: the `SHADER` group) for IP rotation.
- Set a realistic `User-Agent` and `Accept-Language`.
- Add a `Referer` matching a plausible navigation source.

### Why it works

Naive scrapers (Python `requests`, Node `fetch`) have a distinct JA3/JA4 TLS fingerprint that doesn't match any real browser. WAFs maintain blocklists of these signatures. `got-scraping` ships a TLS stack that produces browser-like fingerprints - Chrome, Firefox, or Safari, depending on configuration.

Datacenter proxies are cheap and abundant. They get blocked sooner than residential, but for sites with light protection they last long enough.

### What still trips this level

- Sites that fingerprint the browser via JavaScript (Cloudflare's `cf_chl_jschl_tk`, Akamai's `_abck`, etc.). The TLS is fine, but no JS execution = no challenge solving = block.
- Targets that geo-restrict by IP and your datacenter proxies are flagged as VPN.
- Volume thresholds (10 000+/hour from a single worker will burn a datacenter proxy pool fast).
- **TCP/IP layer detection** (rare but real): if you proxy a Chrome-on-Windows User-Agent through a Linux box, the underlying TCP TTL is 64 (Linux default) instead of 128 (Windows default). Sophisticated anti-bot stacks compare TCP-layer signals to the claimed OS. Mitigation: when this is the suspected cause, the proxy itself must run on the matching OS, or the request must terminate at a cloud proxy network's edge where the OS is opaque (e.g. Apify's proxy infrastructure - TCP signals come from Apify's edge, not from your local stack).

### Code pattern

```ts
import { gotScraping } from 'got-scraping';

const response = await gotScraping({
  url: targetUrl,
  proxyUrl,
  headerGeneratorOptions: {
    browsers: ['chrome'],
    operatingSystems: ['windows', 'macos'],
    locales: ['en-US'],
  },
  retry: {
    limit: 2,
    statusCodes: [408, 429, 500, 502, 503, 504],
    methods: ['GET'],
  },
  timeout: { request: 30000 },
});
```

Inside Crawlee, `BasicCrawler` or `HttpCrawler` plus `gotScraping` covers this transparently.

---

## Level 2 - Sessions and proxy rotation

**Cost**: same proxies, more orchestration. **Solves**: per-session rate limiting, IP cooldowns, lightweight behavioral checks.

When Level 1 starts giving 429/403 after the first 50–500 requests from a given IP, the issue is not the technique - it's the IP burning out. Sessions fix this.

### What you change

- Enable Crawlee's session pool (`useSessionPool: true`).
- Configure the pool: 10–50 sessions, each with an error budget and a usage limit.
- Pair sessions with sticky proxies (one proxy per session for the session's lifetime).
- Mark sessions bad on failure; the pool retires them and creates new ones.

### Why it works

A "session" is a cohesive unit: same cookies, same fingerprint, same IP, same Accept-Language, same viewport. From the target's perspective, this looks like one user browsing. Real users don't make 1000 requests with the same fingerprint and 1000 different IPs.

Crawlee's pool handles all of this. Each session has a `userData`, can be marked good/bad/retired, and is automatically replaced when burnt. You never write the rotation logic by hand.

### Configuration

```ts
const crawler = new CheerioCrawler({
  proxyConfiguration,
  useSessionPool: true,
  persistCookiesPerSession: true,
  sessionPoolOptions: {
    maxPoolSize: 20,
    sessionOptions: {
      maxUsageCount: 50,    // retire after 50 requests
      maxErrorScore: 3,     // retire after 3 errors
    },
  },
  // tell Crawlee to retry the request with a new session on a blocked status
  retryOnBlocked: true,
  // accept that some sessions will burn - give us extra rotations before giving up on the request
  maxSessionRotations: 10,
  maxRequestRetries: 5,
});
```

`retryOnBlocked: true` is the magic flag - Crawlee detects 403/429/503 (and their browser equivalents) and rotates the session automatically.

### What still trips this level

- WAF challenges that need JS execution.
- Behavioral analysis that compares timing patterns within a session (you scroll instantly, click without hesitation).
- Geo-restricted sites where datacenter IPs are flagged regardless of session.

### The clearance cookie / token replay pattern

When a site issues a clearance cookie or session token after a JS challenge - `cf_clearance` (Cloudflare), `_abck` (Akamai), `datadome` (DataDome), Kasada `KP_*` cookies - that token is reusable across many requests within its TTL (typically 30 minutes to several hours). This is the single highest-leverage optimization for browser-based scrapers: solve once, scrape many.

The pattern:

1. Spin up a Playwright/Camoufox session and navigate to the homepage. Let the browser solve the JS challenge naturally.
2. Extract the clearance cookie(s) from the browser context.
3. Pass those cookies to a much cheaper HTTP client (`got-scraping`, CheerioCrawler) and crawl the rest of the site at HTTP speed.
4. Watch for cookie expiry; when the next request returns 403, refresh by re-running the browser solve.

```ts
// 1. Browser solve
const browser = await firefox.launch();
const ctx = await browser.newContext();
const page = await ctx.newPage();
await page.goto('https://www.target.com/');
await page.waitForLoadState('networkidle');
const cookies = await ctx.cookies();
const clearanceCookie = cookies.find(c => c.name === 'cf_clearance');
const userAgent = await page.evaluate(() => navigator.userAgent);
await browser.close();

// 2. HTTP-speed crawl, re-using the cookie + matching UA
const cookieHeader = `cf_clearance=${clearanceCookie!.value}`;
for (const url of urls) {
  const response = await gotScraping({
    url,
    headers: {
      'User-Agent': userAgent,
      'Cookie': cookieHeader,
    },
    proxyUrl: stickyProxyUrl,  // same IP as browser solve!
  });
  // process
}
```

Two non-negotiables:
- **Same IP** for the browser solve and the HTTP requests. Cloudflare and similar systems bind the clearance cookie to the IP that solved it; using a different IP invalidates the token immediately.
- **Same User-Agent** and ideally same fingerprint set. The token verification can include a UA hash check.

Token replay reduces cost on protected sites by 50–95% depending on the volume per session. Treat it as the default optimization once a JS-challenge target is in scope.

---

## Level 3 - Throttling and humanization

**Cost**: lower throughput, residential proxies, longer wall time. **Solves**: behavioral analysis, geo-restrictions, aggressive WAFs.

When Level 2 still produces 403s, the problem is no longer "you're a bot" but "you're an obvious bot from a bad neighborhood".

### What you change

- Switch to residential proxies (Apify: the `RESIDENTIAL` group) with country code matching the user-facing locale of the data.
- Reduce `maxConcurrency` aggressively (2–5 instead of 10–20).
- Add randomized delays between requests (1–5 seconds, drawn from a distribution).
- Add request-handler delays inside the handler (gaussian, not uniform - humans don't have flat distributions).
- For browser-based crawlers: scroll progressively, hover before clicking, randomize viewport per session.
- Vary request order. Don't process IDs sequentially; shuffle the queue.

### Why it works

Residential IPs come from real ISPs (Comcast, Orange, BT). A residential pool is shared with real users - blocking the IP also blocks legitimate visitors. Anti-bot systems are conservative on residential.

Throttling and humanization defeat behavioral analysis. The signal "scrolls 3 px in 50 ms" or "clicks within 100 ms of page load" is a fingerprint of automation. Random gaussian delays look human.

### Configuration

```ts
const crawler = new PlaywrightCrawler({
  // on Apify: Actor.createProxyConfiguration({ groups: ['RESIDENTIAL'], countryCode: 'FR' });
  // standalone: pass your residential proxy URL directly
  proxyConfiguration: await Actor.createProxyConfiguration({
    groups: ['RESIDENTIAL'],
    countryCode: 'FR',
  }),
  useSessionPool: true,
  maxConcurrency: 3,
  maxRequestsPerMinute: 30,  // hard cap
  preNavigationHooks: [
    async ({ page }) => {
      // gaussian delay before navigation
      const ms = Math.max(500, gaussian(2000, 800));
      await new Promise(r => setTimeout(r, ms));
      // randomize viewport per session
      const viewports = [{w: 1920, h: 1080}, {w: 1366, h: 768}, {w: 1536, h: 864}];
      const v = viewports[Math.floor(Math.random() * viewports.length)];
      await page.setViewportSize({ width: v.w, height: v.h });
    },
  ],
  postNavigationHooks: [
    async ({ page }) => {
      // post-load humanization: small scroll
      await page.evaluate(() => window.scrollBy({
        top: Math.floor(Math.random() * 300),
        behavior: 'smooth',
      }));
      await new Promise(r => setTimeout(r, gaussian(800, 300)));
    },
  ],
  async requestHandler(ctx) { /* ... */ },
});

function gaussian(mean: number, stddev: number): number {
  // Box-Muller transform
  const u = 1 - Math.random();
  const v = Math.random();
  const z = Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  return mean + z * stddev;
}
```

### Navigation flow matters too

Real users rarely access deep URLs directly. They search from the homepage, click a link, navigate. Direct hits to `/find/opus+one/2015/us/usd` are suspicious to behavioral analysis (Wine-Searcher example). The fix: start at the homepage, perform the search via form submission, follow the link to the detail page.

```ts
// Anti-pattern: direct deep link
await page.goto('https://www.wine-searcher.com/find/opus+one/2015/us/usd');

// Pattern: simulate the navigation flow
await page.goto('https://www.wine-searcher.com/');
await page.waitForSelector('input[name="Xkwic"]');
await page.fill('input[name="Xkwic"]', 'opus one 2015');
await new Promise(r => setTimeout(r, gaussian(500, 200)));
await page.click('button[type="submit"]');
await page.waitForLoadState('domcontentloaded');
```

### Cookie banners and session continuity

Anti-bot systems explicitly track whether a visitor dismissed the cookie/GDPR banner before consuming content. A scraper that goes straight to data without acknowledging the banner is flagged. Always handle the banner first:

```ts
async function dismissCookieBanner(page: Page) {
  // Common patterns
  const selectors = [
    'button:has-text("Accept")',
    'button:has-text("Accept All")',
    'button:has-text("I agree")',
    '#onetrust-accept-btn-handler',
    '[aria-label*="accept" i]',
  ];
  for (const sel of selectors) {
    const btn = page.locator(sel).first();
    if (await btn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await btn.click();
      await page.waitForTimeout(gaussian(500, 200));
      return true;
    }
  }
  return false;
}
```

Beyond banners, **session continuity** is itself a behavioral signal. A scraper that starts a fresh browser context for every request looks like a string of first-time visitors - which is unnatural. Persist cookies and storage across related requests within the same session pool entry. Crawlee's `persistCookiesPerSession: true` does this; on standalone scrapers, use Playwright's `storageState` option to save and reuse session state.

### Time the runs intentionally

Anti-bot systems are most aggressive during peak traffic to the target site. Running large batch jobs during off-peak hours for the target's primary user base (for US e-commerce: late morning to early afternoon Pacific) reduces the likelihood of triggering rate-based challenges. For a new scraper against a sensitive site, the first deployment should be a small probe during a quiet period.

### Vary entry points at scale

When running hundreds of concurrent jobs against the same site, uniform navigation patterns become a fingerprint themselves - even across different IPs. If every job goes "homepage → search bar → submit → detail page", the aggregate traffic looks robotic. Mix entry points: some jobs start at the homepage, some at category pages, some via search. The aggregate looks more organic, and the cost is small (a tiny variation in code paths).

This pairs naturally with horizontal autoscaling: instead of a single worker running 1000 concurrent requests, run multiple instances (e.g. several Apify Actor runs, separate processes, or serverless invocations) with slightly different starting strategies.

### What still trips this level

- Engine-level fingerprint detection (Cloudflare advanced, HUMAN Security, DataDome with full sensors).
- TLS fingerprint inconsistencies between Crawlee's defaults and Camoufox (already mentioned: turn one of them off).
- Sites with active CAPTCHA challenges that fire regardless of behavior.

---

## Level 4 - Engine-level stealth or managed API

**Cost**: high. **Solves**: every level above, at a price.

This is where you stop and ask: should we build this ourselves, or pay someone?

### Option A - Camoufox (build it yourself)

Camoufox is the only open-source tool that consistently passes 0% headless detection. It modifies Firefox at the C++ level - no JavaScript patches that detection systems can see through. Use when:

- You have ongoing volume on a hard target (Camoufox per-call cost amortizes over many requests).
- You want full control over the scraper.
- The target has anti-bot but isn't pathological (LinkedIn-grade is borderline).

See `tool-ladder.md` for the full Camoufox setup. The key configuration tweaks for hardest targets:

```ts
import { launchOptions as camoufoxLaunchOptions } from 'camoufox-js';

const options = await camoufoxLaunchOptions({
  headless: 'virtual',     // virtual display via xvfb if pure headless leaks
  proxy: residentialProxyUrl,
  geoip: true,             // align timezone, locale, screen size with proxy IP
  humanize: true,          // built-in mouse/scroll/click humanization
  block_images: false,     // don't block images on hardest targets - anti-bot may detect missing
  block_webrtc: true,      // prevent WebRTC IP leak that exposes datacenter origin
  os: 'windows',           // pin OS for fingerprint stability
  geoip_country: 'US',     // explicit if not detected from proxy
});
```

### Option B - Managed scraping API (pay for it)

When the target is one you'll only hit occasionally, or when DIY economics don't work, use a managed provider. They take the URL, return the rendered page or extracted data. See `managed-apis.md`.

The right choice depends on:
- Volume (managed is cheap at low volume, expensive at high)
- Target hardness (LinkedIn-grade economics nearly always favor managed)
- Time-to-market (managed wins by days)
- Existing credits the user has burned (Firecrawl is often already in the stack)

### Hybrid pattern

For multi-source scrapers, mix levels:

- Wine search across multiple sites: Vivino via internal API (Level 1), Wine-Searcher via Camoufox (Level 4).
- Real estate aggregation: Redfin via CheerioCrawler (Level 2), Zillow via managed API (Level 4).

Your scraper orchestrates the mix (a standalone script, a worker process, or an Apify Actor / MCP server). Each source's protection level is independent.

---

## Per-vendor playbook

Quick reference for the major anti-bot vendors. Read in conjunction with the diagnostic in `diagnostic.md`.

### Cloudflare

Default WAF: Level 1–2 (HTTP + sessions + datacenter often enough; residential as fallback).
Bot Fight Mode: Level 3 (browser + residential).
Under Attack Mode: Level 4 (Camoufox or managed API). Some sites enable this only intermittently - check before assuming it's permanent.

What to know about Cloudflare specifically:
- **Shared reputation across the Cloudflare network**: an IP flagged on one Cloudflare-protected site can be blocked on others. This is a network effect - millions of sites share signals.
- **Turnstile** runs invisible challenges (TLS handshake analysis, browser behavior) for low-risk traffic, only escalating to a visible challenge on suspicion. Most users never see it.
- **JS challenges issue a `cf_clearance` cookie** valid for 30+ minutes - solving once and reusing the cookie within that window dramatically reduces challenge volume.

### Akamai Bot Manager

Standard configuration: Level 3 (browser + residential, full fingerprints).
Aggressive (e.g., banking, ticketing): Level 4. Akamai's behavioral analysis is mature; humanization at Level 3 is often not enough.

What to know about Akamai specifically:
- **Sensor data**: Akamai-protected pages embed JavaScript that captures mouse movements, keystroke timing, scroll depth, tab focus, and POSTs them to `/_bm/...` or `/akamai/...` endpoints. Without the sensor data, the session is flagged as bot.
- **Session flow tracking**: Akamai evaluates the entire navigation sequence, not individual requests. Direct-to-deep-URL fails; homepage→category→detail is much more likely to pass.
- **Edge-level integration**: Akamai operates at the CDN edge, correlating IP, ASN, request velocity, and geolocation in real time. Inconsistency at any layer feeds into one trust score.
- **Bot Score is exposed in headers** (`Akamai-Bot-Score`, `True-Client-IP`) on some configurations - useful diagnostically.

### PerimeterX / HUMAN Security

Always Level 4. PerimeterX's behavioral analysis cannot be defeated reliably with Level 3 humanization. Camoufox + residential + extensive humanization, or a managed API.

What to know about HUMAN/PerimeterX specifically:
- **Continuous validation**: client-side sensors run throughout the session, not just at entry. A scraper that passes the first check but behaves robotically five minutes in is still flagged. Every action must continue to look human.
- **Deep fingerprinting**: WebGL, Canvas, AudioContext, font enumeration, plugin counts, mobile motion sensors. The combined fingerprint is hard to spoof convincingly.
- **Automation framework leaks**: PerimeterX specifically tests for Selenium markers (`navigator.webdriver = true`), Puppeteer headless mode (SwiftShader rendering), and HTTP header order anomalies.
- **Cookies**: `_px*` cookies are the runtime state; clearing them mid-session resets the session and triggers re-validation.

### DataDome

Level 3 sometimes works on lighter configurations. Level 4 for the standard production setup.

What to know about DataDome specifically:
- **Sub-2ms scoring**: every request is scored against ML-trained models in under 2 ms. This means your scraper has essentially no opportunity to "learn" its way past - feedback is immediate and final.
- **Cross-platform coverage**: DataDome protects browsers, mobile apps, and APIs uniformly. A scraper that disguises browser traffic but hits an API endpoint with bare requests is caught at the API.
- **Adaptive learning**: DataDome's models are continuously retrained on bot traffic patterns. Techniques that worked last month often fail this month - assume non-stationarity.
- The `datadome` cookie is the session token; once issued, reusing it within its TTL avoids re-challenges.

### AWS WAF

Generally Level 2–3. AWS WAF rules are configured by the site owner, not by AWS. Wide range of difficulty. Probe and react.

What to know about AWS WAF specifically:
- **Managed Rule Groups**: AWS provides prebuilt rule sets including "Known Bad Inputs" and "IP Reputation List" that block scrapers and impersonators of major search engine bots. Spoofing as Googlebot fails immediately on most AWS WAF deployments.
- **Datacenter IP blocking**: AWS WAF customers often add a managed rule that blocks all traffic from cloud provider IP ranges (including AWS itself, ironically). Datacenter proxies often fail trivially.
- **Custom rate-limiting rules**: site-specific. Probe to learn the threshold, then stay below it.
- **Configurable difficulty**: a basic AWS WAF deployment is easy; an enterprise deployment with all the rule groups + custom logic + behavioral can rival Cloudflare or Akamai.

### Imperva (Incapsula)

Level 2–3. Less aggressive than the dedicated vendors. Browser + residential typically passes.

### Kasada

Level 4. Kasada uses heavily obfuscated JavaScript that issues per-session, dynamic cryptographic puzzles (often shipped as `proof.js` or similar). The proof token derives from floating-point quirks, canvas output, hardware-specific platform APIs, and timing characteristics. Naive headless clients fail because they can't execute the obfuscated code; minimally-stealthed browsers fail because the proof picks up automation markers.

What to do:
- Real browser execution is mandatory. Camoufox handles this; standard Playwright with Crawlee fingerprints often does too.
- The obfuscated JS *can* be evaluated live - once it runs in a real browser, the resulting proof token is extractable. Combine token replay (above) with real-browser solve.
- Don't try to reverse-engineer the obfuscation. Kasada specifically rotates the obfuscation regularly to defeat reverse engineering. Live execution is faster and more stable.

### Shape Security (F5 Distributed Cloud)

Bank, airline, and ticketing-grade. Generally Level 4 plus careful behavioral mimicry. Shape correlates session continuity, device fingerprints, and long-term reputation across visits - passing once is not enough; the device identity persists.

What to know:
- Most active around authentication and checkout flows. Anonymous public-content scraping faces a lighter version.
- Persistent device IDs follow you across sessions. Fresh sessions look suspicious because no real user starts with no history.
- Counter: warm sessions over multiple visits before doing anything sensitive, and avoid auth scraping entirely (see `legal-ethics.md`).

### Forter / ThreatMetrix

Fraud-focused vendors used at the auth/payment layer. They build cross-device graphs - your fingerprint is correlated with other devices that have appeared in similar contexts. Suspicious clusters get stepped up to multi-factor or hard blocks.

These are largely a non-issue for public-content scraping. They become relevant if you're scraping anything that touches account creation or payment, in which case the right answer is "don't" - see `legal-ethics.md`.

### Regional vendors

Different markets are dominated by different vendors. When scraping non-US sites, check for these:

- **YandexCaptcha / Yandex SmartCaptcha** - used widely on Russian sites (.ru), often paired with Yandex's own anti-bot infrastructure.
- **Qrator** - Russian DDoS/anti-bot. ASN/DNS-policy-driven. Sensitive to bursts.
- **GeeTest** - dominant in China (.cn). Slider, click, and inference-based puzzles. Often paired with Alibaba/Tencent infrastructure.
- **FriendlyCaptcha** - privacy-focused, lightweight. Cryptographic puzzle solved client-side. Common on EU sites that want to be GDPR-friendly.

Strategies are the same as the major vendors - real browser, residential proxies in the right country, normal pacing - but the country code on your proxy must match. A Chinese site with GeeTest will treat US residential IPs with suspicion regardless of how clean they are.

### "Naive" rate limiting

Level 1–2. Datacenter proxies + sessions + reasonable concurrency. The cheapest case.

---

## Diagnostic-driven escalation in practice

The full loop, including failure handling:

```
1. Diagnostic → Level X (initial bet)
2. Deploy at Level X.
3. Run on a sample (50–100 URLs) and observe:
   - Success rate ≥ 95% → ship at Level X.
   - Success rate 50–95% → fix the most common failure first (often a selector or session issue, not anti-bot).
   - Success rate < 50% → escalate to Level X+1 and re-run sample.
4. Repeat until success rate ≥ 95%.
5. Document the level chosen and why, in the project's docs.
```

The "fix the failure first" step matters. Anti-bot is the assumption everyone reaches for, but the actual cause of failure is often a brittle selector, a missed CAPTCHA in a redirect, an empty response treated as success, or a timeout that should have been a retry. Triage the failure before escalating the tooling.

---

## What never works (in 2026)

A short list of advice you'll see in older blog posts that should be ignored:

- **Random User-Agent rotation as a primary defense.** Anti-bot systems compare the UA against TLS fingerprint, sec-ch-ua, viewport, and locale. Mismatched sets are detected immediately. Use Crawlee's fingerprint generator, which produces internally consistent sets.
- **`puppeteer-extra-plugin-stealth` as a serious anti-detection layer.** Not meaningfully updated since 2023. The patches were written for older detection vectors. Modern WAFs see through them.
- **Free public proxies.** Almost all are flagged by every major anti-bot vendor. They were never going to work; using them just wastes time.
- **Setting `navigator.webdriver = false` manually in 2026.** Modern detection cross-references this with dozens of other signals; spoofing one signal while leaving others inconsistent is itself a flag. Crawlee's defaults handle this consistently; let them.
- **Solving CAPTCHAs as the answer to a 403.** A CAPTCHA means you've already failed earlier checks. Solve the upstream problem (proxy, fingerprint, session) so CAPTCHAs don't appear. CAPTCHA solvers are a last resort.

## Defender's red flags - patterns the scraper must NOT generate

Modern anti-bot systems are tuned by what defenders see in their dashboards. Knowing what spikes their alerts is knowing what your scraper must avoid producing. The signals that get scrapers blocked en masse:

- **Sudden spike in failed login attempts** from one IP or fingerprint. Scrapers that touch login pages - even just to read public profile info that requires auth - produce this signal. Avoid login flows entirely unless using legitimate credentials sparingly.
- **High new account creation rate**. We don't create accounts, but if a scraper inadvertently triggers signup forms, this fires. Make sure the scraper visits *only* the public pages it needs.
- **Unusual geographic distribution**. Hundreds of requests in 5 minutes from IPs that span 20 countries are obviously coordinated. Stay within a few country codes per session pool, ideally one matching the target's primary user base.
- **Abnormally high request rate from a single IP**. Even with good IPs, exceeding the per-IP request budget triggers IP-level rate limiting at the vendor (not just the target). Stay below ~30 requests/minute per IP for sensitive targets, much lower for very sensitive ones.
- **Uniform navigation patterns at scale**. If 200 jobs all do "homepage → search → click first result → extract", the aggregate looks robotic even with different IPs. Vary entry points and timing across the job pool.
- **Fresh sessions with no history every time**. Real users carry cookies, localStorage, cached responses across requests. A fleet of scrapers each starting from a clean state looks like a bot army. Use Crawlee's `persistCookiesPerSession: true` and reuse the session pool entries.
- **Direct hits to data-heavy endpoints** without an entry path. A request to `/products/sku/12345.json` without ever having loaded `/products/` first is suspicious. Real users navigate; scrapers must too.
- **Identical request timing across the pool**. If every request is exactly 2.0 s after the previous, that's a fingerprint of `setInterval`-style automation. Use gaussian or jittered delays.

The framing: think like the defender's dashboard. What spike would make a security analyst hit the "block" button? Don't generate that pattern.
