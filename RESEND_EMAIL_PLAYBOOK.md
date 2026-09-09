# Resend Email Newsletter — Setup Playbook

> **Provenance.** Ported from the `wc-challenge` repo (World Cup pool, 2026 season,
> pipeline now retired) on 2026-09-03. Everything learned building that project's
> daily recap email, written so a Claude Code session can rebuild it here for the
> **college football pick'em** without re-deriving any of it.
>
> The working source it describes is vendored read-only under
> [reference/wc-challenge-email/](reference/wc-challenge-email/) — that is a
> **snapshot to read and rewrite from, not a package to import**. Nothing in this
> repo should depend on it at runtime; once the CFB `email/` folder exists, the
> reference folder can be deleted.

Read this top to bottom before writing code. Sections 1–3 are account/infra setup
(a human has to do parts of it). Section 4 is the file inventory — what to copy
verbatim, what to copy-and-rewrite, what to skip. Section 5 is the gotchas that
cost real time.

---

## 1. What this system actually is

A **static-data newsletter**: a scheduled GitHub Actions job regenerates the pool's
JSON, a Python script resolves every template variable into one flat `payload.json`,
a Jinja2 template renders that payload into email-safe HTML, and the Resend API
sends it as one group email.

```
  cron (GitHub Actions)
        │
        ├─ fetch results ─► score ─► write site/data/*.json     (your existing pipeline)
        │
        ├─ build_email_payload.py ──► email/payload.json         (ALL logic lives here)
        │
        ├─ should_send.py ──────────► exit 0 = send, 1 = skip    (the gate)
        │
        ├─ generate_hero_image.py ──► site/assets/email/*.png    (optional, paid API)
        │
        ├─ git commit + push ───────► Pages deploy (image must be live BEFORE send)
        │
        └─ send_email.py ───────────► render.py ─► template.html ─► Resend API
                                      └─ on success: snapshot delta baseline + stamp date
```

Two principles that made it work and should be preserved:

1. **The template does zero logic.** Every string, colour, arrow, and URL is
   resolved in `build_email_payload.py`. The template is loops and conditionals
   only. This means you can debug an email by reading `payload.json` — no need to
   render anything.
2. **One place decides whether to send.** `should_send.py` is the single gate. The
   workflow calls it once, stores the answer in a step output, and every downstream
   step (hero image, deploy wait, send) keys off that one output. This is what
   prevents paying for AI image generation on a run that won't email.

---

## 2. Resend account setup (human steps, do these first)

> ### ⚠️ ALREADY DONE FOR THIS PROJECT — skip to §2.3
>
> **Decided 2026-09-05: cf-challenge reuses wc-challenge's Resend setup.** The
> account exists, `mustardboy.xyz` is already a verified sending domain, and the
> same send-only API key is reused. Verified live on 2026-09-05 (the key
> authenticates; it is scoped to sending only, which is correct).
>
> Concretely that means:
> - **No domain purchase, no DNS records, no verification wait.** §2.1 and §2.2
>   below are historical — they document how it was done the first time, for
>   reference only. **Do not follow them.**
> - Resend verifies *domains*, not addresses, so any new local-part at
>   `mustardboy.xyz` works with zero setup. Pick one that isn't `recap@` (which
>   wc-challenge used) — e.g. `picks@mustardboy.xyz`.
> - `RESEND_API_KEY` is already in this repo's local `.env` (gitignored) and
>   documented in `.env.example`, so local development and test sends work now.
> - **The one remaining human step:** add `RESEND_API_KEY` as an Actions secret on
>   this repo — secrets do not carry across repositories.
>   <https://github.com/Zandor435/cf-challenge/settings/secrets/actions/new>
>
> **Sender decided 2026-09-05: `mb4@mustardboy.xyz`** ("mustard boy 4"), display
> name `CF Challenge`. Already written into `email/config.json`. Deliberately not
> `recap@`, which wc-challenge used.
>
> **Recipients work DIFFERENTLY here than in wc-challenge — read §6.1.** This repo
> has four groups (browns, church, family, panel) and sends one email per group.
> Addresses live in `groups/<id>/config.json` under `managers[].email`, not in a
> flat list in `email/config.json`. Zach is supplying them piecemeal; most are
> still `"TODO"`.
>
> Shared-key consequence to remember: revoking or rotating this key in Resend
> breaks sending in **both** projects. wc-challenge's pipeline is retired, so in
> practice this repo is the only live consumer.

### 2.1 Create the account and verify a sending domain *(historical — already done)*

Resend will let you send from `onboarding@resend.dev` immediately, but **only to
your own account email**. To send to the league you need a verified domain.

1. Sign up at <https://resend.com>. Free tier: **3,000 emails/month, 100/day, 1
   verified domain** — enormously more than a 10-person pool needs.
2. Buy or reuse a cheap domain. (WC Challenge used `mustardboy.xyz` — an `.xyz` is
   a few dollars/year.) You do **not** need the domain to host anything; it exists
   purely so your mail authenticates.
3. In Resend → **Domains → Add Domain**, enter the domain. Resend shows a set of
   DNS records. Add all of them at your registrar:

   | Type  | Purpose | Notes |
   |-------|---------|-------|
   | `MX`  | bounce/feedback subdomain (`send.yourdomain.com`) | required for the sending subdomain |
   | `TXT` | SPF (`v=spf1 include:amazonses.com ~all`) | on the same `send.` subdomain |
   | `TXT` | DKIM (`resend._domainkey`) | long public key; paste exactly, no line breaks |
   | `TXT` | DMARC (`_dmarc`, `v=DMARC1; p=none;`) | optional but strongly recommended |

4. Click **Verify**. Propagation is usually minutes; can be up to an hour. Do this
   *days* before your first real send — a late scramble here is the classic way to
   miss week 1.
5. Once verified, your `from` can be any address at that domain. Format matters —
   use the display-name form so inboxes show a human name:
   `"CFB Pickem <recap@yourdomain.com>"`.

### 2.2 API key *(historical — already done; the existing key is reused)*

Resend → **API Keys → Create**. Scope it **Sending access** only. Copy it once
(`re_...`); it is not shown again.

Two places it needs to live:

- **Locally**, for test sends: a gitignored `.env` at the repo root containing
  `RESEND_API_KEY=re_...`. `send_email.py` loads it via `python-dotenv` if that
  package is installed, and silently skips if not (so CI, where the secret is
  already in the env, doesn't need dotenv).
- **In CI**: GitHub repo → Settings → Secrets and variables → Actions → New
  repository secret, named `RESEND_API_KEY`. Reference it as
  `${{ secrets.RESEND_API_KEY }}` in the workflow step's `env:`.

Never commit the key. Confirm `.env` is in `.gitignore` before the first commit.

### 2.3 Reply-all group email

The pool wants replies to go to everyone. Two config fields do this:

- `to`: the full recipient array in **one** send call. Resend puts them all in the
  `To:` header, so a reply-all reaches the group. (Sending N separate personalised
  emails breaks this — don't, unless you want per-person content, in which case
  accept that replies go nowhere.)
- `reply_to`: also an array (Resend accepts a list here). Set it to the same league
  roster so a plain "Reply" also hits everyone, not just the noreply sender.

Downside to accept: everyone sees everyone's address. For a friend group, fine.

---

## 3. Dependencies and runtime

Python 3.12. Install list used in CI:

```bash
pip install resend jinja2 python-dotenv
# plus whatever your pipeline needs: numpy, pillow, google-genai, etc.
```

The send itself is tiny:

```python
import resend
resend.api_key = os.environ["RESEND_API_KEY"]
resend.Emails.send({
    "from": "CFB Pickem <recap@yourdomain.com>",
    "to": ["a@x.com", "b@y.com"],      # one group email
    "subject": subject,
    "html": html,
    "reply_to": ["a@x.com", "b@y.com"],
})
```

That is the entire API surface you need. Everything else in these files is
scheduling, gating, and content.

---

## 4. File inventory — what to steal

All paths relative to this repo (`wc-challenge/`). Copy the whole `email/` folder
as a starting skeleton, then work through this table.

### Copy nearly verbatim (mechanics, not content)

| File | Size | What it does | Changes needed |
|------|------|--------------|----------------|
| [email/render.py](reference/wc-challenge-email/render.py) | ~25 lines | Shared Jinja2 render helper so the sender and the previewer stay in lockstep. | **None.** Copy as-is. |
| [email/send_email.py](reference/wc-challenge-email/send_email.py) | ~140 lines | Loads payload + config, renders, sends via Resend, and on success snapshots the delta baseline and stamps the send date. Has `--dry-run` and `--to` (test-send override that deliberately does *not* touch the baseline). | Rename the state file paths; point `--narrative` at whatever your equivalent state file is. Logic is generic. |
| [email/should_send.py](reference/wc-challenge-email/should_send.py) | ~120 lines | The send gate. Four conditions: (1) today is a configured send date, (2) at/after a UTC hour cutoff, (3) new scored results since the last email, (4) haven't already emailed today. Exits 0 to send, 1 to skip. | Swap `phase.matches_played` for your freshness counter (e.g. `games_scored` or week number). Retune the hour and cadence for CFB — see §6. |
| [email/.gitignore](reference/wc-challenge-email/gitignore.reference) | 5 lines | Documents which email artifacts are regenerated (ignored) vs. which must persist across CI runs (committed). | Copy the pattern; it prevents a real class of bug. |

### Copy the structure, rewrite the content

| File | Size | What it does | Changes needed |
|------|------|--------------|----------------|
| [email/build_email_payload.py](reference/wc-challenge-email/build_email_payload.py) | ~500 lines | The brain. Reads the committed JSON the pipeline already produced and resolves every template variable into one flat payload: standings rows with rank-movement arrows, a featured-commentator block rotating by matchday, an "Up Next" fixture list, the subject line, the hero URL. | **Rewrite the body, keep the shape.** The docstring's "Reads / Writes" contract block is worth imitating verbatim. Its genuinely reusable ideas: rank-delta arrows computed against a *last-sent* snapshot (not "yesterday"), per-owner colour + nickname maps defined once at module top, and the rule that a missing input degrades to a hidden section rather than a crash. |
| [email/template.html](reference/wc-challenge-email/template.html) | ~230 lines | The newsletter itself: hero image, headline hook, compact standings table, one featured pundit, CTA to the site. Table-based, inline CSS, absolute image URLs, 600px max width, dark theme. | **Steal the email-client-safe scaffolding wholesale** (the nested `<table role="presentation">` structure, the hidden preheader div, the `bgcolor` + inline `background-color` doubling). Replace the sections with CFB ones. See §5 for why you can't just write normal HTML here. |
| [email/config.json](reference/wc-challenge-email/config.json) | 25 lines | `from`, `reply_to[]`, `recipients[]`, `site_base_url`, `send_dates[]`. | Rewrite entirely: your league's addresses, your domain, and a `send_dates` array of the CFB Sundays. |
| [email/preview.py](reference/wc-challenge-email/preview.py) | ~200 lines | Renders to `preview.html` for browser testing. `--sample` fabricates a fully-populated mid-season payload (with a locally generated placeholder hero as a data-URI) so you can preview **offline, before any real data exists**. `--open` launches the browser. | Copy the approach; update the sample payload to your schema. This is the highest-value dev-loop file in the folder — build it early, not last. |

### Optional / skip

| File | Verdict |
|------|---------|
| [email/generate_hero_image.py](reference/wc-challenge-email/generate_hero_image.py) | Only if you want AI cover art per email. It calls Gemini `gemini-2.5-flash-image` (Nano Banana) with a fixed art-direction prompt block plus the day's storyline, writes `site/assets/email/day_N.png`, and **on any failure patches `payload.hero_image_url = null` so the email still sends without a hero**. That graceful-degradation pattern is worth copying even if you use a different image source. Costs money per send. |
| `email/payload.json`, `email/preview.html` | Generated artifacts, gitignored. Don't copy — but do open `payload.json` to see the flat-payload shape. |
| `email/last_sent_state.json`, `email/last_send_date.txt` | **Committed on purpose** — they're the cross-run memory (delta baseline + once-per-day marker). Your version starts empty/absent; the code handles the first-run case. |

### The workflow

The orchestration was deleted from `wc-challenge`'s HEAD when the tournament ended
(commit `1a4e090`, "Retire the live pipeline"). A copy is vendored here as
[reference/wc-challenge-email/update-data.yml](reference/wc-challenge-email/update-data.yml).
It is the **whole** pipeline (fetch, score, sim, commentary, art, email, deploy) —
only the steps below are email-relevant, but the rest is a decent model for a
scheduled data pipeline generally.

The email-relevant steps, **in this exact order** (the order is the design):

1. `Build email payload` — `continue-on-error: true`, so a malformed input can
   never block the data commit.
2. `Email send gate` — runs `should_send.py`, writes `send=true|false` to
   `$GITHUB_OUTPUT`. Every later step is `if: steps.sendgate.outputs.send == 'true'`.
3. `Generate hero image` — only on send runs. `continue-on-error`.
4. `Commit refreshed data + hero image` — **before the send**, so Pages serves the
   image by the time inboxes request it.
5. `Wait for hero image to deploy` — polls the actual image URL with `curl` for up
   to ~5 min, proceeds on the first HTTP 200. `continue-on-error`.
6. `Send daily email` — `continue-on-error`, `env: RESEND_API_KEY`.
7. `Commit email delta baseline` — after the send, commits `last_sent_state.json`
   and `last_send_date.txt`.

Also worth lifting from that file: the `git add` loop that only stages paths that
exist (`[ -e "$p" ] && git add "$p"`) — a bare `git add` on a missing pathspec
exits 128 and kills the push — and the `git pull --rebase origin main` before each
push so concurrent cron runs can't be rejected as non-fast-forward.

---

## 5. Gotchas that cost real time

**Email HTML is not web HTML.** Outlook and Gmail strip `<style>` blocks, ignore
flexbox and grid, and mangle modern CSS. Non-negotiable rules, all visible in
`template.html`:

- Layout with nested `<table role="presentation" cellpadding="0" cellspacing="0" border="0">`. No divs for structure.
- **Inline CSS only.** Every `style=""` goes on the element itself.
- **Absolute URLs for every image.** No relative paths, no `cid:` attachments.
- 600px max width, and set both `bgcolor="#0c0e14"` *and* `style="background-color:#0c0e14"` — different clients honour different ones.
- Add a hidden preheader div (`display:none; max-height:0; overflow:hidden; opacity:0`) at the top of `<body>` holding your best one-line hook. It's the grey text next to the subject in the inbox list, and it's free real estate.

**The image must be deployed before the send.** If you host hero/avatar images on
GitHub Pages, the push → Pages rebuild takes a minute or two. Send first and inboxes
cache a 404. Hence steps 4–5 above: commit, poll the URL until 200, *then* send.

**Never advance the delta baseline on a test send.** `send_email.py --to
me@example.com` skips both the state snapshot and the date stamp. Without that
guard, one test send silently zeroes out the real email's rank-movement arrows.

**Freshness gate, not just a date gate.** Once the cron runs more than once a day,
"today is a send date" fires on every run of that day. You need all four conditions
in `should_send.py`, and specifically the *"new results since the last email"*
comparison — that's what makes the email land promptly after the games finish
rather than at a fixed hour that may be too early.

**Everything in the email path is `continue-on-error`.** A Resend hiccup, a Gemini
quota error, or a slow Pages deploy must never block the data commit. The site
staying current matters more than any single email going out.

**Read and write files with `encoding="utf-8"` explicitly.** These scripts do it
everywhere. On Windows, Python's default encoding is cp1252 and em-dashes / smart
quotes in generated commentary turn to mojibake in the subject line.

**Subject lines get truncated.** Aim for ~50–60 characters of signal before any
truncation. WC Challenge derived the subject from the day's most dramatic event
rather than a static template — worth doing; a subject of "Week 6 Recap" gets
ignored by week 3.

---

## 6. Adapting to the college football pick'em

### 6.1 The big one: four groups, four emails

**This is the largest divergence from wc-challenge and it is already decided in
`ARCHITECTURE.md` (L125, L166, L209) — do not redesign it.** wc-challenge was one
league with one flat recipient list. cf-challenge has four independent groups:

| Group | `display_name` | Managers | `email_enabled` |
|-------|----------------|----------|-----------------|
| `browns` | The Browns | 7 | `false` |
| `church` | (see config) | 6 | `false` |
| `family` | (see config) | 8 | `false` |
| `panel` | (see config) | 4 | `false` |

Consequences for the email code:

- **Recipients come from `groups/<id>/config.json` → `managers[].email`**, gated by
  that group's `email_enabled` flag. There is no flat `recipients` key in
  `email/config.json`, deliberately — a flat list would cross-post one group's
  standings to another's table.
- **The send loop runs per group**, so `build_email_payload.py` takes a group id and
  writes a per-group payload; `should_send.py`'s "already sent today" marker and
  delta baseline must be **per group** too (e.g. `email/state/<group_id>/`), or the
  first group's send suppresses the other three.
- `email_enabled` is `false` for all four right now. Flipping one to `true` is how
  a group goes live — that is the intended kill switch, so honour it.
- Zach is supplying addresses **piecemeal**; most `email` values are still the
  string `"TODO"`. **Treat a `"TODO"` (or empty) address as a hard skip and refuse
  to send that group's email**, rather than letting `TODO` reach Resend as a
  recipient. All 25 addresses were collected 2026-09-05..08; see the group configs.
- `manager_id` is the stable join key everywhere; `display_name` is cosmetic. Note
  browns has two Matts (`matt_m`, `hauck`) disambiguated only by `manager_id`.

### 6.2 Cadence

The other structural difference is **cadence**. WC Challenge ran a 3-hour cron over
a month-long tournament with a once-per-UTC-day email. CFB is weekly, with a
long-tailed Saturday and games finishing past 1am ET.

Recommended shape *(superseded — what was actually built is below)*:

- **Cron**: every 3h on Sunday only — `0 */3 * * 0` — plus `workflow_dispatch`.
  Don't run the cron all week. The rest-day gate pattern in `update-data.yml` (a
  separate `gate` job that checks whether today or yesterday has a game and skips
  the entire run) is the cheap way to do this if your schedule lives in a CSV.
- **`send_dates`**: the Sundays of the season, plus bowl/CFP dates. Explicit dates
  beat clever week arithmetic — they're inspectable and easy to fix.
- **Time gate**: raise it. Late west-coast kicks finish ~08:00 UTC Sunday, so a
  05:00 UTC cutoff would email before those games are scored. **13:00 UTC (9am ET)
  Sunday** is the sane cutoff.
- **Freshness counter**: replace `phase.matches_played` with your scored-games count
  for the week. Same comparison, same file mechanics.
- **Content sections**: hero → the week's headline → standings with movement arrows
  → best/worst pick of the week → next week's marquee games → CTA. That maps almost
  one-to-one onto the existing template sections.
- **Rank-delta arrows** get *more* valuable weekly than daily — a week's movement is
  a real story. Keep that mechanism.

### 6.3 What was actually built (2026-09-09) — this overrides §6.2

Three of the recommendations above were changed on contact. The as-built shape:

| Decision | Recommended | Built | Why |
|---|---|---|---|
| Cron | `0 */3 * * 0` | `0 8 * * 0`, **added alongside** the existing daily `0 13 * * *` | The daily run already refreshes the site for Tue–Fri games; the Sunday 08:00 pass is a second full pipeline run whose only extra job is the email. One fire, not eight — the every-3h shape existed to catch a tournament's staggered finishes, and a CFB Saturday has exactly one finish line. |
| Time gate | 13:00 UTC (9am ET) | **08:00 UTC** (4am ET; 3am ET once EST starts) | Commissioner's call: the recap should be in the inbox before anyone is awake. Accepted cost — a Hawaii/late-west-coast kick ending ~07:15 UTC leaves CFBD ~45 min to post the final, so a very late game can miss the board. Symptom to recognise: one game missing from an otherwise correct Sunday email. |
| `send_dates` | explicit Sundays + bowls | **not used** | The cron is the schedule and `should_send.py`'s week-freshness check is the cadence (one email per group per week number, opened by a newly filed column). The key survives in `email/config.json` marked UNUSED so nobody adds dates to it expecting them to gate anything. |

Two invariants this created, both guarded by `scripts/test_email_schedule.py`:

- **The cron hour and `should_send.SEND_HOUR_UTC` must match.** They are two
  encodings of one decision. Move the cron alone and every Sunday run refuses on
  time — silently, forever, because "gate said skip" is the normal outcome and
  reads as healthy in a green log.
- **Only the 08:00 Sunday cron (and `workflow_dispatch`) may email.** The email
  steps key off `steps.emailwindow.outputs.run`, which is false for the 13:00
  daily pass — including the one that fires on the same Sunday. Without that,
  Sunday would send twice and a midweek run that filed a column would send on a
  Wednesday.

The email steps sit **last in the job, after the Pages deploy**, so nothing in
the send path can cost the site its data commit or its deploy (rule 3), and the
email's CTA points at a site published seconds earlier. The reverse trade is
accepted: a failed deploy skips that week's send, and the recovery is a
`workflow_dispatch` re-run — safe because no state was stamped, so the gate still
owes the week. There are no image steps because `email/template.html` carries no
`<img>`; restore the playbook's commit-then-poll-for-200 dance the day it does.

---

## 7. Suggested build order

1. ~~Buy the domain, add the Resend DNS records, verify.~~ **Done** — reusing
   wc-challenge's verified `mustardboy.xyz`. See the callout in §2.
2. ~~Create the API key; put it in `.env` locally.~~ **Done** — the shared key is
   already in this repo's `.env`. Still outstanding: add it as the
   `RESEND_API_KEY` Actions secret on `Zandor435/cf-challenge`.
3. Copy `render.py`, `send_email.py`, `should_send.py`, `.gitignore` verbatim.
4. Write `config.json` with your league's roster and send dates.
5. Write a minimal `build_email_payload.py` that emits a hardcoded payload.
6. Write `preview.py --sample` and iterate on `template.html` in the browser until
   it looks right. **This is the loop — do not iterate by sending real emails.**
7. Test send to yourself: `python email/send_email.py --to you@example.com`.
   Check it in Gmail *and* on a phone.
8. Wire the real data into `build_email_payload.py`.
9. Add the workflow steps in the §4 order; test with `workflow_dispatch` and a
   `send_dates` entry set to today.
10. Before the season opener, do one full end-to-end dry run with the gates
    bypassed. Don't discover a broken step on the first real Sunday.
