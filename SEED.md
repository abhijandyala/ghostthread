# TestTeam seed content

Paste this in as-is. The content is engineered for two things at once: a kill
shot that degrades cleanly *with false leaks*, and a repeat complainant whose
history makes the memory pitch visible.

> **Revised for the memory-first pitch.** The first version had one complaint per
> topic per actor, which is fine for leak detection and useless for memory —
> `times_reported_by_actor` would be 1 for everyone and every reply would come
> out `first_contact`. Since the lede is now "every other agent reads one
> message, we read the history", the seed needs someone with a history. Section 0
> is the addition; everything else is unchanged.

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

## 0. The repeat actor — this is what makes the memory beat work

One customer, **Northbeam**, reports the same defect three times over five weeks.
`reply_tone_thresholds` in the intent profile is `{returning: 1, escalation: 3}`,
so the third contact must land on `escalation` — and it only does if the first
two are in HydraDB Memories before the demo starts.

| # | when | channel | state |
|---|---|---|---|
| 1 | ~5 weeks ago | Gmail | resolved, ticket closed |
| 2 | ~3 weeks ago | Slack | resolved, ticket closed — "fixed" but it regressed |
| 3 | during the demo | Slack | the live complaint |

Contacts 1 and 2 go into **memory**, not into the connectors, because they are
history rather than open work. `scripts/seed_memory.py` writes them through the
same `memory_write` path the pipeline uses — no special-casing, no fabricated
counts, just prior resolutions that genuinely happened.

> **Set the actor before you seed.** `memory_read` filters on the sender address
> exactly, so the seeded actor must equal the `author_email` the live contact 3
> arrives with — otherwise it reads back as zero prior contacts, which is a
> legitimate answer and so fails with no error at all.
>
> The plan is to send contact 3 by email from a second Gmail account, so seed
> with that account's address:
>
> ```bash
> .venv/bin/python scripts/seed_memory.py --force --actor you@second-account.com
> ```
>
> A Slack message resolves to the posting member's workspace address, which is
> not the fixture default of `ops@northbeam.io` — so a Slack-delivered contact 3
> needs `--actor` set to whatever that address is. To see what a given source
> actually resolves to:
>
> ```bash
> PYTHONPATH=src .venv/bin/python -c "
> from ghostthread import knowledge_query as kq
> for d in kq.load_documents('slack', refresh=True)[:3]:
>     print(d.actor_email or '(none)', '|', d.text[:50])"
> ```

Contact 3 is the message you post live. When it lands, GhostThread should report
`times_reported_by_actor: 3`, tone `escalation`, and name the two prior tickets.

**Contact 1 — Gmail, ~5 weeks ago.** Subject: `CSV export truncating rows`
> Our Friday export came out short again — 1,204 rows in the file against 1,231 on the dashboard. No error, the file just ends. We reconcile these against finance so the gap gets noticed.

Resolution: closed as `NB-104`, "export pagination dropped the final partial page".

**Contact 2 — Slack, ~3 weeks ago, `#all-testteam`.**
> Northbeam again on the CSV export — it's short by a handful of rows. This is the same thing we closed a couple of weeks back, it's come back.

Resolution: closed as `NB-118`, "reopened, off-by-one in the page-boundary fix".

**Contact 3 — post this live during the demo.**
> Northbeam, third time on this: the CSV export is still dropping rows. We were told twice this was fixed. 8,400 contacts exported, 212 missing, every one with a non-ASCII character in the name. We need someone senior on this.

That third message is deliberately the same defect described more precisely, so
retrieval matches it to the prior two on topic while the escalating tone is
carried by the counts rather than by the wording.

**Why seed memory rather than post all three as complaints:** contacts 1 and 2
were *resolved*. If they were live complaints in Slack and Gmail they would be
two more rows in the leak table competing for attention, and the demo would have
to explain why they are not leaks. As memories they are what they actually are —
history — and they make the third contact mean something.

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
.venv/bin/python scripts/seed_memory.py          # Northbeam's two prior contacts
.venv/bin/python scripts/eval.py                 # should go green, with fewer skips
```

Slack and Linear sync in seconds. Give GitHub a moment after creating the issues.

Then fill in `fixtures/eval_cases.json` with the real complaint ids and their
expected verdicts. Until that happens the expected-case checks skip rather than
pass, and a skip proves nothing.

**The check that matters before you go on stage:** run the memory read for
Northbeam and confirm `times_reported_by_actor` is 3 and the tone is
`escalation`. If it says `first_contact`, the memory seed did not land and the
central beat of the pitch is dead — with no visible error, because zero prior
contacts is a legitimate answer.
