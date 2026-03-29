# DB-14: Intent-Specification Separation

**Status:** DECIDED
**Decided:** 2026-03-28
**Severity:** High — when intent collapses into specification, the entire downstream hierarchy builds the wrong thing correctly
**Question:** How do we prevent observable user intent from collapsing into implementation specification in the golden context?

## Decision

**Split the intent layer from the specification layer: introduce `intents.md` (observable outcomes the user experiences) alongside the existing `requirements.md` (implementation constraints the coordinator plans against). Make artifact forms a first-class concept in the harness template via `ArtifactForm`.**

This decision is grounded in Hutchins' distributed cognition (cognitive artifacts reorganize cognition through structure, not content), DB-07's incommensurability principle (when two concerns share a container, the more concrete displaces the more abstract), and the fixation research (every framing in a specification artifact anchors all downstream cognition).

## The Research-Grounded Argument

### The Failure That Exposed the Gap

Over eight milestones, the supervisor wrote implementation specifications in requirements.md: "TaskGraphWidget with keyboard nav, drill-down, expansion" instead of "user sees live agent progress during breath mode." The coordinator decomposed these faithfully — clean task graph, working components, all tests passing. But the components weren't integrated into the running system, because the requirements described artifacts, not observable outcomes. VERIFY couldn't catch this because it verified against requirements that could be satisfied by file inspection alone.

This is not a discipline failure. Supervisor.md line 66 already says: "'User can add a book to their reading list' not 'CRUD operations work.'" The guidance existed. It didn't hold. Discipline doesn't hold. Structure holds.

### Why requirements.md Cannot Hold Both Concerns

DB-07 established: incommensurable concerns need separate containers. When specification and state shared `milestone.md`, the spec (more concrete, denser, more relevant-seeming) dominated the coordinator's attention during EXECUTE cycles where only state mattered. The fix was splitting into separate files at the file-granularity boundary that the per-cycle read set operates on.

The same dynamic applies one level up. Intent ("user sees live agent progress") and specification ("TaskGraphWidget with keyboard nav") are incommensurable:

| | Intent | Specification |
|---|---|---|
| **Level of abstraction** | Observable outcome | Implementation constraint |
| **Verifiable by** | Running the system, observing | Reading files, checking structure |
| **Consumed by** | Verifier (golden paths), Coordinator (decomposition orientation) | Coordinator (PLAN decomposition, ASSESS evaluation) |
| **Update frequency** | Set once by supervisor | May refine during PLAN |
| **Failure when wrong** | Wrong thing built correctly | Right thing built with wrong constraints |

When both share requirements.md, specification wins — it's more concrete, more actionable, more satisfying to write. The artifact's name ("requirements") itself anchors cognition toward specification. The supervisor writes implementation plans and calls them requirements without noticing the contradiction with its own protocol.

### Fixation Risk at the Specification Boundary

CHI 2024 fixation research (DB-13): AI-generated framings become cognitive anchors. Every word the supervisor writes in requirements.md is a framing that anchors the entire downstream hierarchy. "TaskGraphWidget" anchors the coordinator on a component. The coordinator decomposes widget construction, the assessor evaluates widget quality, the verifier checks widget behavior — but nobody checks whether the widget serves the intent, because the intent was replaced by the artifact name in the specification itself.

Behavioral language ("user sees live agent progress") resists this because it doesn't name an artifact. The coordinator must invent the artifact during PLAN — which is exactly where decomposition belongs. The intent shapes the plan without constraining it to a specific implementation.

### Artifact Form as Cognitive Affordance

Hutchins (1995): cognitive artifacts reorganize cognition through their structure, not their content. The golden context has three artifacts with structural affordances:

- **compose.py** — AST validation (five structural guarantees). You literally cannot write an invalid decomposition.
- **status.md** — Checkpoint schema (required keys, enum values). The form elicits progress tracking.
- **understanding.md** — Section structure (What this is becoming / Active tensions / Continuity / Resolved). The form elicits conceptual thinking.

requirements.md has no structural affordance. It accepts whatever the supervisor's current cognitive habit produces. After eight milestones of momentum, that habit was decomposition leaking upward.

intents.md gets a form: each criterion follows the template `"When {trigger}, {observable_outcome}."` If a criterion can be verified by file inspection rather than system observation, it belongs in requirements.md or compose.py, not intents.md. The form makes the right kind of thinking (behavioral specification) natural and the wrong kind (implementation specification) feel out of place.

### Domain Generality via ArtifactForm

The principle — every mediating artifact needs a form that shapes cognition through it — is domain-invariant. Making artifact forms a first-class concept in the harness template means new domains define their cognitive architecture alongside their agent capabilities:

```python
@dataclass(frozen=True, slots=True)
class ArtifactForm:
    """Cognitive affordance for a golden context artifact.

    Not a schema — a generative constraint. The form makes the right
    kind of thinking natural and the wrong kind feel out of place.
    """
    sections: tuple[str, ...] = ()
    criterion_template: str | None = None
    anti_patterns: tuple[str, ...] = ()
```

A software-construction harness defines intents as "When [trigger], [observable outcome]." A research harness might define them as "Reader observes [outcome] when [condition]." The form changes. The principle — artifact form shapes cognition — doesn't.

## Decisions

### D1: Introduce intents.md

New golden context artifact: `.clou/milestones/<name>/intents.md`

**Purpose:** Observable outcomes — what a person standing outside the system sees when the milestone succeeds.

**Written by:** Supervisor (during crystallization, alongside milestone.md and requirements.md)
**Read by:** Coordinator (PLAN — orients decomposition toward observable outcomes), Verifier (VERIFY — walks golden paths against these)

**Form:** Each criterion follows the template:
```
When [trigger], [observable outcome].
```

**Anti-patterns (indicate wrong-level content):**
- File paths or module names as criterion subjects
- Implementation verbs (extract, refactor, build, implement) as criterion actions
- Criteria verifiable by file inspection alone

### D2: requirements.md Retains Specification Role

requirements.md remains. Its purpose narrows to implementation constraints:
- Functional requirements (what the system must do, expressed in product terms)
- Non-functional requirements (performance, accessibility, security)
- Integration requirements (services, APIs, protocols)
- Constraints (tech stack, patterns, conventions)

The coordinator reads requirements.md during PLAN (for constraints) and ASSESS (for evaluation against constraints). The verifier does NOT read requirements.md — it reads intents.md.

### D3: ArtifactForm as First-Class Harness Concept

Add `ArtifactForm` dataclass to `harness.py`. Add `artifact_forms: dict[str, ArtifactForm]` to `HarnessTemplate`. Each harness defines the cognitive affordance for its intent-layer artifact.

This makes the harness template a complete cognitive architecture specification — both what agents can do (capabilities) and what artifacts shape their thinking (forms).

### D4: Updated Per-Cycle Read Sets

```
PLAN:    milestone.md, requirements.md, intents.md, project.md
EXECUTE: status.md, compose.py, phase.md, active/coordinator.md
ASSESS:  status.md, compose.py, execution.md, requirements.md, decisions.md, assessment.md, active/coordinator.md
VERIFY:  status.md, intents.md, compose.py, active/coordinator.md
EXIT:    status.md, handoff.md, decisions.md, active/coordinator.md
```

Key changes:
- **PLAN** gains `intents.md` — decomposition oriented by observable outcomes
- **VERIFY** reads `intents.md` instead of `requirements.md` — verification grounded in observable outcomes, not implementation constraints
- **ASSESS** retains `requirements.md` (implementation constraints relevant to assessment) but drops the intent concern

### D5: intents.md Validation (Narrative Tier)

intents.md is narrative-tier (DB-12 D3). Form-only validation:
- Required: at least one criterion present
- Warning: criterion doesn't match `When ... , ...` template
- Warning: implementation artifact names detected (file paths, class names)
- Content quality assessed by quality gate, not orchestrator

### D6: Supervisor Prompt Updated

Supervisor.md crystallization step includes intents.md with form guidance:
```
- .clou/milestones/{name}/intents.md — observable outcomes only.
  Each criterion: "When [trigger], [observable outcome]."
  NOT implementation artifacts. NOT file structure. What a person
  standing outside the system sees when this milestone succeeds.
```

## Relationship to Existing Decisions

### Extends DB-07: Incommensurability Principle

DB-07 split milestone.md from status.md because specification and state are incommensurable. DB-14 applies the same principle one level up: intent and specification are incommensurable. The split follows the same logic — per-cycle read sets operate at file granularity, so incommensurable concerns need separate files.

### Extends DB-11: Harness Architecture

DB-11 defined the harness template as a capability profile (agents, tools, permissions, quality gates). DB-14 extends it to a cognitive architecture profile by adding artifact forms. This makes the template the single specification for a distributed cognitive system — both actors and artifacts.

### Extends DB-12: Validation Tiers

DB-12 defined three tiers (structural, checkpoint, narrative) but did not cover requirements.md or intents.md. DB-14 places intents.md in the narrative tier with form-only validation, consistent with D3's principle that content quality is the quality gate's responsibility.

### Consistent with DB-13: Cognitive Artifact Principle

DB-13 established understanding.md as a cognitive artifact whose form (sections) shapes the supervisor's comprehension. DB-14 applies the same principle to the intent layer: intents.md's form (criterion template) shapes the supervisor's crystallization. Both are Hutchins-grounded: the artifact's structure reorganizes cognition, not its content.

## Cascading Effects

| DB | Effect |
|---|---|
| **DB-03 (Context Lifecycle)** | intents.md is golden context. Follows compaction principle. Added to supervisor read set and coordinator PLAN read set. |
| **DB-04 (Prompt Architecture)** | Coordinator PLAN prompt gains intents.md in read set. VERIFY prompt reads intents.md instead of requirements.md. Supervisor prompt updated for crystallization. |
| **DB-07 (Milestone Ownership)** | File tree gains intents.md. Ownership: supervisor writes. Per-cycle read sets updated per D4. |
| **DB-08 (File Schemas)** | intents.md schema defined: criterion template, anti-patterns, narrative tier. |
| **DB-09 (Verification)** | Verifier reads intents.md for golden path criteria. Behavioral criteria ensure verification requires running the system. |
| **DB-11 (Harness Architecture)** | HarnessTemplate gains `artifact_forms` field. ArtifactForm dataclass defined. |
| **DB-12 (Validation Tiers)** | intents.md classified as narrative tier. Form validation added (criterion template, anti-pattern warnings). |

## Implementation Scope

| Component | Change |
|---|---|
| `harness.py` | Add `ArtifactForm` dataclass. Add `artifact_forms` to `HarnessTemplate`. |
| `harnesses/software_construction.py` | Define intents form in template. Add `intents.md` to supervisor write permissions. |
| `tools.py` | `clou_create_milestone()` gains `intents_content` parameter, creates `intents.md`. |
| `recovery.py` | `determine_next_cycle()` adds `intents.md` to PLAN read set, replaces `requirements.md` with `intents.md` in VERIFY read set. |
| `hooks.py` | `WRITE_PERMISSIONS["supervisor"]` gains `milestones/*/intents.md`. |
| `validation.py` | Add `_validate_intents()` for narrative-tier form checking. |
| `_prompts/supervisor.md` | Crystallization step includes intents.md with form guidance. |
| `_prompts/coordinator-plan.md` | Read intents.md for observable outcomes alongside requirements.md. |
| `_prompts/coordinator-verify.md` | Read intents.md instead of requirements.md for acceptance criteria. |
| `_prompts/verifier.md` | Read intents.md for golden path criteria. |
| `knowledge-base/golden-context.md` | File tree, file purposes, ownership table, read sets updated. |
| Tests | Read set assertions, tool output assertions, validation tests updated. |
