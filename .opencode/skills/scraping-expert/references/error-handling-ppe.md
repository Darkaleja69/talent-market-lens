# Error Handling and PPE Billing (Apify deployment doctrine)

> **Scope.** This file is **Apify-specific deployment doctrine**. The platform-agnostic part - the **failure taxonomy** (`TARGET_BLOCKED` vs `EXTRACTION_FAILED` vs rate-limit vs `TARGET_NOT_FOUND`, and "a 200 with an empty/fake body is a silent block") - applies to any scraper and is summarized in `SKILL.md` Phase 4. The Apify *implementation* below (exit SUCCEEDED, `Actor.charge`, `setStatusMessage`, free-plan handling) is what's deployment-specific.

The single highest-impact pattern for production Apify Actors is the **graceful exit** pattern. It is the difference between a 50% success rate (visible to users, hurting Store ranking) and a 95%+ success rate (Store-friendly, retains users).

This page documents the **engineering implementation** of the graceful-exit + PPE-billing pattern from the scraping-engineering perspective: error categorization, exit-status table, charge helper, error decision tree.

This file documents the scraping-engineering-specific layer: how the graceful-exit pattern integrates with Crawlee, how error categorization drives status messages, the per-Actor charge helper. The canonical, end-to-end PPE doctrine (full `Actor.charge()` patterns, event taxonomy design, `pay_per_event.json` schema, the PPE+usage toggle rules, and common antipatterns) lives in the Apify monetization documentation; on any contradiction, defer to the authoritative monetization doctrine.

---

## The core rule

> **Always exit SUCCEEDED unless the infrastructure itself failed.**

Infrastructure failures are: out-of-memory, Docker container crash, platform-side outages. Business-logic errors are: invalid input, third-party API errors, scraping blocked by target, no results found, malformed input file. **Business-logic errors do not produce FAILED runs.** They produce SUCCEEDED runs with a structured error record in the dataset.

The user opens the Apify Console. They see a green SUCCEEDED status. They open the dataset. They see a clear error message. They understand what happened. They never need to read the raw logs.

This single rule routinely lifts an Actor from ~50% success rate to >95%, with zero changes to scraping logic. The Actor is doing the same work - it's just communicating more clearly.

---

## The graceful exit pattern

The implementation:

```ts
import { Actor } from 'apify';

await Actor.init();

try {
  const input = await Actor.getInput<MyInput>();
  
  if (!isValidInput(input)) {
    await Actor.pushData({
      error: true,
      errorType: 'INVALID_INPUT',
      message: 'Required field "query" is missing or empty.',
      suggestion: 'Provide a "query" string of at least 1 character.',
      input: redactPII(input),
      timestamp: new Date().toISOString(),
    });
    await Actor.setStatusMessage(
      'Error: invalid input. See the dataset for details.',
      { isStatusMessageTerminal: true },
    );
    await Actor.exit();  // SUCCEEDED, with error record
    return;
  }
  
  const result = await scrapeIt(input);
  await Actor.pushData(result);
  await Actor.charge({ eventName: 'item-scraped', count: 1 });
  
  await Actor.setStatusMessage(
    `Done. Scraped 1 item successfully.`,
    { isStatusMessageTerminal: true },
  );
  await Actor.exit();
  
} catch (error) {
  // unhandled exception → categorize and push as error record
  await Actor.pushData({
    error: true,
    errorType: error.constructor.name,
    message: error.message,
    stack: process.env.APIFY_LOG_LEVEL === 'DEBUG' ? error.stack : undefined,
    timestamp: new Date().toISOString(),
  });
  await Actor.setStatusMessage(
    `Error: ${error.message}. See the dataset for details.`,
    { isStatusMessageTerminal: true },
  );
  await Actor.exit();  // STILL SUCCEEDED
}
```

Things to notice:

- `Actor.fail()` is never called for business errors. The catch block exits via `Actor.exit()`.
- The error record has a fixed shape: `error`, `errorType`, `message`, optional `suggestion`, optional `details`, `timestamp`.
- The status message is set to terminal - it freezes at the end of the run and shows in the Console.
- PPE charge happens *only* on the success path, before exit, after `pushData`.

`Actor.fail()` is reserved exclusively for cases where the platform itself has lost confidence in the run - out-of-memory, Docker crash, fatal initialization errors. In practice, you almost never need to call it; the platform will mark the run as FAILED on its own when those conditions are hit.

---

## Error taxonomy

Categorize every error you handle. The taxonomy below covers the common cases.

### `INVALID_INPUT`

Cause: user-provided input doesn't match the schema (missing required field, out-of-range value, wrong type).
Handling: validate at the start of `main`. Push error record, set status, exit SUCCEEDED. **No charge.**
Recovery for the user: edit input, run again.

### `TARGET_BLOCKED`

Cause: the target site returned 403/429 after exhausting retries and session rotations.
Handling: push error record explaining the block. Suggest: retry later, change country, switch to residential proxies (if available in input). Exit SUCCEEDED. **No charge.**
Recovery for the user: change configuration, retry.

### `TARGET_NOT_FOUND`

Cause: the requested resource (URL, ID, search term) returned 404 or "no results".
Handling: distinguish from blocking - 404 with a clean response is informational, not an error in itself. Push the result with `notFound: true`. Whether to charge depends on the contract - usually yes (the user asked for a lookup, you performed it; the answer is "doesn't exist").
Recovery for the user: usually nothing; this is the answer.

### `API_RATE_LIMITED`

Cause: a third-party API (Claude, OpenAI, Apify Client, target API) returned 429 with `Retry-After`.
Handling: respect `Retry-After` and retry up to N times. After exhausting retries, push error with the retry guidance. Exit SUCCEEDED. **No charge.**
Recovery: wait and retry.

### `API_AUTH_FAILED`

Cause: the third-party API returned 401/403 because the API key is invalid, missing, or insufficient.
Handling: push error with explicit message about the API key. Don't expose the key itself. Exit SUCCEEDED. **No charge.**
Recovery: user updates the key in input.

### `API_QUOTA_EXCEEDED`

Cause: third-party API returned a billing/quota error.
Handling: explicit message. Suggest: upgrade plan, wait for reset. **No charge.**
Recovery: user-side.

### `INPUT_FILE_INVALID`

Cause: a PDF/image/file in the input is corrupted, password-protected, unreadable.
Handling: explicit message about the file issue. Exit SUCCEEDED. **No charge.**
Recovery: user re-uploads or fixes the file.

### `EXTRACTION_FAILED`

Cause: the page loaded but the expected data wasn't found (selector returned nothing, JSON shape unexpected, schema validation failed).
Handling: this is the canary for site changes. Push error with the suspect selector/path. **No charge** (we didn't deliver the result the user expected). Often warrants an alert; site has likely changed structure.
Recovery: maintenance update on the Actor side.

### `INTERNAL_ERROR`

Cause: an unhandled exception in our code that doesn't fit the categories above.
Handling: catch in the outer try/catch, push generic error record with the exception message and class name. **No charge.**
Recovery: bug report.

---

## The decision table

A table is the clearest way to communicate when to charge and what status to use:

| Situation | Push to dataset | Charge PPE | Exit status |
|---|---|---|---|
| Full success | Result | Yes | SUCCEEDED |
| Partial success (some pages OK, some failed) | Partial result + error notes | Yes (per successful unit) | SUCCEEDED |
| No results found (404 / empty search) | `{ notFound: true }` | Yes (per contract) | SUCCEEDED |
| Invalid input | Error record (`INVALID_INPUT`) | No | SUCCEEDED |
| Target blocked after retries | Error record (`TARGET_BLOCKED`) | No | SUCCEEDED |
| Third-party API rate limited | Error record (`API_RATE_LIMITED`) | No | SUCCEEDED |
| Third-party API auth failed | Error record (`API_AUTH_FAILED`) | No | SUCCEEDED |
| Third-party API down | Error record | No | SUCCEEDED |
| Input file corrupted | Error record (`INPUT_FILE_INVALID`) | No | SUCCEEDED |
| Selector failure (site changed) | Error record (`EXTRACTION_FAILED`) | No | SUCCEEDED |
| Multi-agent: one agent fails, others succeed | Per-agent results (some null) | Yes if overall threshold met | SUCCEEDED |
| Multi-agent: all agents fail | Error record | No | SUCCEEDED |
| Out-of-memory | (handled by platform) | (no opportunity) | FAILED |
| Docker crash | (handled by platform) | (no opportunity) | FAILED |
| Unhandled exception caught in outer try | Error record (`INTERNAL_ERROR`) | No | SUCCEEDED |

Make this table for each Actor in its project docs. The table is the contract.

---

## PPE charging rules

Six rules. They are absolute.

### 1. Charge only after confirmed full success

`Actor.charge()` is the last operation before `Actor.exit()` on the success path. It fires after `Actor.pushData()` has returned, after the data is committed.

```ts
const result = await processOne(input);
await Actor.pushData(result);
await Actor.charge({ eventName: 'item-processed', count: 1 });
```

Never charge inside try blocks where exceptions might prevent the data from being delivered. Charge after delivery.

**Atomic shortcut (preferred where it fits):** when the event corresponds to a dataset item, prefer `Actor.pushData(data, 'event-name')` - it combines persistence and charging atomically and avoids the (tiny) crash-between-pushData-and-charge gap.

```ts
const chargeResult = await Actor.pushData(result, 'item-processed');
```

The atomic shortcut is the preferred pattern. Both patterns are valid; the two-call form remains useful for illustrating the rule and for cases where the charge corresponds to non-dataset value (e.g. a successful side-effect like a webhook delivery).

**Double-charging gotcha:** if your `pay_per_event.json` declares `apify-default-dataset-item` AND a custom event, calling `Actor.pushData(data, 'custom-event')` charges **both** events - the synthetic `apify-default-dataset-item` (auto-fires on every default-dataset write) AND your custom event. This is almost never what you want. Either declare the synthetic OR the custom event, not both - unless the dual charge is intentional (rare).

### 2. Never charge on retry

If you retry an operation due to a transient failure, you charge once for the final successful outcome. Retries are an implementation detail; the user pays for the result, not the attempts.

```ts
// Anti-pattern: charging in a retry loop
for (let i = 0; i < 3; i++) {
  try {
    await fetchAndCharge();  // charges once per attempt - WRONG
    break;
  } catch (e) { /* retry */ }
}

// Pattern: charge once after success
const result = await retryUntilSuccess(() => fetchData());
await Actor.pushData(result);
await Actor.charge({ eventName: 'item-fetched', count: 1 });
```

### 3. Never charge on partial result

If the contract is "deliver a complete multi-stage analysis," don't charge for "deliver 4 of 6 stages." Either deliver the full result and charge, or deliver a partial result and don't charge.

For multi-agent / multi-stage flows, define the success threshold up front. Document it in the Actor's project docs:

> "Charge the `analysis-complete` event only when ≥ 5 of 6 stages return valid output. Below that, push partial result without charging."

### 4. Never charge on error

Any error record pushed to the dataset must not be accompanied by a charge. The user gets visibility into what went wrong, free of charge.

### 5. Respect `eventChargeLimitReached`

Free-plan users have monthly event limits. When the limit is reached, `Actor.charge()` returns a result indicating it.

```ts
const result = await Actor.charge({ eventName: 'item-processed', count: 1 });

if (result.eventChargeLimitReached) {
  await Actor.setStatusMessage(
    'Free plan event limit reached. The result is in the dataset; future calls require a paid plan.',
  );
}
```

The Actor still completes successfully; the dataset is delivered; the user just doesn't get charged. This is intentional: free-plan users shouldn't be blocked from getting their result, they should just be aware of the limit.

### 6. PPE + usage toggle - default OFF, two narrow exceptions

The "Pay per event + usage" toggle exists in the Apify Console (Monetization → Pricing). A sensible default is **PPE only**, with the toggle **OFF** in production. Two narrow exceptions:

1. **First 30 days post-launch**, while you calibrate event prices, the toggle can stay ON. Turn it off as soon as event prices are stable (the toggle-off is immediate; toggle-on takes 14 days). This window damages the Actor Quality Score, so keep it short.
2. **MCP servers in Standby mode** are the legitimate long-term case. Standby idle compute is otherwise uncompensated; Pay-per-Usage pass-through covers it.

For everything else (commodity scrapers, AI agents, one-shot Actors), the toggle stays OFF. Toggling it ON in production reduces pricing transparency and confuses cost prediction.

This file documents the **engineering-side** of the rule; the canonical, end-to-end pricing doctrine lives in the Apify monetization documentation.

The synthetic event `apify-actor-start` fires automatically on every run; leave it at the default price (which is what makes the platform work). Don't customize it.

---

## Implementation: the charge helper

To enforce these rules consistently, wrap `Actor.charge()` in a helper:

```ts
// src/ppe/charge.ts

import { Actor, log } from 'apify';

interface ChargeOptions {
  eventName: string;
  count?: number;
}

/**
 * Charges a PPE event. NEVER call Actor.charge() directly - use this wrapper.
 * It enforces the rules:
 * - Verifies count > 0
 * - Logs the charge attempt (visible in Apify Console logs)
 * - Handles eventChargeLimitReached gracefully
 * - Returns whether the charge actually went through
 */
export async function chargeEvent(opts: ChargeOptions): Promise<boolean> {
  const count = opts.count ?? 1;
  if (count <= 0) {
    log.warning(`Refused to charge event ${opts.eventName} with count ${count}`);
    return false;
  }
  
  const result = await Actor.charge({ eventName: opts.eventName, count });
  
  if (result.eventChargeLimitReached) {
    log.info(`Event ${opts.eventName} not charged: limit reached for this user.`);
    return false;
  }
  
  log.info(`Charged event ${opts.eventName} × ${count}`);
  return true;
}
```

All charging in the Actor goes through this helper. New developers can't bypass the rules accidentally.

---

## Multi-agent and parallel patterns

For Actors that run multiple agents in parallel (e.g. a multi-perspective analysis pipeline where several agents each contribute one section of the result), error handling is more nuanced.

### Use `Promise.allSettled`, not `Promise.all`

`Promise.all` rejects on the first failure. In a 6-agent pipeline, that means one agent failing kills the whole run.

`Promise.allSettled` waits for all promises and returns each one's outcome (fulfilled or rejected) without short-circuiting. You decide what to do based on the aggregate.

```ts
const results = await Promise.allSettled([
  runAgent('agent-1', input),
  runAgent('agent-2', input),
  runAgent('agent-3', input),
  runAgent('agent-4', input),
  runAgent('agent-5', input),
  runAgent('agent-6', input),
]);

const successful = results.filter(r => r.status === 'fulfilled').length;
const successThreshold = 5;  // documented contract: charge if ≥ 5/6

if (successful >= successThreshold) {
  // build report from fulfilled results, fill nulls for rejected
  const report = buildReport(results);
  await Actor.pushData(report);
  await chargeEvent({ eventName: 'analysis-complete' });
} else {
  // partial result, no charge
  await Actor.pushData({
    error: true,
    errorType: 'AGENT_PIPELINE_DEGRADED',
    message: `Only ${successful}/6 agents succeeded. Threshold is ${successThreshold}.`,
    partialResults: results.map(r => r.status === 'fulfilled' ? r.value : null),
  });
}
```

### Document the threshold

The success threshold is part of the Actor's contract. It belongs in the Actor's project docs and in its README. Users on PPE have a right to know when they're charged.

---

## Status messages on the error path

Every error record should be accompanied by a clear status message. This is what the user sees in the Console run summary.

Templates:

| Situation | Status message template |
|---|---|
| Invalid input | `"Error: invalid input. {detail}. See dataset for details."` |
| Target blocked | `"Error: target site blocked the request. Try again later or change configuration."` |
| API rate limited | `"Error: AI API rate limited. Try again in a few minutes."` |
| API auth failed | `"Error: AI API key invalid. Update your API key in input and retry."` |
| Input file invalid | `"Error: input file unreadable. {detail}."` |
| Selector failure | `"Warning: site structure may have changed. Result may be incomplete."` |
| Partial multi-agent | `"Partial analysis complete ({N} of {M} stages succeeded). See dataset."` |
| Full success | `"Done. {N} items processed successfully."` |

Always include the actionable next step. "Error happened" is not enough; "Error happened, do X" is the goal.

---

## What to log vs. what to dataset

Logs (via `log.info`, `log.warning`, `log.error`) are for debugging. They're visible in the Console but most users don't read them.

Dataset entries are what the user sees first. They're the API output of the Actor. Errors that the user needs to know about belong here.

A reasonable rule: **anything that affects whether the user got the result they wanted goes in the dataset.** Anything that's purely diagnostic (cache hits, retry attempts, internal state) goes in logs.

---

## Summary checklist

For every Actor, before publishing:

- [ ] All `Actor.fail()` calls removed except for catastrophic infrastructure errors.
- [ ] Outer try/catch wraps the entire flow; catches push error records and exit SUCCEEDED.
- [ ] All errors categorized using a stable taxonomy (and documented in the Actor's project docs).
- [ ] PPE charges only fire on the success path, after `pushData`, never on retries or partial results.
- [ ] `eventChargeLimitReached` handled with a graceful status message.
- [ ] All status messages are actionable (`isStatusMessageTerminal: true` at end).
- [ ] No PII in error records (sanitize input before pushing).
- [ ] Decision table copied into the Actor's project docs.
- [ ] Multi-agent flows use `Promise.allSettled`, not `Promise.all`.
- [ ] Charge helper used everywhere; no direct `Actor.charge()` calls in business logic.
