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

A **report surface** is a first-class rendered artifact in the channel, published in
place of a chat message. It exists because koan's recurring reports (the ops digest,
`/report`, audits) are *documents*, not events: pasting 25 lines of Markdown into a
channel every morning renders badly and buries the current one under the old ones.

There are two legitimate shapes, and they optimize for opposite things. A provider
declares which it implements; an operator may choose when a provider offers both.

| Shape | Semantics | Optimizes for |
|---|---|---|
| **`upload`** | A new dated artifact per run. Never overwrites. | *History* — every past run stays readable as its own card. |
| **`canvas`** | One document per key, replaced in place. | *Currency* — one permanent link that is always today's. |

Neither is the default for all time: `upload` is the better default because it works on
every plan, renders, and cannot destroy history, and because a collapsed artifact card is
*less* channel noise than the report text it replaces. `canvas` is right when a stable
link matters more than the archive.

Platform primitives for each shape:

| Provider | `upload` shape | `canvas` shape |
|---|---|---|
| Slack | `files.upload` of `<key>-<date>.md`, shared to the channel. Slack renders Markdown in its file viewer. Needs `files:write` (+ `files:read` for the permalink) and **works on every plan**. | standalone canvas (`canvases.create` / `canvases.edit` with a whole-document `replace`, shared read-only via `canvases.access.set`). Needs `canvases:write` and **a paid plan** — standalone canvases are not available on Free. |
| Telegram | document upload (`sendDocument`) | pinned message (`editMessageText` + `pinChatMessage`), needs pin rights |
| Discord | file attachment | pinned message, or a thread's starter message (`MANAGE_MESSAGES`) |
| Matrix | `m.file` event | replaced event (`m.replace`) + `m.room.pinned_events` |

Only Slack implements either shape today; the rest inherit the `None` default and fall
back to a message. **`files.*` has no edit-content method** (`files.upload` creates,
`files.delete` removes — there is no `files.edit`), which is *why* `upload` is inherently
dated-new rather than replace-in-place. That is a platform constraint, not a design
choice.

### Invariants

- **`key` names the report, not the artifact.** `key` is a stable, operator-facing
  identifier (`ops-digest`) that groups every run of the same report. What a republish
  does with it is **shape-dependent**, and callers MUST NOT assume either:
  - `canvas` — replaces that key's single document in place. Never appends: appending
    would recreate the scrollback problem the capability exists to solve.
  - `upload` — creates a new dated artifact and leaves prior ones untouched. History is
    the point, so destroying it would defeat the shape.

  A caller that needs one specific semantic must read the configured shape rather than
  infer it from the return value.
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
- **The channel still learns about it.** Under `canvas` the publish is silent (canvases
  and pins notify nobody), so delivery emits a short **pointer message** (title + link)
  through the normal `send_message` path to keep notifications, mobile and priority
  filtering working. Under `upload` the shared artifact card **is** a channel message, so
  no pointer is needed and `silent` is not achievable — the card cannot be suppressed
  without making the artifact invisible. A `full`-mode footer is likewise omitted for
  `upload`: the card sits beside the message and already carries the link. Note the
  corollary — a shared artifact's own message has **empty text**, so `upload` + `pointer`
  gives no readable preview in the channel; `full` is the mode that preserves an
  at-a-glance read. `messaging.reports.notify`
  selects `pointer` (default) / `silent` / `full`.
- **Surface IDs are cache, not truth.** The `key → surface_id` map persists to
  `instance/.report-surfaces.json` via `utils.atomic_write()`, owned by
  `report_delivery.py` and passed *into* the provider. Under `canvas` it is what makes a
  republish land on the same document; if a stored ID is gone (deleted canvas, unpinned
  message) the provider creates a fresh surface and returns the new ID to record. Under
  `upload` nothing is reused — the entry simply records the most recent artifact, which
  is what the pointer link refers to. Either way a lost ID is never a failure, and no
  provider persists state of its own.
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
`parse_outbox_priority()`. A marker in a **mission result** is equally sufficient:
`mission_runner._should_forward_result` forwards any result carrying one to the outbox,
bare (no icon or mission-title prefix, which would both echo the prompt and break the
line-anchored match). Delivery MUST NOT depend on the agent choosing to write
`outbox.md` itself — observed working on one run of a report mission and silently not on
the next, losing the report. The existing idempotency guard still applies: a result is
not forwarded when the session already wrote to `outbox.md`, so there is no double
delivery. It tolerates up to 3 leading spaces — the same allowance
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
