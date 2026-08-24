---
type: component-spec
title: "Component Spec — Messaging Providers"
description: "Design contract for the provider-neutral messaging abstraction (Telegram/Slack/Discord/Matrix): the required core, the optional-capability tiers that degrade to text, and the report-surface contract for persistent updatable documents."
tags: [bridge]
created: 2026-08-24
updated: 2026-08-24
---

# Component Spec — Messaging Providers

**Modules:** `messaging/base.py`, `messaging/telegram.py`, `messaging/slack.py`,
`messaging/discord.py`, `messaging/matrix.py`, `messaging/markdown_layout.py`

## Purpose

One human-facing channel abstraction so that **no other component knows which chat
platform an operator runs**. The bridge (`specs/components/bridge.md`) classifies and
dispatches; the agent loop writes `outbox.md`; both reach the human only through a
`MessagingProvider`. Swapping Telegram for Slack must require zero changes outside
`messaging/`.

This is deliberately *not* the CLI-provider abstraction — see
`specs/components/providers.md` for that. Same word, unrelated contract.

## Architecture

```
agent loop ──> instance/outbox.md ──┐
                                     ├─> OutboxManager.flush()
bridge (awake.py) ──────────────────┘        │
                                             ▼
                              MessagingProvider  (messaging/base.py)
                                             │
        ┌────────────────┬───────────────────┼──────────────────┐
     telegram          slack              discord            matrix
    (HTTP poll)   (Socket Mode + Web)    (REST poll)      (Client-Server)
```

The provider is constructed once at startup and used as a **process-wide singleton**.
It holds live network state (Socket Mode client, flood counters, thread maps), so it is
never re-instantiated per message.

## Capability tiers

The contract is deliberately **thin at the bottom and optional above it**. This is the
component's central design decision: platforms differ enormously in what they can
express, and koan must run on the weakest of them without any caller branching on
provider name.

**Tier 0 — required** (`@abstractmethod`; a provider is unusable without all five):
`send_message`, `poll_updates`, `get_provider_name`, `get_channel_id`, `configure`.

**Tier 1 — optional, base class returns a benign default.** A provider that cannot do
these simply does not override them:

| Capability | Default | Meaning of the default |
|---|---|---|
| `get_last_message_ids` | `[]` | No message-ID tracking; callers must not rely on IDs. |
| `get_bot_username` | `""` | Unknown — callers handle empty gracefully. |
| `send_typing` / `stop_typing` | `True` | **No-op is success.** Absence of an indicator is never an error. |
| `add_reaction` | `False` | Unsupported → caller falls back to a text acknowledgement. |
| `reaction_acknowledges_mission` | `False` | Keep the informative text ack. |
| `publish_report` | `None` | No persistent surface → caller falls back to `send_message`. |

Current coverage (informative, not normative — providers may add overrides at any time):

| | Tier 0 | typing | reactions | reports |
|---|---|---|---|---|
| Slack | ✅ | ✅ (+`stop_typing`) | ✅ (replaces text ack) | ✅ canvas |
| Telegram | ✅ | ✅ | ✅ (text ack kept) | — |
| Matrix | ✅ | ✅ | — | — |
| Discord | ✅ | — | — | — |

## Key types & functions

| Symbol | Contract |
|---|---|
| `Message` / `Reaction` / `Update` | Provider-agnostic dataclasses. `raw_data` carries the untranslated payload for provider-specific needs; **no caller outside `messaging/` may read `raw_data`.** |
| `send_message(text, reply_to_message_id=0)` | Sends with provider chunking. Returns True only if **all** chunks sent. `reply_to_message_id` is an opaque *token* (not necessarily a platform message ID) that providers map to their own threading model. |
| `chunk_message(text, max_size)` | Character-based split, `DEFAULT_MAX_MESSAGE_SIZE = 4000`. Providers override for smarter boundaries. Always returns ≥1 chunk, even for empty text. |
| `markdown_layout.lay_out_markdown(text)` | Inserts blank lines **between** Markdown constructs, never inside one; preserves fenced code verbatim; idempotent. Applied **before** chunking so a chunk boundary can never fall between a heading and its body. |
| `publish_report(key, title, markdown, surface_id=None)` | Create-or-update a persistent, named document in the channel. Returns a `ReportRef` (`key`, `surface_id`, `url`) or `None` if unsupported. `surface_id` is the id previously returned for `key`, so **providers stay stateless** — the caller owns persistence. See below. |

## Report surfaces

> **Status:** contract landed ahead of implementation (contract-first, per
> `docs/design/spec-changes-are-architectural.md`). Slack is the first implementation;
> the other three intentionally inherit the `None` default until someone needs them.

A **report surface** is a persistent, named, updatable document attached to the channel.
It exists because koan's recurring reports (the ops digest, `/report`, audits) are
*snapshots*, not events: yesterday's digest has no value once today's exists, yet the
chat-message model forces every regeneration into new scrollback the human must
archaeologise. Every supported platform has a primitive for this:

| Provider | Surface | Requirement |
|---|---|---|
| Slack | standalone canvas (`canvases.create` / `canvases.edit` with a whole-document `replace`, shared via `canvases.access.set`) | `canvases:write`; `files:read` for the permalink only; **a paid Slack plan** — standalone canvases are not available on Free, though channel/DM canvases are |
| Telegram | pinned message (`editMessageText` + `pinChatMessage`) | admin rights to pin |
| Discord | pinned message, or a thread's starter message | `MANAGE_MESSAGES` |
| Matrix | replaced event (`m.replace`) + `m.room.pinned_events` | power level to pin |

### Invariants

- **Idempotent by key.** `key` is a stable, operator-facing identifier
  (`ops-digest`). Publishing the same key **replaces** the surface's content. A report
  surface is never appended to — that would recreate the scrollback problem it exists
  to solve.
- **Never load-bearing.** A missing scope, revoked permission, or absent capability
  MUST degrade to `send_message` with the full report text. A report is **never
  dropped** because a surface was unavailable. Same fail-open rule as
  `notify_dedup.py`.
- **A new surface MUST be made reachable.** Platforms create bot-owned documents
  private to the bot (a Slack canvas comes back `access: owner`, `channels: []`), so a
  provider that only creates one has published a link nobody can open. Granting the
  posting channel access is part of creating the surface, and the grant is
  **read-only** — the surface is overwritten every run, so a human edit would be
  silently destroyed by the next publish. A failed grant leaves a correct but unshared
  surface: degraded, not broken, and never a failed publish.
- **Markdown in, always.** Callers pass Markdown; the provider translates. No
  caller ever passes Slack `mrkdwn`, Telegram HTML, or Matrix formatted bodies.
  `lay_out_markdown` applies before publishing, exactly as it does before sending.
- **The channel still gets a message.** Publishing a surface does not by itself notify
  anyone — canvases and pins are silent. Report delivery therefore emits a short
  **pointer message** (title + link) through the normal `send_message` path, so
  notifications, mobile, and priority filtering keep working. `messaging.reports.notify`
  selects `pointer` (default) / `silent` / `full`.
- **Surface IDs are cache, not truth.** The `key → surface_id` map persists to
  `instance/.report-surfaces.json` via `utils.atomic_write()`, owned by
  `report_delivery.py` and passed *into* the provider. If a stored ID is gone (deleted
  canvas, unpinned message), the provider creates a fresh surface and returns the new ID
  for the caller to record. A lost ID is never a failure, and no provider persists state
  of its own.
- **A report is never rewritten by the formatter.** Outbound messages normally pass
  through `notify.py`'s AI formatter (`_format_message`). Report bodies MUST bypass it —
  on the surface path *and* on the text-fallback path. A report is already a finished
  document; letting the formatter re-prose it would discard the structure the mission
  deliberately produced.
- **Opt-in.** `messaging.reports.enabled` defaults to **false**: upgrading koan changes
  no operator's behaviour until they add the scope and flip it.

### Outbox transport

The agent loop reaches the human only through `outbox.md`, which is plain text. Report
routing therefore reuses the established **line-marker** convention already used for
priority (`_OUTBOX_PRIORITY_RE` in `outbox_manager.py`):

```
[report:ops-digest] Ops Digest — 2026-08-24
## ACT TODAY
- my-toolkit#463 — approved, green: merge it.
```

`parse_outbox_report()` strips and returns `(key, title, body)`, mirroring
`parse_outbox_priority()`. It tolerates up to 3 leading spaces — the same allowance
Markdown gives its own block constructs — so a mission prompt that renders the marker
indented still routes instead of leaking a literal `[report:…]` line into chat. Absent marker, unsupported provider, or `reports.enabled:
false` → the content flushes as an ordinary message, byte-for-byte as today. This keeps
the whole feature a **routing decision at flush time**, with no new IPC surface between
the two processes.

## Invariants (component-wide)

- **Provider name is never branched on outside `messaging/`.** Callers ask
  "does this capability work?" (a return value), never "is this Slack?". A `provider ==
  "slack"` test in `app/` outside `messaging/` is a contract violation.
- **No-op is success.** Tier-1 defaults return the value meaning "nothing to do,
  nothing broken". Returning `False`/`None` means *unsupported*, and callers MUST have a
  text-based fallback for every one.
- **Singleton with live state.** Providers are not re-instantiated per message; anything
  cached in them (threads, flood counters, sockets) must tolerate a long-lived process.
- **`configure()` fails loudly, at startup.** Credential validation prints an actionable
  message to stderr and returns False — never a silent degradation discovered later.
- **Chunking is the provider's problem.** No caller pre-splits text to fit a platform
  limit.

## Integration points

- `OutboxManager.flush()` (`specs/components/bridge.md`) is the only agent-loop → human
  path; it owns crash-safe staging and now report routing.
- `notify.py` wraps provider sends with flood protection, priority filtering
  (`notifications.min_priority`) and cross-incarnation dedup (`notify_dedup.py`).
- `docs/messaging/*.md` are the per-provider setup guides; each scope added here must be
  added to the matching guide **and** `docs/messaging/slack-app-manifest.json`, with an
  upgrade note for existing installs (the `reactions:write` note is the template).

## Known debt / watch-outs

- `DEFAULT_MAX_MESSAGE_SIZE = 4000` is shared across providers even though the real
  limits differ (Slack ~40k for `text`, Telegram 4096). Conservative, not correct.
- `reply_to_message_id: int` types an *opaque token* as an integer, which forced Slack to
  keep side maps (`_thread_for_token`, `_ts_for_token`) from int tokens to string
  `thread_ts`. A future contract should make it an opaque handle type.
- Discord implements Tier 0 only; Matrix lacks reactions. Neither is a bug — but
  operators on those providers silently lose acknowledgement affordances, which is worth
  stating in their setup guides.

## Change protocol

Adding a **capability** means: a Tier-1 default in `base.py` whose default value means
"unsupported", a documented text fallback in every caller, a row in the capability
matrix above, and the per-provider scope/permission documented in `docs/messaging/`.
Adding a **provider** means Tier 0 only — never a requirement to implement Tier 1.
Changing Tier 0 is an architectural change affecting all four providers and must be
declared in the PR body.
