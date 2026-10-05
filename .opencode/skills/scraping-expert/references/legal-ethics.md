# Legal and Ethical Boundaries

Scraping sits in a legal grey area in most jurisdictions. The technical question - "can we get this data?" - is usually solvable. The right question is "should we?". This page covers the legal frameworks that touch scraping, the ethical guardrails any scraping project should operate within, and the lines that don't get crossed.

This is not legal advice. For specific high-stakes scraping projects (especially involving user-generated content, behind-login data, or commercial redistribution), consult a lawyer.

---

## The legal frameworks

Four bodies of law most often touch scraping. Each carries different risk in different jurisdictions.

### Terms of Service (contract law)

When you use a website, you may be bound by its Terms of Service. Most ToS forbid automated access. Violating them is a breach of contract - civil liability, not criminal.

Practical implications:
- The risk of breach-of-contract action against an individual scraper is extremely low for non-commercial use.
- For commercial scraping (a paid scraping service, a sold dataset), the risk is non-zero.
- Aggressive sites (LinkedIn famously) have sued scrapers and won. The case law is mixed but trending toward "ToS matter".

Mitigations:
- Don't accept ToS via login if you're scraping while ignoring the ToS - you create a stronger contract claim against yourself.
- Read the ToS for high-stakes targets. Note the explicit prohibitions.
- Don't bypass paywalls or subscription gates. That's not "ToS grey area"; it's clear violation.

### Computer Fraud and Abuse Act (CFAA, US)

The US federal law against "unauthorized access" to computer systems. Whether scraping public data violates the CFAA has been litigated extensively (hiQ v. LinkedIn series). The current state, as of 2026:

- Scraping data that is **publicly accessible** (no login required, no technical access control bypass) is generally **not** a CFAA violation per the Supreme Court's narrow reading in Van Buren v. United States (2021) and the hiQ Labs ruling.
- Scraping data **behind authentication** that you're not authorized to access **is** likely a CFAA violation.
- Bypassing technical access controls (CAPTCHA solving against the site's intent, IP rotation specifically to evade IP bans the site has explicitly placed on you) lives in a grey area.

Practical implications:
- Public data scraping is largely safe under CFAA.
- Don't scrape data behind login that isn't the user's own account.
- Don't scrape after receiving an explicit cease-and-desist.

### Copyright

Scraped data may be copyrighted. The data itself (facts, prices, addresses) usually isn't copyrightable. The expression of the data (the article text, the photo, the product description) typically is.

Practical implications:
- Scraping facts (prices, ratings, structured product data) for transformation/aggregation is generally fine.
- Scraping articles, photos, or substantial creative content for republication is a copyright violation.
- Fair use (US) and similar doctrines (EU's "exceptions for text and data mining") allow scraping for research, analysis, and AI training under specific conditions, but the conditions matter.

Most legitimate scraping is "scraping facts" (ratings, listings, prices, search results). Stay in that territory.

### GDPR and data protection (EU)

When scraping involves personal data (names, emails, photos of identifiable people, behavioral data tied to identifiable users), GDPR applies. Even if the data is publicly available.

GDPR requires:
- A legal basis for processing (legitimate interest is the most common for scraping, but it's narrowly construed).
- Data minimization (don't collect more than you need).
- Storage limitation (don't keep it longer than necessary).
- Right to erasure (data subjects can ask you to delete).
- Notice (data subjects should know you're processing their data).

Practical implications:
- Scraping product data, reviews without names, ratings, prices: low GDPR concern.
- Scraping LinkedIn profiles, social media users, individual contact information: high GDPR concern. Don't, unless you have a clear legal basis and a compliance program.
- General rule: avoid scraping that returns identifiable personal data unless the explicit purpose justifies it.

---

## robots.txt

`robots.txt` is a voluntary protocol. Search engines respect it; the law doesn't require you to. But:

1. Respecting robots.txt is the **default** for ethical scraping.
2. Some jurisdictions have begun citing robots.txt non-compliance as evidence in scraping cases.
3. In a court case, "we ignored their robots.txt" looks worse than "we respected it where reasonable."

Implementation in Python:

```python
from urllib.robotparser import RobotFileParser

rp = RobotFileParser()
rp.set_url("https://target.com/robots.txt")
rp.read()
if rp.can_fetch("*", url):
    scrape(url)
else:
    log.warning(f"Skipped per robots.txt: {url}")
```

In Node.js, `robots-parser` provides similar functionality. Crawlee 3.x supports `respectRobotsTxtFile: true` on most crawlers, which automatically fetches and respects robots.txt.

When to override:
- Internal APIs that aren't covered by robots.txt anyway.
- When the robots.txt explicitly excludes scrapers but the data is unambiguously public and your purpose is clearly legitimate (research, journalism). Document the reasoning.

When to absolutely respect:
- The robots.txt explicitly disallows your User-Agent.
- The target has noted explicitly (in robots.txt, ToS, or a site notice) that automated access is prohibited.

---

## What this skill won't build (any project)

Drawing the line clearly. Regardless of platform - Apify Actor, standalone scraper, web-app backend - the scrapers built under this skill do not:

- **Scrape behind authentication that isn't the user's own.** The user can build a scraper that uses their own LinkedIn cookie to fetch their own connections; the user does not build a scraper that fetches arbitrary LinkedIn profiles using stolen credentials.
- **Republish copyrighted content.** Wine review text from professional critics is not republished; only quantitative ratings, prices, and metadata.
- **Sell PII.** Personally identifiable information (emails, phone numbers, home addresses tied to individuals) is not collected or sold.
- **Bypass paywalls.** A site's paywall is the site's monetization. We don't undermine it.
- **Hit rate-limited APIs maliciously.** Even when an API has no formal rate limit, we apply reasonable throttling and back off on errors.
- **Use scraped data to harm individuals.** Stalking aids, doxing tools, harassment platforms. None of these are built.
- **Misrepresent ourselves to gain access.** No fake accounts to bypass anti-bot, no impersonating other organizations, no spoofed referrers from sites we don't control.

These are baseline principles for legitimate scraping, codified.

---

## Defensible scraping practices

A "defensible" scraper is one whose behavior, if examined by a court or regulator, looks reasonable. Properties:

1. **Respects robots.txt by default.** Override only with documented reasoning.
2. **Uses a User-Agent that identifies the scraper as a bot.** E.g. Apify Actors typically use a bot-identifying UA when not specifically required to mimic a browser. For fingerprinting reasons, consumer-facing scrapers often have to mimic; in that case, ensure the rest of the operation is legitimate.
3. **Throttles aggressively.** Default conservative concurrency, gaussian delays, no aggressive bursts. The site barely notices the scraper.
4. **Honors `Retry-After`.** When asked to back off, back off.
5. **Stops on cease-and-desist.** If the user receives a C&D from a target site, stop scraping that target. Period.
6. **Doesn't cache or republish more than necessary.** Cache for performance; don't build a parallel database of the target site.
7. **Respects copyright.** Facts only, not creative expression. When in doubt, link instead of copy.
8. **Logs and audits.** Your platform's run history is your audit trail (Apify run history, your own logs, etc.). Don't delete it.

Most of these are also good engineering. Defensible scraping is also reliable scraping.

---

## Specific high-risk patterns to avoid

### Bulk PII collection

A scraper that returns 10,000 individuals' email addresses or phone numbers is high-risk regardless of how the data was acquired. GDPR penalties are severe (up to 4% of annual global revenue). US state privacy laws (CCPA, CPRA) add to this. Don't.

### Logging into accounts you don't own

Even with "the user's permission" obtained via input, automating access to a third party's account is a CFAA risk and a ToS violation. The user can scrape their own accounts; they don't authorize you to scrape someone else's.

### Bypassing CAPTCHAs at scale

A CAPTCHA is the site's explicit signal that they don't want automated access. Solving CAPTCHAs occasionally for legitimate diagnostic purposes is one thing; building it into a production scraper as a routine is another. The legal difference matters.

### Data that triggers obvious anti-bot escalation

When a target deploys a CAPTCHA, redirects to a challenge page, or returns fake content specifically to fool scrapers, they are signaling: do not scrape this. Persisting through those signals is increasingly being read in court as evidence of "intent to circumvent" - closer to CFAA territory.

### Reselling raw scraped content

Scraping news articles and reselling them as a "news API" is a copyright issue and a ToS issue and probably a few other issues. If the business model is "we scrape X and resell X," check with a lawyer first.

---

## When in doubt, ask

Three questions to ask before any new scraping project:

1. **Would the target site, if asked, say "yes that's fine"?** If yes, you're probably fine. If no, the project is at minimum ethically risky.
2. **Is there an official API for this?** If yes, use it. The legal and operational risk drops to near zero when there's an explicit channel.
3. **Would I be comfortable being identified as the operator of this scraper?** If you'd want to hide it, that's a signal.

These aren't tests of legality - they're tests of legitimacy. Legitimate scraping survives scrutiny.

---

## What this skill enforces

Concretely, when working on a scraping project, this skill applies these rules:

- **Default to respecting robots.txt.** Crawlee's `respectRobotsTxtFile: true` is on unless explicitly disabled with reasoning.
- **Refuse to build PII-collection scrapers.** Aggregating personal contact info is out of scope.
- **Refuse to build paywall-bypass scrapers.** If the target paywalls content, the scraper doesn't get past it.
- **Refuse to build login-impersonation scrapers** (using credentials that aren't the user's own).
- **Refuse to scrape after a cease-and-desist** has been received from a target.
- **Decline to write CAPTCHA-bypass logic for sites that have actively challenged the scraper.** A CAPTCHA is the site's explicit "no."

When a request lands in this territory, the response is to explain the boundary and propose an alternative. There is almost always a legitimate variant of the goal that doesn't carry the risk.

---

## The Identity Verification (IDV) wall - a hard limit

A new category of anti-bot is emerging in 2026: when behavioral analysis and fingerprinting fail to give the defender confidence, some sites escalate to **identity verification** - a requirement to submit a government-issued ID and a selfie. Vendors like Vouched, Onfido, Persona, Veriff, and Jumio supply this. It's increasingly deployed on banking, healthcare, gambling, crypto, and high-trust e-commerce.

**This is a hard limit. Don't engineer around it.** Bypassing IDV requires either:

1. Submitting fake or stolen ID documents - fraud, full stop.
2. Using a real person's ID without their authorization - identity theft.
3. Synthesizing a deepfake selfie + altered ID document - also fraud, and pattern-detected by the IDV vendor.

All three are actual crimes in most jurisdictions, not grey-area ToS violations. When a target deploys IDV in front of the data you want, the answer is "this site does not allow scraping by anyone whose identity isn't human and verified, and that's a legitimate decision." Move on.

This is a meaningful change from the historical scraping landscape. Anti-bot used to mean "block bots while letting humans through automatically." Increasingly, it means "verify human identity explicitly." The set of targets where automated scraping is genuinely possible is shrinking. Plan accordingly.

---

## AI agents in 2026 - a new category, new scrutiny

The 2026 anti-bot landscape is reshaping around AI agents specifically - autonomous software that perceives, plans, and acts (the kind of system you are essentially building when you ship MCP servers and multi-agent scrapers). Three implications:

### Anti-bot is being designed against AI agents specifically

Vendors now market "anti-bot for AI agents" as a distinct category. The detection focus is shifting toward signals that AI agents produce despite humanlike fingerprinting:
- Logical-but-overly-direct navigation flows (an AI agent achieves the goal efficiently)
- Inhuman session durations (an AI agent finishes in 30 seconds what a human would do in 5 minutes)
- Inability to handle unexpected interruptions (a popup, a redirect, a layout change)
- Pattern-stable behavior across many sessions (AI agents trained on similar prompts produce similar action sequences)

Modern scrapers - especially LLM-driven ones - produce these patterns naturally. The countermeasure is not "make the AI more humanlike" but "use AI for the parts that benefit from intelligence (extraction, classification) and use deterministic code for the navigation, with humanlike pacing built in."

### Know Your Agent (KYA)

A new category of anti-bot, often called "Know Your Agent," is emerging. The concept: instead of trying to detect whether a request is from a bot vs. human, sites verify the identity of the agent itself - what model, what operator, what authorization. The MCP-I specifications (an extension to Model Context Protocol for agent identity) are an early implementation.

Practical implication: as MCP becomes mainstream, sites may start requiring AI agents to identify themselves cryptographically and authorize specific use cases. Agents that comply get access; those that don't get treated as malicious bots. This is an *opportunity*, not just a threat - for legitimate use cases, KYA may make access easier than today's anti-bot arms race. MCP servers may eventually want to support this directly.

### Compliance-driven enforcement

Anti-bot deployment is increasingly *required* by regulation, not just chosen by site owners. Specifically:

- **GDPR** (EU) - Article 32 requires "appropriate technical and organisational measures" against unauthorised processing. Aggressive scraping of EU residents' personal data is increasingly read as such unauthorised processing. Penalties can reach 4% of global revenue.
- **PCI DSS** (payment) - anti-bot is a recognized control for credit card data environments. Sites that handle payments are under audit pressure to deploy it.
- **HIPAA** (US healthcare) - patient portals deploy anti-bot to prevent unauthorized access to PHI. The penalties for breach of this kind are severe.
- **CCPA/CPRA** (California) - gives consumers rights over scraped data; bypassing the controls undermines the site's compliance.
- **National AI Acts (EU AI Act etc.)** - increasingly apply to AI agents specifically, including scraping by them.

The practical takeaway: scraping in regulated sectors carries multiplied risk. A scraper that pulls public e-commerce prices and one that pulls patient-portal records are not in the same risk class - even if the technical bypass is identical. Stay out of the regulated sectors unless the use case is unambiguously legitimate (e.g., a healthcare provider scraping their own portal).

---

## Specific patterns to refuse

In addition to the patterns already covered, the skill explicitly declines to implement:

- **Token-replay-via-human-CAPTCHA-solve**: A pattern where a human solves the CAPTCHA in a WebView/popup and the resulting token is captured for the bot. This crosses from "stealthy automation" into "active deception of the verification system" and is uncomfortable territory ethically. Use legitimate CAPTCHA-solving services if absolutely necessary, not user-tricking patterns.
- **Encrypted form-field replay against rotating-keys defenses**: When a site goes to the trouble of encrypting form input names per page load and obfuscating the JavaScript that injects them, that's a defender saying "no automation, period." Reverse-engineering this isn't impossible, but it's an arms race that signals the target genuinely doesn't want you there. Move on or use a managed API.
- **Bypass of cease-and-desist notifications**: When a C&D has been received from a target, we don't keep scraping that target. We don't switch to a managed API to launder the requests. We stop.

These are extensions of the existing principles, not new categories. They reflect what the *current* anti-bot landscape (as of 2026) makes salient.

---

## Resources

For deeper reading:

- The hiQ Labs v. LinkedIn case history (US scraping case law).
- GDPR text, especially Articles 6 (lawful basis) and 14 (notice).
- Each major target site's ToS - read once before building a serious project against them.

When a project pushes against any of these boundaries, the right move is a short conversation about what you're trying to accomplish - usually there's a legitimate variant of the project that doesn't carry the risk.
