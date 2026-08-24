---
type: doc
title: "Report surfaces (persistent reports in the channel)"
description: "How to publish recurring reports — the ops digest, /report, audits — into a persistent, updatable channel document (a Slack canvas) instead of a new chat message each time."
tags: [messaging]
created: 2026-08-24
updated: 2026-08-24
---

# Report surfaces (persistent reports in the channel)

Kōan's recurring reports are **snapshots, not events**. Yesterday's digest has no
value once today's exists — yet the chat-message model pushes every regeneration
into new scrollback, so finding the current one means scrolling past all the old
ones.

A **report surface** fixes that: Kōan publishes the report as a first-class rendered
artifact instead of pasting it as text. A collapsed card you click is less noise than 25
lines of Markdown, and it renders properly.

There are two shapes, and they optimise for opposite things:

| Shape | What a run does | Best when |
|---|---|---|
| **`upload`** (default) | Uploads `<key>-<date>.md`, shared to the channel. Never overwrites. | You want **history** — every past run stays readable as its own card. Works on **every Slack plan**. |
| **`canvas`** | Replaces one canvas per report key, in place. | You want **one permanent link** that is always today's. Needs a **paid** Slack plan. |

This is off by default. Nothing changes until you enable it.

## Enabling it

```yaml
# instance/config.yaml
messaging:
  reports:
    enabled: true       # default: false
    surface: upload     # upload (default) | canvas
    notify: pointer     # pointer (default) | silent | full
```

Then grant your provider's permission — for Slack, the `canvases:write` scope,
followed by **reinstalling the app** (see [slack.md](slack.md) Step 3). Without it
nothing breaks: reports simply keep arriving as ordinary messages.

> **Slack Free plan:** the default `upload` shape works on every plan — it needs only
> `files:write`, which Kōan already has. The paid-plan note below applies to
> `surface: canvas` only.

> **`surface: canvas` needs a paid Slack plan.** Kōan creates a *standalone*
> canvas, and per Slack's own documentation "channel and direct message (DM) canvases
> are available on all plans, while standalone canvases are only available on paid
> plans." On a Free workspace `canvases.create` fails and every report falls back to an
> ordinary message — degraded, never broken. A future enhancement could fall back to a
> *channel* canvas (free-plan-eligible), at the cost of supporting only one report key
> per channel, since a channel has exactly one channel canvas.

### `notify` modes

With `surface: upload` the artifact card **is** the channel message, so `pointer` posts
nothing extra and `silent` cannot be honoured — suppressing the card would make the
artifact invisible. The table below describes `canvas`; for `upload`, only `full` differs
from the default.

| Mode | Behaviour |
|---|---|
| `pointer` | Update the surface, then post a one-line message with the title and link. **Recommended** — surfaces are silent, so this is what actually notifies you (and reaches your phone). |
| `silent` | Update the surface only. No message at all. Use when you'll go look on your own schedule. |
| `full` | Update the surface *and* post the whole report as a message, with a `— report surface updated` link appended. You get the glance *and* a click-through to confirm the surface really updated. Best while you are deciding whether you like surfaces. |

## Provider support

| Provider | Surface | Requires |
|---|---|---|
| Slack | Standalone canvas, shared read-only with the channel | `canvases:write` (+ `files:read` for the link) **and a paid Slack plan** |
| Telegram | — | *not yet implemented; falls back to a message* |
| Discord | — | *not yet implemented; falls back to a message* |
| Matrix | — | *not yet implemented; falls back to a message* |

The contract is provider-neutral (`specs/components/messaging.md`) — each of these
platforms has a pinned/replaceable-document primitive, so the remaining three are
implementable without touching anything outside `messaging/`. They are absent
because nobody has needed them yet, not because they can't work.

## Emitting a report from a mission

Prefix the outbox content with a `[report:<key>]` marker and an optional title:

```
[report:ops-digest] Ops Digest — 2026-08-24
## ACT TODAY
- my-toolkit#463 — approved, green: merge it.
```

- **`<key>`** is a stable, lowercase identifier naming the *report*, not the run. Use a
  new key for a genuinely different report (`ops-digest`, `pr-report`,
  `security-audit`), never a dated one like `ops-digest-2026-08-24`. Under `upload` the
  date is added to the filename for you; under `canvas` the key is what makes the run
  replace yesterday's rather than pile up beside it.
- **The title** is optional; without it the key is used (`ops-digest` → "Ops
  Digest").
- The marker composes with `[priority:…]`, which still governs the pointer
  message.

Report bodies are published **verbatim** — they skip the AI message formatter,
because a report is already a finished document.

## What happens when something is missing

A report is never dropped. Every failure degrades to sending the full report as an
ordinary message:

| Situation | Result |
|---|---|
| `reports.enabled: false` | Plain message (the default) |
| Provider has no surface support | Plain message |
| Scope missing / not reinstalled | Plain message, error logged |
| `surface: canvas` on a Slack Free plan | Plain message, error logged |
| Upload rejected (`files:write` missing) | Plain message, error logged |
| Stored surface deleted by a human | A fresh surface is created |
| Publish fails for any other reason | Plain message, error logged |

The canvas is shared **read-only** with the channel Kōan posts to, automatically, at
creation time. Read-only is deliberate: the surface is overwritten on every run, so any
edit you made in it would vanish at the next publish. Annotate the vault copy instead.

Surface ids live in `instance/.report-surfaces.json`. It's a cache — delete it and
Kōan creates new surfaces on the next run. Deleting a canvas in Slack is safe for
the same reason.

## See also

- [`specs/components/messaging.md`](../../specs/components/messaging.md) — the
  provider contract, capability tiers, and invariants
- [slack.md](slack.md) — Slack app setup and scopes
- [messaging-level.md](messaging-level.md) — controlling how much Kōan says
