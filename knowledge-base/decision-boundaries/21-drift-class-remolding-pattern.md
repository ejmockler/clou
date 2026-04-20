# DB-21: The Drift-Class Remolding Pattern

## Decision

**Validated artifacts follow the schema-first pattern: a dataclass is
the single source of truth; code renders canonical markdown from it;
code parses markdown back to it (tolerantly, to recover from legacy
drift); code validates the parsed form; a hook denies direct ``Write``
for the artifact path; the LLM only ever passes structured input to an
MCP writer tool.**

Every validated artifact added after 2026-04-20 follows this pattern.
Existing artifacts migrate into it as they surface drift incidents.

## Context

Three incidents within a two-week window, all the same class of bug:
LLM freeforms a format that code independently validates, and the two
sides drift apart under scope stretch.

1. **Slug-drift (paths).** Parallel workers briefed with
   ``execution-{task_slug}.md``; ``task_slug`` was LLM-owned, drifted
   between cycles (``extend-logger`` vs ``extend_logger``),
   validation tripped on duplicate shards, blocked the 4th phase from
   running. See ``clou/shard.py``, ``clou/coordinator.py`` cleanup,
   ``clou/coordinator_tools.py::clou_brief_worker``.

2. **Supervisor cleanup hole.** The supervisor had no way to remove
   orphan intermediate artifacts without escalating to the user; the
   shell hook blocked the obvious ``rm`` pathway, and no MCP tool
   filled the gap. Fixed by adding ``clou_remove_artifact`` with
   path-scope + reason telemetry. See ``clou/orchestrator.py``.

3. **Assessment-drift (format).** Brutalist + evaluator wrote
   ``assessment.md`` with ad-hoc sections (``## Phase: X``,
   ``## Classification Summary``, ``## Rework Signal Roll-up``) when
   a cycle covered multiple phases in parallel. Regex validator
   required ``## Findings`` and errored "missing '## Findings'" even
   though 50 findings were present under drifted section names. See
   ``clou/assessment.py``, ``clou/coordinator_tools.py::clou_write_assessment``
   / ``clou_append_classifications``.

All three are the same underlying failure:

> Canonical structure lived in two places — prompt text (LLM reads
> as guidance) and code (regex/hook enforces as law) — with no shared
> source of truth that both derived from.  Every time scope stretched
> beyond what either side foresaw, the two drifted apart, and the
> code-side check lost.

Two incompatible mental models collide:

- **Code's model — "artifact is a schema."** Fixed section names,
  fixed field patterns, regex-parseable skeleton.
- **LLM's model — "artifact is a report."** Narrative document whose
  organizational structure emerges from what's being reported;
  richer content begets richer structure.

Both models are correct in their domains. They cannot both own the
same artifact without a shared source of truth.

## Principle

**Code owns format. LLM owns content.**

The markdown document is a *rendered view* of the structured data —
it is not the source of truth. The source is a dataclass.

## The Recipe — seven steps

Apply in this order. Each step is small; the whole remolding is
usually a single afternoon per artifact.

### 1. Schema

Define a dataclass representing the structured form of the artifact.
Located in a new module ``clou/<artifact>.py`` (for an artifact named
``<artifact>.md``).

```python
@dataclass(frozen=True, slots=True)
class <Artifact>Form:
    ...
```

Rules:
- Frozen + slots (immutability, small memory).
- Use ``tuple`` instead of ``list`` for collection fields (immutability
  compatible with frozen dataclass).
- ``Literal`` types for finite enums (status, severity, kind).
- Export the enum sets (``VALID_STATUSES = (...,)``) so the writer
  tool, parser, and validator all reference the same tuple.
- Sub-dataclasses for sub-sections (``Summary``, ``Entry``,
  ``Classification``) — don't flatten.

Example from the assessment remolding:

```python
@dataclass(frozen=True, slots=True)
class Finding:
    number: int
    title: str
    severity: FindingSeverity  # Literal["critical", "major", "minor"]
    source_tool: str = ""
    # ...
```

### 2. Render

A pure function ``render_<artifact>(form) -> str`` that produces
canonical markdown from the dataclass.

Rules:
- **No ``if llm_might_do_X`` branches.** This function produces one
  shape only.
- Section headers, field prefixes (``**Severity:**``), key-value
  lines — all literal strings in this function.
- Optional sections render iff the corresponding field is populated.
- End with trailing ``\n``; intermediate sections separated by a
  blank line.
- Deterministic: same input → byte-identical output.

### 3. Parse

A tolerant function ``parse_<artifact>(text) -> <Artifact>Form``.

Rules:
- **Tolerant of LLM drift.** Accept the canonical structure AND
  common drifted variants. If the brutalist will ever write
  ``## Phase: X`` subsections, the parser must recognize those too.
- **Missing fields default.** Don't raise on structural gaps; fill
  with sensible defaults. The validator flags problematic defaults.
- Round-trip property: ``render(parse(canonical_text)) ==
  canonical_text`` (up to trailing whitespace).
- ``render(parse(drifted_text))`` produces canonical form — this is
  how legacy drifted artifacts get repaired on the first MCP-mediated
  write.

Separator-tolerance trick from the assessment parser: accept colon,
em-dash (U+2014), en-dash (U+2013), or hyphen after the ``# Title``
header, because real LLM output used all four.

### 4. MCP writer tool

Register in ``clou/coordinator_tools.py`` as ``clou_write_<artifact>``
(initial write) and, if the artifact is amended across turns,
``clou_append_<sub>`` for amendments.

Schema for the tool parameters mirrors the dataclass shape. Tool
handler:

1. Coerce JSON input (the SDK advertises arrays as strings sometimes;
   use ``_coerce_json_array`` / ``_coerce_json_object``).
2. Validate enum values against the exported enum sets — reject early
   with a structured error.
3. Build the dataclass.
4. For initial-write tools: ``render`` and write.
5. For amendment tools: ``parse`` existing file, merge new input,
   ``render`` and write. Merge strategy depends on artifact (e.g.,
   last-writer-wins per finding_number for classifications).
6. Return ``{written, <count>, <status>}`` so the caller can verify.

Side effect: emit telemetry (``<artifact>.written`` or similar)
carrying at least ``milestone`` and count fields.

### 5. Hook boundary

In ``clou/hooks.py`` ``WRITE_PERMISSIONS``, and in every harness
template's ``write_permissions``:

- Remove the artifact path from any tier whose writes should go
  through the MCP tool instead.
- Add the MCP tool name to the corresponding tier's ``tools`` list
  in ``AgentSpec.tools`` (and the inline fallback in
  ``clou/harness.py``).
- Run the ``test_software_template_tools_match_orchestrator`` drift
  canary — it keeps the fallback and template synchronized.

The tier retains access to ``Read`` and other tools; only the direct
``Write`` path to the validated artifact is denied. The MCP writer is
Python-in-process and bypasses the hook by design.

Add a hook-deny test: direct Write to ``milestones/*/<artifact>.md``
from the affected tier produces ``permissionDecision: "deny"``.

### 6. Validator refactor

Update ``_validate_<artifact>`` in ``clou/validation.py``:

- Call ``parse_<artifact>`` instead of running regex on raw text.
- Check the parsed dataclass's fields.
- **Structural errors still gate** (missing required sections,
  invalid enum values) — these reflect true structural defects.
- **Drifted-but-parseable inputs emit WARNING**, not ERROR. Include
  a message nudging the operator toward canonical re-render via the
  MCP tool.
- Per-field body-hygiene checks (missing ``**Severity:**`` inside a
  finding) remain regex-based on raw text, because the parser
  tolerates them via defaults and the dataclass alone can't tell
  "user wrote severity=minor" from "parser defaulted to minor".

### 7. Prompt update

In the prompt that tells the LLM to produce the artifact:

- Remove the static markdown template block entirely. It was the
  source of drift.
- Instruct the LLM to call the MCP tool with structured input. Show
  one concrete invocation example.
- Name the scope where the tool applies (e.g., "use this for
  multi-phase cycles too; the tool handles structure").
- Do not include content that tells the LLM how to organize sections.
  That's now code's job.

Mirror any project-local ``.clou/prompts/`` copy to the bundled
``clou/_prompts/`` original so developers reading the project doc
don't see stale guidance.

## Tests required per remolding

1. **Round-trip test.** ``render(known_form) → parse → render`` is
   byte-stable; ``parse(drifted_example) → render`` produces
   canonical form.
2. **MCP tool tests.** Happy path (canonical write). Invalid status
   rejection. Invalid severity/kind rejection. Stringified JSON array
   acceptance (SDK schema-fallback case). Amendment mode if
   applicable. Last-writer-wins if applicable.
3. **Hook boundary tests.** Direct Write to the artifact path from
   the now-restricted tier is denied. Decisions.md / other
   freeform-adjacent paths still allowed for the same tier (no
   over-tightening).
4. **Validator tests.** Canonical form passes with zero errors and
   zero warnings. Drifted form passes with one drift warning.
   Structurally empty form (no findings / no cycle sections)
   errors.
5. **Integration test.** End-to-end: LLM-tier-A calls writer →
   optional LLM-tier-B calls amendment → validator runs → validator
   reports zero errors and the canonical ``## Findings`` (or
   equivalent) section is present with no drifted section names.

## Hand-patch protocol (for stuck milestones)

When an existing project's artifact is already drifted and blocking
a cycle:

1. Read the drifted file.
2. ``parse_<artifact>`` it — tolerant parser extracts structured data.
3. Construct a corrected ``<Artifact>Form`` with re-derived counts /
   fields from the parsed data if the drifted summary is wrong.
4. ``render_<artifact>`` → write back.
5. Run the validator — confirm zero errors.
6. Resolve any escalation's disposition field to ``resolved`` with an
   explanation of the remolding that unblocked it.

Done in ``<20 minutes`` once steps 1–4 are available from the
remolding.

## When to apply

**Apply the remolding if:**

- The artifact has a validator that checks structure.
- The validator has errored or warned on real LLM output in the wild.
- Multiple LLM tiers write to the artifact with different roles.
- Scope will plausibly stretch (new categories, new sub-entities,
  multi-phase cycles).

**Do not apply (leave freeform) if:**

- The artifact is narrative prose with no structural validation
  (``decisions.md`` coordinator-narrative sections, milestone.md
  vision text).
- The artifact is written by exactly one tier and the validator
  only warns, never errors — drift has no cycle-blocking impact.

## Applied to

| Artifact | Drift bug | Remolding refs |
|---|---|---|
| worker execution paths | slug-drift | ``clou/shard.py``, ``clou/coordinator_tools.py::clou_brief_worker``, ``clou/hooks.py`` WRITE_PERMISSIONS worker scope |
| supervisor cleanup | orphan artifacts accumulate | ``clou/orchestrator.py::clou_remove_artifact``, ``clou/hooks.py`` SUPERVISOR_CLEANUP_SCOPE |
| assessment.md | ``## Phase: X`` drift | ``clou/assessment.py``, ``clou/coordinator_tools.py::clou_write_assessment`` / ``clou_append_classifications``, ``clou/validation.py::_validate_assessment`` |

## Pending application

| Artifact | Expected drift | Estimated work |
|---|---|---|
| decisions.md (evaluator classifications) | ad-hoc ``### <Kind>:`` naming (valid/noise/architectural/security vs validator's Accepted/Overridden/Tradeoff) | smaller than assessment — classifications-only, keep coordinator narrative freeform |
| handoff.md | structure drift at milestone boundary | full vertical slice, ~same as assessment |
| intents.md | ``ArtifactForm`` validation already exists; integrate into schema-first | full vertical slice |

## Anti-patterns

**Don't validate via regex on raw text for any field the writer
controls.** That's the translation gap that drifts.

**Don't put structural guidance in prompts.** The prompt is for
telling the LLM what content to produce, not what shape. Shape is
code's job.

**Don't grant direct ``Write`` permission "for flexibility" while
also enforcing structure.** If you enforce structure, enforce the
writer.

**Don't skip the tolerant parser.** Without it, you can't migrate
legacy projects. Without migration, adoption stalls and drift
accumulates in parallel with the new path.

**Don't require hand-patching to be interactive.** The parse-render
round-trip is the hand-patch. A one-liner Python script resolves a
stuck milestone.
