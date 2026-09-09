# READ-ONLY REFERENCE — wc-challenge email pipeline

**This is not live code. Nothing in cf-challenge should import, execute, or depend
on anything in this folder.**

A snapshot of the working Resend email system from the `wc-challenge` repo (World
Cup 2026 pool), copied here on **2026-09-03** so the CFB pick'em email can be
built by reading and rewriting it rather than reinventing it.

Read [../../RESEND_EMAIL_PLAYBOOK.md](../../RESEND_EMAIL_PLAYBOOK.md) first — it
explains the architecture, the Resend account/DNS setup, and which of these files
to copy verbatim vs. rewrite.

## Contents

| File | Copy verbatim? | Notes |
|------|----------------|-------|
| `render.py` | **Yes** | Shared Jinja2 render helper. Fully generic. |
| `send_email.py` | **Yes** | The Resend send + post-send state snapshot. Only paths change. |
| `should_send.py` | **Yes** | The four-condition send gate. Retune the hour/counter for CFB. |
| `gitignore.reference` | Yes (as `email/.gitignore`) | Renamed from `.gitignore` so it doesn't act on this folder. Documents regenerated vs. committed artifacts. |
| `build_email_payload.py` | No — rewrite | The content brain. Keep the shape and the docstring contract; rewrite the body for CFB. |
| `template.html` | Structure only | Keep the email-client-safe table scaffolding; replace the sections. |
| `config.json` | No — replace | Contains the WC league's roster and `mustardboy.xyz` sender. Rewrite entirely. |
| `preview.py` | Approach only | `--sample` offline preview. Build the CFB equivalent early — it is the dev loop. |
| `generate_hero_image.py` | Optional | Gemini cover art per send. Costs money. The graceful-degradation pattern is the reusable part. |
| `update-data.yml` | Excerpt | The full retired GitHub Actions pipeline. Only the seven email steps matter; see §4 of the playbook for the required order. |

## Not copied

`payload.json` and `preview.html` are regenerated artifacts. `last_sent_state.json`
and `last_send_date.txt` are cross-run state that starts empty in a new project —
the code handles the first-run case.

## Delete me

Once `cf-challenge/email/` exists and sends, this folder has served its purpose.
