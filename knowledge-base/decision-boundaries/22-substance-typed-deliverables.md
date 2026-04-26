# DB-22: Substance-Typed Phase Deliverables

## Decision

**Phase-completion contracts are typed by what the artifact
substantively contains, not by where the artifact lives.**  Each
phase declares an `ArtifactType` (registered in
`clou/artifacts.py::ARTIFACT_REGISTRY`); the engine's
phase-acceptance gate (`clou/phase_acceptance.py`) parses typed
artifact blocks out of `execution.md`, validates them against the
type's schema, and produces an `Advance` or `GateDeadlock` verdict.
The verdict is persisted in the checkpoint envelope's
`last_acceptance_verdict` field, and `clou_write_checkpoint`
refuses any `phases_completed` advance not authorised by an
`Advance` verdict for the right phase.

Filenames continue to exist (every phase has an `execution.md`) but
they are not the contract — the contract is the artifact type and
its schema.

## Context

M51 (orient-cycle gating) deadlocked on a filename premise.  The
supervisor LLM was looking for `phases/p1/spec.md` to declare the
phase complete; the worker tier was permitted to write only
`execution.md`.  Two incompatible mental models — supervisor "the
spec lives at this path" vs worker "I write here" — collided across
a tool-permission boundary that neither side could cross.  Six
ASSESS cycles produced 1193 lines of substantive judgment-layer
content inside `execution.md`; the supervisor never saw it because
the file it was inspecting did not exist.

The deadlock is the same drift class as DB-21 (canonical structure
lived in two places: prompt text vs code, drifted), but on a
different axis: canonical *location* lived in two places — the
phase-acceptance check (filename string in code) vs the worker tier
(write authority on a different filename).  The third place that
should have mediated — substance — was nowhere.

## Forces

- **Worker tier writes only `execution.md`.**  This is a load-bearing
  invariant from M49a — the worker hooks deny `Write` to any other
  file in the phase directory, because cross-file edits during EXECUTE
  produce co-layer interference and break compose.py-driven dispatch.

- **Supervisors and assessors read whatever the prompt names.**  Prompts
  evolved from "look at execution.md" → "look at the deliverable file"
  → "look at the typed artifact in execution.md."  Each prompt change
  was a small behavioural drift; aggregated, they produced the M51
  filename premise.

- **The completion contract must be enforceable by code.**  An LLM
  judging "is this phase done?" introduces M50/M51-class
  anti-convergence: same-content rounds get different verdicts based
  on prompt drift / model variance.  The verdict needs to come from a
  pure function over bytes, not from a prompted judgment.

- **Migration must be incremental.**  Pre-M52 milestones in flight
  cannot all be retroactively re-typed; the deployment must allow
  legacy phase.md files to bootstrap-advance once via a migration
  shim, then enforce strict typing thereafter.

## Why this resolves the conflict

Substance-typing replaces "filename matches" with "artifact-type
schema matches."  The single source of truth becomes the
`ArtifactType` registry: every consumer (gate, supervisor, ASSESS
prompt) routes through it.

- The worker still writes only `execution.md` — no tier-permission
  change.
- The phase-acceptance gate parses typed artifact blocks out of
  `execution.md` and validates against the registered schema.  No
  filename strings in the gate code path
  (`tests/test_no_filename_completion.py` enforces this with an AST
  check scoped to `clou/phase_acceptance.py`).
- The verdict is a pure function over bytes; same content → same
  verdict.  M50/M51-class anti-convergence is structurally
  impossible: the gate cannot disagree with itself.
- `clou_write_checkpoint` enforces single-phase-increment +
  `Advance`-verdict + matching-phase contract.  The LLM cannot
  self-judge past a deadlocked phase; cross-phase off-by-one is
  refused.

The verdict carries `(phase, decision, content_sha)`.  The
content_sha ties it to a specific `execution.md` body, so re-emitting
during rework requires a fresh gate evaluation — a stale verdict
cannot authorise an advance against a freshly-written body.

## Anti-pattern

Writing a milestone requirement that names a specific filename as
the deliverable: "phase produces `spec.md`", "phase ends when
`output.md` exists", "the verifier reads `summary.md` to decide
acceptance."  These are M51's deadlock class.  The engine cannot
enforce filename-coupled contracts except by counting bytes; under
worker-tier permission constraints, the supervisor's filename
premise routinely outruns what the worker can produce.

Express requirements in terms of substance: what sections, what
fields, what shape the artifact contains.  The coordinator's PLAN
cycle picks an `ArtifactType` (registering a new one if necessary)
that captures those substance requirements and writes the typed
declaration into `phase.md`'s `## Deliverable\ntype: <name>`
section.

## Implementation

- `clou/artifacts.py` — registry, parser, validators,
  `parse_phase_deliverable_type`, `lint_phase_md`.
- `clou/phase_acceptance.py` — pure-function gate
  (`check_phase_acceptance`), result types (`Advance`,
  `GateDeadlock`), `GateDeadlockReason` enum.
- `clou/recovery_checkpoint.py` — `AcceptanceVerdict` dataclass +
  `last_acceptance_verdict` field on `Checkpoint`; wire-format
  parser.
- `clou/golden_context.py::render_checkpoint` — wire-format
  serialiser (`<phase>|<decision>|<content_sha>` or `none`).
- `clou/coordinator_tools.py::clou_write_checkpoint` — F33
  verdict-gate validation (single-increment + decision==Advance +
  phase==prev_cp.current_phase) and F40/F41 bootstrap / migration
  grace.
- `clou/coordinator.py::_run_phase_acceptance_gate` — engine-side
  caller invoked at the start of each ASSESS cycle.

## Test surface

- `tests/test_artifacts.py` — registry round-trip, canonicalisation,
  rejection classes, fenced-block grammar.
- `tests/test_phase_acceptance.py` — gate verdicts (six rejection
  reasons + Advance + idempotence) including the M51 real-content
  regression `test_m51_real_content_advances`.
- `tests/test_no_filename_completion.py` — AST property test pinning
  the gate's freedom from `*.md` literals.
- `tests/test_verdict_gate.py` — wire format + tool-side
  validation paths.
- `tests/test_engine_gate_integration.py` — engine helper
  (`_run_phase_acceptance_gate` + `parse_phase_deliverable_type`)
  with real fixtures.
- `tests/test_verdict_gate_integration.py` — end-to-end advance /
  refusal / rework / migration scenarios combining engine gate +
  tool validation.
- `tests/test_phase_md_linter.py` — phase.md linter rejecting
  unregistered types.

## Migration posture

Pre-M52 milestones in flight have phase.md files without typed
deliverables.  The engine's gate detects this (legacy phase.md →
no `## Deliverable\ntype:` line) and skips writing a verdict; the
LLM's first advance attempt sees `prev_cp.last_acceptance_verdict ==
None` and triggers the F41 bootstrap grace, allowing one advance
with a `migration.last_acceptance_verdict` telemetry event.
Subsequent advances either bootstrap again (the engine still has no
verdict to write) or hit strict gating once a phase migrates to
typed format.

M51's phase.md files are archived with explicit `ARCHIVED` markers;
M52 onward use typed deliverables natively.

## Related

- DB-21 (drift-class remolding pattern) — the schema-first lineage
  this decision extends.
- DB-17 (brutalist verification architecture) — the same single-
  source-of-truth principle applied to the brutalist gate.
- M52 milestone spec
  (`.clou/milestones/52-substance-and-capability/`) — the full
  architecture, including the round-by-round brutalist findings
  that produced it.
