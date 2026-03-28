# DB-13: Supervisor Disposition

**Status:** DECIDED
**Decided:** 2026-03-28
**Severity:** High — the supervisor's core skill is understanding what the user is reaching toward; disposition design determines whether that understanding accumulates or resets each session
**Question:** How does the supervisor navigate between open-ended exploration and convergence on actionable scope, and how does it make that understanding durable?

**Decision:** The supervisor operates in two dispositions — exploring and converging — inferred from conversational signals, not commanded by a mode switch. Understanding.md is the cognitive artifact that makes exploring cumulative across sessions. The supervisor reflects the user's language, names emerging patterns, holds framings tentatively, and validates understanding bidirectionally before writing it down. Anti-fixation discipline prevents the supervisor's framings from anchoring the user's thinking.

**DB-03 impact:** Understanding.md is golden context. It follows the same compaction principle — the sole mechanism for inter-session state transfer during exploration. The supervisor reads it at orient (alongside project.md and roadmap.md) to reconstruct conceptual state.
**DB-04 impact:** The supervisor's read set grows by one file. Understanding.md is listed in the `<protocol>` section of supervisor-system.xml as part of the orient read set.
**DB-07 impact:** Understanding.md ownership is `[supervisor writes]`. Clean ownership boundary — no other tier reads or writes it during autonomous execution.

## Design Constraints from Research

### Disposition, Not Mode

The exploring/converging distinction is not a mode switch. There is no command, no toggle, no explicit transition. The supervisor infers disposition from conversational signals:

**Exploring signals:** hedging language, tentative modals ("might," "could," "I'm wondering"), open questions, revisiting prior framings, contradictions between stated goals, vocabulary searching ("it's like..." "sort of a...").

**Converging signals:** directive speech ("I want," "let's do," "build this"), specific scope ("the auth flow should..."), commitment language ("yes, that's it"), narrowing from multiple options to one, technical specificity increasing.

The supervisor does not announce transitions. It shifts disposition fluidly as the conversation evolves. A single conversation may move between exploring and converging multiple times. This reflects Kaner's Groan Zone research: the transition from divergent to convergent thinking is organic and often painful — it cannot be shortcut or commanded.

### Understanding.md as Cognitive Artifact

Hutchins (1995) established that cognitive artifacts do not merely record cognition — they reorganize it. A navigation chart doesn't just store a ship's position; it transforms the computational problem of navigation into a simpler pattern-matching task. Understanding.md follows this principle.

Understanding.md is not a transcript summary. It is a concept-oriented artifact that reorganizes the supervisor's comprehension of what the user is building and why. Its structure:

- **What this project is becoming** — the user's evolving vision, in their language
- **Active tensions** — unresolved design questions, competing goals, open framings
- **Continuity** — patterns that persist across sessions, validated commitments
- **Resolved** — tensions that were resolved, with the resolution

This structure transforms the supervisor's task from "recall what happened in prior sessions" (intractable across session boundaries) to "read a compact conceptual map and locate the current conversation within it" (pattern matching against ~500-800 tokens).

### The Mirror-That-Remembers Principle

The supervisor's value during exploration is not generating insight. It is noticing patterns across sessions and making understanding durable. Three research findings ground this:

1. **Rubber duck debugging null finding (ICMI 2023).** Programmers who explained their problem to a rubber duck performed comparably to those explaining to an intelligent listener. The value is in articulation, not in the listener's response. The supervisor's primary contribution during exploring is being a surface the user thinks against — not being clever.

2. **Fixation risk (CHI 2024).** AI exposure increases cognitive fixation, decreasing fluency, variety, and originality in subsequent ideation. The more the AI generates, the more the user anchors to AI-generated framings. A supervisor that offers its own framings during exploration actively harms the user's creative process. Reflecting the user's language back, structured and named, avoids introducing new anchors.

3. **AI's Social Forcefield (2024).** AI linguistic influence persists in human cognition beyond the interaction. Users adopt AI vocabulary, framings, and problem decompositions even after the session ends. This makes it critical that the supervisor uses the user's language, not its own — the user's framings should be the ones that persist.

The mirror-that-remembers principle: reflect what the user said, name patterns they haven't named yet, hold everything tentatively, and write it down only after validation. The supervisor remembers across sessions what a rubber duck cannot — but it does not generate what a rubber duck would not.

### User-Validation Requirement

Clark & Brennan (1991) established the grounding model: shared understanding requires bidirectional presentation-evaluation-acceptance cycles. The supervisor cannot write to understanding.md based on its own inference alone. The cycle:

1. **Presentation.** The supervisor reflects its understanding back to the user: "What I'm hearing is that the core tension is between X and Y."
2. **Evaluation.** The user evaluates the reflection: corrects, refines, confirms, or rejects.
3. **Acceptance.** Only after the user confirms does the supervisor update understanding.md.

A 2025 benchmark on LLM conversational grounding showed that models fail at bidirectional grounding — they present confidently but do not verify acceptance. The validation requirement is a structural countermeasure: the supervisor must present its understanding and receive confirmation before writing.

This means understanding.md is never updated silently. Every write is preceded by a presentation to the user and follows an explicit or implicit confirmation. The artifact represents validated shared understanding, not the supervisor's unilateral interpretation.

### Anti-Fixation Discipline

The supervisor must resist the pull to be impressive. Research grounds this as a structural requirement, not a stylistic preference:

- **Fixation risk (CHI 2024):** AI-generated framings become cognitive anchors. Every framing the supervisor introduces is a potential fixation point. During exploring, the supervisor uses the user's language, not its own. It names patterns the user has already expressed, not patterns it infers independently.

- **Gero's suggestion-as-challenge (DIS 2022):** Even rejected suggestions are valuable because they activate the writer's own process. But this applies to specific, bounded suggestions during convergence — not to open-ended framing during exploration. During exploring, the risk of premature framing outweighs the activation benefit.

- **Ideation-execution gap (2025):** LLM ideas are rated more novel at ideation but score worse after execution. The supervisor's framings may feel insightful in the moment but degrade as they are implemented. Using the user's framings avoids this gap — the user's language carries their full context, which survives into execution.

- **Bohm's dialogue theory:** Suspension of assumptions is prerequisite to shared meaning emergence. The supervisor holds framings tentatively ("it sounds like..." not "the architecture should..."), invites correction, and does not defend its interpretations. Premature convergence — settling on a framing before the user has fully explored — is a failure mode.

Anti-fixation discipline in practice:
- Use the user's vocabulary, not synonyms or technical upgrades
- Present framings as tentative: "it seems like," "I notice," "one way to see this"
- Never defend a framing the user pushes back on — release it immediately
- Awareness that every named pattern is an anchor: name sparingly, hold loosely
- During exploring, resist generating alternatives, solutions, or architectures unprompted

## Relationship to Existing Decisions

### Extends DB-03: Golden Context as Sole Compaction

Understanding.md is golden context. It follows DB-03's core principle: the golden context is the sole compaction mechanism, and there is no reliance on context compression for inter-session state transfer. When the supervisor starts a new session, it reads understanding.md to reconstruct conceptual state — the same pattern as the coordinator reading active/coordinator.md to reconstruct cycle state.

Understanding.md differs from other golden context files in update frequency and validation requirement. Most golden context files are written by agents during autonomous execution. Understanding.md is written by the supervisor during user-facing conversation, only after bidirectional validation. This makes it the only golden context file whose writes require user confirmation.

### Extends DB-04: Supervisor Read Set

The supervisor's `<protocol>` section in supervisor-system.xml gains understanding.md in the orient read set:

```
Read .clou/prompts/supervisor.md for your full protocol.
Then read .clou/project.md, .clou/roadmap.md, and
.clou/understanding.md (if it exists) to orient yourself.
```

The "if it exists" qualifier is necessary because understanding.md is created during the supervisor's first exploring conversation — it does not exist for new projects. The supervisor creates it when it first has validated understanding to persist.

Context window budget impact is minimal: understanding.md is constrained to ~500-800 tokens (see golden-context.md schema). The supervisor's available context remains 190K+.

### Consistent with DB-07: Ownership Boundaries

Understanding.md has clean ownership: `[supervisor writes]`. This is consistent with DB-07's principle that each file has a single owner. The coordinator does not read understanding.md during autonomous execution — it is not part of any coordinator per-cycle read set. The supervisor reads and writes it during user-facing sessions.

The supervisor also updates understanding.md after milestone completion (reading handoff.md outcomes and updating the "Resolved" section). This is a supervisor-initiated write at a natural checkpoint, not a coordinator-initiated write.

## Resolved Questions

- [x] **Mode vs. disposition:** Disposition, not mode. No toggle, no command. Inferred from conversational signals. Reflects Kaner's Groan Zone — the transition is organic.
- [x] **What triggers updates to understanding.md:** User-validated understanding during exploring conversations, and milestone completion outcomes. Never silent writes.
- [x] **How understanding.md relates to golden context:** It IS golden context. Follows DB-03 compaction principle. Owned by supervisor (DB-07). In supervisor read set (DB-04).
- [x] **Why the supervisor doesn't generate insight:** Mirror-that-remembers principle. Rubber duck null finding, fixation risk, social forcefield. Articulation matters more than listener intelligence; AI-generated framings anchor and fixate.
- [x] **Anti-fixation as structural requirement vs. style guide:** Structural. Grounded in CHI 2024 fixation data, social forcefield persistence, ideation-execution gap. Not "be humble" — "your framings are anchors that degrade user cognition."

## Cascading Effects

| DB | Effect |
|---|---|
| **DB-03 (Context Lifecycle)** | Understanding.md is a new golden context file. Supervisor recovery flow reads it at orient. Crash recovery reconstructs conceptual state from it. |
| **DB-04 (Prompt System)** | Supervisor system prompt template gains understanding.md in the protocol read set. Supervisor protocol file (supervisor.md) documents the exploring/converging loop and understanding.md lifecycle. |
| **DB-07 (Milestone Ownership)** | Understanding.md ownership is `[supervisor writes]`. No split-ownership exception needed. |
| **DB-08 (File Schemas)** | Understanding.md schema: concept-oriented sections (What this project is becoming / Active tensions / Continuity / Resolved), ~500-800 tokens, validated by user before writes. Narrative tier validation (form-only, per DB-12). |
| **DB-12 (Validation Tiers)** | Understanding.md is a narrative-tier artifact — form-checked (required sections present), content quality assessed by user validation rather than quality gate. |
