# Unified Coordination Layer

**Status:** product architecture (direction of travel).  
**Audience:** anyone adding a subsystem, skill, integration, job, or UI view.  
**Related:** `RUNTIME_CONTRACTS.md`, `LIFECYCLE_TRUTHFULNESS.md`, `THREAD_EXECUTION_CONTEXT.md`, `MODE_SYSTEM.md`, `VIDEO_EDITOR_ARCHITECTURE.md`.

---

## Principle (read this first)

**Do not optimize for making the next demo pass.**  
**Optimize for making the next subsystem easy to build.**

If the authority layer, jobs, memory, skills, and execution pipeline are clean,
then Calendar, Voice, Video, Images, Agents, and future media tools become
**plugins into a stable foundation** — not new special cases that invent their
own idea of “done,” “pending,” or “allowed.”

**Every time you replace a guess with one authoritative source of truth, the
entire platform becomes more predictable and easier to extend.**

---

## What EchoSpeak already has (do not rebuild)

Chat, research, coding, memory, Projects, Sessions, tools, approvals, skills,
integrations, video foundation, durable executions and ToolRuns, authority and
permissions, Studio/Code/Research/Video views, local and hosted models.

The recurring glitches are not “missing identity.” They come from **overlapping
ownership**, **different interpretations of the same fact**, and **prose used
as runtime truth**.

---

## Canonical request lifecycle

Every request — coding, research, video, memory, calendar, generation — should
follow one path:

```text
User request
  → identify Session and Project
  → understand the objective
  → select capabilities (structural inventory, not vibes)
  → build a structured plan
  → check permissions / approvals
  → execute exact tools
  → verify outcomes
  → persist truth (Executions, ToolRuns, domain stores)
  → project that truth to the UI
  → generate the response from projection (not invent status)
```

Specialized engines (video ops, sports search, coding implement) are allowed.
**Separate definitions of completion, permission, retry, or truth are not.**

---

## Real capability system (no guessing “I can edit files”)

At plan and UI time, EchoSpeak must know **this moment’s** inventory, e.g.:

```text
Project attached: yes | no
file_read: available | blocked (reason)
file_write: available | blocked (reason)
write approval: required | auto | blocked
terminal: available | disabled
video timeline: available | unavailable
render worker: unavailable
generation model: not configured
```

**One capability result** must drive:

* planning;
* error messages;
* Settings / readiness;
* Studio / Code / Video chrome.

Never invent capability from model prose or from “the tool is registered
somewhere in the process.”

Primary surfaces today (converge these, don’t fork):

* `ToolRegistry` — single tool registration / `is_action` / policy owner
* `GET /capabilities?thread_id=…` — Session-bound; inventory = full ToolRegistry
* `coding_readiness` — intersection report only (does not invent availability)
* video adapter registry / job shells — domain projection; apply still ToolRun/approval
* `SkillsRegistry` + `skill_selection` — executable skill contracts
* Project path + `ALLOW_*` + ApprovalRecord — mutation authority

---

## Reasoning vs execution (hard split)

| Layer | May decide | Must not decide alone |
|-------|------------|------------------------|
| **Model** | Next useful action, edit *proposal*, research angle | Whether path is in Project; whether write succeeded; whether approval applies |
| **Runtime** | Path scope, permissions, schema, ToolRun outcome, verification, durable status | “It looks done because the model said so” |

Even a strong model will eventually hallucinate if **prose is allowed to become
runtime truth**. Small models must fail honestly (“could not produce a valid
edit plan”) rather than claim completion.

---

## One state owner per concept

| Concept | Single authority (target) | Others may only |
|---------|---------------------------|-----------------|
| Project attachment | ThreadSessionState + ProjectManager | Display / cache |
| Session identity | threads / StateStore | Display |
| Current execution | StateStore Execution | Stream UI keyed by id |
| ToolRun state | StateStore ToolRun (stable id) | Project rows from ToolRuns |
| Pending approval | ApprovalRecord | Confirm/cancel UI |
| Retry target | thread retry_target | Resume path only |
| Memory records | memory / profile / curated stores | Inject into context |
| Active coding work | ActiveWorkStore | Briefs / continuity |
| Video timelines / revisions | VideoEditorStore | Video UI projection |
| Background jobs | **one** job system (target) | Progress UI |

If two modules can *change the meaning* of the same concept, that is a defect.

---

## Skills as formal contracts (not prompt packs)

A skill should declare:

* which requests it handles;
* required tools and permissions;
* expected inputs / structured outputs;
* verification of results;
* model / hardware needs (if any);
* whether it may mutate anything;
* behavior when a dependency is unavailable.

**Example:** `remove_silence` must not silently rewrite the timeline. It
produces a **structured edit plan** (operations + evidence); the **video
runtime** applies those operations under authority.

Same pattern: research, coding, tasks, calendar, generation.

---

## One durable job system (target)

Quick tool calls ≠ long-running work. Unify:

* deep research;
* video render / proxy / transcribe;
* local model download;
* large folder index;
* media generation.

Required states: `queued | running | blocked | cancelled | failed | completed`
plus progress, cancel, retry, resource needs, artifacts, Project/Session
ownership, honest restore after refresh.

Until this exists, every major feature will invent its own spinner and lose
state on reload. Prefer **extending one job shell** (see video jobs) over new
ad-hoc backgrounds.

---

## Models are workers, not the architecture

| Role | Small local models | Stronger hosted models |
|------|--------------------|-------------------------|
| Fit | Chat, simple intent, utility tools, named-file read, small structured propose | Multi-file plans, deep research, hard JSON multi-step, rich edit judgment |
| Failure mode | “Could not produce a valid plan” | Still must pass runtime verification |

Runtime, authority, and verification stay **identical** across models. Selection
may later use task hardness, context size, privacy, cost, hardware — never
silent capability downgrade disguised as “model profile.”

See equal-access rules in `RUNTIME_CONTRACTS.md` §A.

---

## Integrations share one framework (target)

Calendar, Gmail, TTS, generation, Discord, etc. share:

auth · health · capabilities · read/write · approval · rate limits · cost ·
webhooks · errors · retries · ownership · revocation.

No integration may bypass the main execution or approval path.

---

## Frontend never reconstructs truth

UI **projects** backend state. It must not infer completion from:

* last assistant sentence;
* nearby tool chrome;
* optimistic local flags;
* spinner stop;
* “N of N tasks green” without ToolRuns.

Every view (chat, Studio, Code, Video) should consume the same structured
projection where possible:

```text
status
tools attempted / successful
files or domain objects changed
approvals
blockers
verification
jobs
next action
```

See also `LIFECYCLE_TRUTHFULNESS.md` and `UI_AGENT_STATE_MAP.md`.

---

## Durable product models still needed (before feature splash)

| Model | Must not live as |
|-------|------------------|
| Personal Tasks (user todos) | Chat lines |
| Calendar Events | Chat lines |
| Research Sessions + evidence | Free prose only |
| Integration Accounts | Scattered env flags only |
| Voice session / stream state | Ephemeral UI only |
| Media generation jobs + assets | Unowned temp files |
| Video analysis artifacts | Unstructured notes |
| Skill install / version | Loose folder without contract |

Domain stores (like `VideoEditorStore`) are the pattern: **one owner, immutable
revisions where needed, UI as projection.**

---

## Glitch classes to watch (test taxonomy)

When something fails, capture:

```text
What you asked
What Echo said
Which tools actually ran
What durable status was recorded
What really changed (disk / domain store)
```

Classify:

1. understanding  
2. planning  
3. capability selection  
4. permission  
5. tool execution  
6. verification  
7. persistence  
8. frontend projection  
9. model limitation  

Do not open a “fix everything” pass from a single bad sentence.

### Highest-risk guesses (reduce these first)

1. New request resumes an older workflow without intent.  
2. Cross-Session / cross-Project bleed.  
3. UI success before durable verification.  
4. Tool registered but not in current executable inventory.  
5. Confirm applies to the wrong proposal.  
6. Failed ops still create “changed file” / complete rows.  
7. Skills as authority shortcuts.  
8. Long jobs vanish after refresh.  
9. Studio / chat / Video reading different stores for the same fact.  
10. Small-model limits mislabeled as runtime limits (or the reverse).

---

## How to add the next subsystem (checklist)

Before coding Calendar / Voice / Image gen / Agents:

1. **Name the single store** for that domain’s durable state.  
2. **Map every tool** through the shared lifecycle (no private “done”).  
3. **Declare capabilities** for readiness and `/capabilities`.  
4. **Define verification** (what ToolRun + domain head mean success).  
5. **Define approval** if mutation leaves the machine or user data.  
6. **UI only binds** to execution_id / domain revision / job id.  
7. **Fail closed** when inventory or verification is incomplete.  
8. **Write one transition test** (attach, confirm, cancel, switch Session).

If a change only makes a demo green by special-casing chat text or a single
view, **reject it** in review.

---

## Relationship to existing docs

| Doc | Role |
|-----|------|
| This file | North-star coordination + extension rules |
| `RUNTIME_CONTRACTS.md` | Concrete contracts + known gaps + live gates |
| `LIFECYCLE_TRUTHFULNESS.md` | Recovery / confirm / ToolRun / projection honesty |
| `THREAD_EXECUTION_CONTEXT.md` | Thread state schema |
| `MODE_SYSTEM.md` | Mode as routing, not authority |
| Domain docs (e.g. video) | Specialized engine; still subordinate to this lifecycle |

---

## Immediate engineering priority (ordered)

1. **Kill dual ownership** — any field two modules can mutate differently.  
2. **Capability inventory** — one Session-bound report used by plan + UI.  
3. **Projection-only finals** — no “complete” without ToolRuns / domain verify.  
4. **Job system consolidation** — start from existing job shells; one status enum.  
5. **Skill schema** — formal declare before new skill packs.  
6. **Then** add Calendar / Voice / generation adapters as plugins.

The overall direction is correct. **Coordination, ownership, and observability
matter more than adding features** until guesses are gone.
