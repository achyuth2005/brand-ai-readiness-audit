# AI Crawler Agents: Who They Are and What Blocking Them Does

## The insight this skill is built on

AI crawlers are **not one population**, and they do different jobs. Blocking a training
crawler keeps a site's content out of future model training; it does not stop the site
being retrieved and cited in a live answer. Blocking a **retrieval** crawler removes the
site from the set of sources an assistant can fetch and quote at answer time — the brand
disappears from answers no matter how good its content is.

A site that "blocked AI bots" on legal advice frequently blocked both kinds without
realising the second consequence. That is invisible in a browser and in analytics, which
is why it survives for years, and why this is the highest-value check in the package.

## The agent table

The authoritative data is `AI_CRAWLERS` in `scripts/analyze_access.py`. Each entry names
the robots.txt product token, the operator, its job, and its impact class.

| Token | Operator | Job | Impact of blocking |
| :--- | :--- | :--- | :--- |
| `OAI-SearchBot` | OpenAI | Builds the search index ChatGPT retrieves citable sources from | **severe** — removes citation eligibility |
| `ChatGPT-User` | OpenAI | Live fetch when a user's conversation needs a page | **severe** |
| `GPTBot` | OpenAI | Model training corpus | training — not a citation blocker |
| `Claude-SearchBot` | Anthropic | Search index for answer retrieval | **severe** |
| `Claude-User` | Anthropic | Live user-initiated fetch | **severe** |
| `ClaudeBot` | Anthropic | Model training corpus | training |
| `PerplexityBot` | Perplexity | Search and answer index | **severe** |
| `Perplexity-User` | Perplexity | Live user-initiated fetch | **severe** |
| `Googlebot` | Google | Core web index; many assistant surfaces ride on it | **severe** and broad |
| `Google-Extended` | Google | Control for Gemini grounding and training use | moderate |
| `Bingbot` | Microsoft | Core web index used by Copilot and others | **severe** and broad |
| `Applebot` | Apple | Search index for Siri and Spotlight | moderate |
| `Applebot-Extended` | Apple | AI training opt-out control | training |
| `CCBot` | Common Crawl | Open web snapshots widely used as model-training corpora; no assistant retrieves live citations from them | training |
| `Amazonbot` | Amazon | Index for Alexa and shopping surfaces | moderate |
| `meta-externalagent` | Meta | Search and AI corpus | moderate |

**Calibration.** Operators publish, rename and split these tokens over time. The table is
data, kept in one place so it can be updated from each operator's own documentation without
touching detector logic. A token not in the table is not evaluated, and the report does not
claim anything about it.

## How a block is determined

1. The site's robots.txt is reconstructed from the evidence ledger (the audited host's own
   file is preferred over host variants or off-site files).
2. For each agent, the **effective group** is chosen the way RFC 9309 specifies: the group
   whose `User-agent` token is the longest case-insensitive match for the agent, falling
   back to `*`. A blanket `User-agent: *` / `Disallow: /` does not block an agent that has
   its own permissive group.
3. The agent is evaluated against the site's **sampled content paths only** — the homepage
   plus every fetched non-utility page. `Disallow: /cart`, `/search`, `/admin` or `/api` is
   correct engineering and can never produce this finding.
4. Within the group, the longest matching pattern wins; `Allow` wins an exact-length tie;
   `*` and a trailing `$` are honoured. `urllib.robotparser` is not used, because it
   mishandles exactly these cases.

## What is reported

| Situation | Output |
| :--- | :--- |
| A **severe** or **moderate** agent is disallowed on content paths | `G0.ROBOTS.AI_AGENT_BLOCKED` finding, citing the exact robots.txt line and group, the blocked share of content paths, and — when one exists — a named agent that *is* permitted on the same paths, which shows the treatment is differential rather than a site-wide policy |
| A **training** agent is disallowed | `G0.ROBOTS.TRAINING_AGENT_BLOCKED` observation that states plainly this does not affect citation, and that the rule is correct if the intent was to stay out of training data while remaining citable |
| All retrieval agents permitted, but only through `*` | a near miss feeding a proactive recommendation to name them explicitly, so an unrelated future blanket rule cannot silently remove citation eligibility |

Scope is site-wide when at least 60% of sampled content paths are blocked, otherwise
section. Confidence is high: the observation is the site's own policy file, read with a
standards-correct matcher.

## Respecting owner intent

A block can be deliberate. When robots.txt carries comments about licensing, copyright,
scraping, training data or opt-outs — or when four or more AI agents are explicitly blocked
at the root while search engines are allowed — the finding is **kept** but the suggested
action is reframed:

> Confirm that blocking OAI-SearchBot is still the intended policy, and note that it
> removes citation eligibility rather than only training use.

The steps separate the two decisions — training-corpus use and live citation — so the owner
can keep one policy and change the other. The audit reports the consequence; the decision
stays with the owner.

## The correct fix, when the block is unintended

```
User-agent: OAI-SearchBot
Allow: /
Disallow: /cart
Disallow: /checkout
Disallow: /search
Disallow: /admin

User-agent: GPTBot
Disallow: /
```

Retrieval crawlers allowed on public content, utility paths still excluded, training policy
left exactly as the business chose.

**Verify:** test the agent against a public content path with a robots.txt tester, and fetch
a public URL with a user-agent containing the token to confirm a 200 with the page body.
Where the operator publishes IP ranges, allowlist by those at the CDN rather than by
user-agent alone, which is spoofable.
