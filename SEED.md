# TestTeam seed content

Paste this in as-is. The content is engineered so the kill shot produces a clean,
monotone degradation *with false leaks*, which is the part judges react to.

**TestTeam** is a B2B SaaS dashboard that rolls up sprint progress across projects.
Its three advertised features — cross-project rollups, one-click CSV/Excel export,
and Slack/email digests — are what the complaints are about. The public repo is
its marketing landing page.

## The design, and why each piece exists

Twelve complaints across Slack and Gmail. Where each one is tracked is the whole
point:

| group | tracked in | count | role in the demo |
|---|---|---|---|
| true leaks | nowhere | 5 | the answer |
| tracked work | Linear only | 2 | proves it doesn't cry wolf |
| tracked work | **GitHub only** | 2 | **becomes a false leak when GitHub is dropped** |
| noise | n/a | 3 | proves the classifier filters, not just matches |

The GitHub-only group is load-bearing. Without it, narrowing scope only ever
produces *fewer* leaks. With it, narrowing scope makes the system **confidently
wrong** — it reports two complaints as ignored when they were actually fixed, in
a source it wasn't allowed to look at. That's the moment worth 90 seconds.

Expected degradation:

| scope | leaks reported | false | invisible |
|---|---|---|---|
| slack only | 0 (all `unknown`) | — | — |
| slack + linear | 4 | 2 | 3 |
| slack + gmail + linear | 7 | 2 | 0 |
| all four | **5** | **0** | **0** |

---

## 1. Slack — post these in `#all-testteam`

Post as yourself. The channel reads as a support triage channel where the team
relays customer reports, so a single author is realistic. Space them out or post
in one go; ordering doesn't matter.

**First, delete the two "testing" messages** — they classify as noise and clutter
the demo.

> Northbeam are saying the Pricing link in the top nav doesn't go anywhere. Click it and the page just sits there. Minor but it's on the main marketing page.

> Graywater flagged that the Monday digest hasn't arrived for two weeks now. No error, it just silently stopped. They only noticed because someone asked where the update was.

> Ferndale Labs report the cross-project rollup is showing stale sprint data — looks like roughly a day behind. Closing a sprint doesn't seem to refresh it.

> Halcyon: on mobile Safari the hero section on the landing page overlaps the nav bar. Looks broken on an iPhone, fine on desktop.

> Arbor & Co say the customer logo strip is basically unreadable in dark mode. Grey on near-black, very low contrast.

> quick q from Northbeam — how do they change the digest to send Fridays instead of Mondays? couldn't find it in settings

> the new rollup view is getting really good feedback, Ferndale said it saved them the Monday scramble entirely

---

## 2. Gmail — send these to `testteamcustomerservice@gmail.com`

Send from any other account you have; the sender address doesn't need to be
unique per email, because clustering is topical rather than identity-based.

**Subject:** `CSV export dropping rows`
> Hi — we've hit a data issue with the CSV export. Any project whose name contains a comma seems to break the row, and we end up with fewer rows in the file than on screen. We exported 240 projects and got 232 lines. No warning anywhere. This is going into a board report so it matters.

**Subject:** `Excel export loses all formatting`
> When we export to Excel the header row comes out as plain text — no bold, no freeze pane, column widths all collapsed. The CSV is fine. We have to re-format it by hand every week.

**Subject:** `Billing — charged for 12 seats, we have 9`
> Our invoice this month is for 12 seats but we only have 9 people in the workspace. We removed three contractors back at the start of the month. Can someone check and refund the difference?

**Subject:** `Possible data leak between workspaces`
> This is a bit alarming — when I switch workspaces using the top-left picker, I briefly see another organisation's sprint board before it reloads to ours. I can read their project names. I don't think I should be able to see that at all.

*(The Google security alerts and GitHub notifications already in the inbox are
useful — leave them. They should classify as `spam_or_unrelated` and get filtered,
which demonstrates the classifier is doing real work.)*

---

## 3. Linear — create these two issues

In the **Agentsloveyou2hackathon** team. Deliberately worded to match the
complaint *topically* rather than word-for-word, so retrieval has to actually work.

**Title:** `Rollup cache not invalidating on sprint close`
> Sprint rollups continue serving cached aggregates after a sprint is closed, so the dashboard lags real state by up to 24h. Need to bust the cache on the sprint-close event rather than waiting for the scheduled refresh.

**Title:** `Excel export: preserve header formatting and column widths`
> The xlsx writer emits an unstyled sheet. Restore bold headers, a frozen top row, and auto-fit column widths. CSV output is unaffected.

**Create only these two.** Everything else must stay untracked or the leak count
collapses.

---

## 4. GitHub — create these two issues

In `testteam-agentsloveyou2hackathon/testteam`. **These two are what make the kill
shot land** — they're the tracked work that the Linear-only scope cannot see.

**Title:** `Hero section overlaps nav below 640px on iOS Safari`
> The hero block's negative top margin collides with the fixed nav on small viewports. Reproduced on iPhone 13 / Safari 17. Desktop and Android Chrome are unaffected. Likely needs a responsive margin on the hero wrapper in `src/App.jsx`.

**Title:** `Customer logo wall fails contrast in dark mode`
> The logo strip renders mid-grey on the near-black dark background, well below WCAG AA. Needs a lighter treatment or a background panel behind the logos.

---

## 5. Leave this bug unfixed — it's the auto-fix target

`src/App.jsx` declares a nav link to `#pricing`:

```jsx
const NAV_LINKS = [
  { label: 'Product', href: '#product' },
  { label: 'Features', href: '#features' },
  { label: 'Customers', href: '#customers' },
  { label: 'Pricing', href: '#pricing' },
]
```

The rendered page has sections with ids `top`, `product`, `features`, `customers`
and `cta` — **there is no `pricing` section**, so that link does nothing. It is a
real bug, it is genuinely small, and it maps exactly to the first Slack complaint.

This is what the coding agent fixes live: low severity, code-shaped, single file,
well under the auto-fix ceiling in the intent profile. Don't fix it beforehand.

---

## After seeding

```bash
.venv/bin/python scripts/setup_connectors.py --only slack,linear,github
.venv/bin/python scripts/ingest_gmail.py
```

Slack and Linear sync in seconds. Give GitHub a moment after creating the issues.
