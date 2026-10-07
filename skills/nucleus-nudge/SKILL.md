---
name: nucleus-nudge
description: >
  Send Nucleus CRO Deal Desk forecast nudges for one recipient, many recipients,
  or every open nudgeable row. Use when Justin says "nudge", "nudge all",
  "nudge the open list", "send the forecast nudge", or asks Alicia to remind
  reps about Deal Desk corrections.
metadata:
  version: "0.1.0"
  author: "Clearspeed RevOps"
---

# Nucleus CRO nudge

Alicia drives Nucleus Deal Desk nudges through authenticated tools. Bulk always
means **one identical message**. “All” / “the open list” means every **open
nudgeable** row on the current Deal Desk table; after send those show **Sent**.

## Tools

| Tool | When |
|---|---|
| `cro_nudge_list` | See open targets and follow-through (`not_nudged` / `sent` / `corrected`). |
| `cro_nudge_draft` | Compose the identical draft for one id, many ids, or `all_open=true`. Nothing is delivered. |
| `cro_nudge_send` | Deliver. **Gated** — use `propose_action` unless Justin already confirmed. |

API (Nucleus): `GET/POST https://nucleus.clearspeed.com/api/cro/deal-nudge/machine`
with `CRO_BRIEF_MACHINE_SECRET`.

## Speaking flow (default)

1. Call `cro_nudge_list` if you need who is open.
2. Call `cro_nudge_draft` with `opportunity_id`, `opportunity_ids`, or `all_open=true`.
3. Read the draft and recipient count back to Justin.
4. `propose_action` → `cro_nudge_send` with the **same** recipients and final message.
5. Wait for his yes (`yes` / `send it` / `go ahead`). Only then does send run.

## “Send now”

If he explicitly says send now / send it / do it with the draft already agreed,
still put the exact `cro_nudge_send` args through `propose_action` so the gate
object matches what runs. Do not invent a parallel send path.

## Hard rules

- Bulk = identical message for every recipient. Never personalize per row in bulk.
- `all_open=true` = current open nudgeable Deal Desk list only (owner email present, not Corrected).
- Message must be ≥ 20 characters.
- Never claim a nudge was sent without a successful `cro_nudge_send` result.
- Do not use this for Government signal nudges, hygiene manager nudges, or Slack DMs outside Deal Desk.

## Example asks

- “Nudge Acme for DFTU.” → list/draft one → confirm → send.
- “Nudge these three deals with the same note.” → draft with `opportunity_ids` → confirm → send.
- “Nudge all open.” → draft with `all_open=true` → confirm → send; they show Sent.
