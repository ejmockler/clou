# DB-09: Verification Generalization

**Status:** DECIDED
**Decided:** 2026-03-19
**Severity:** Medium — limits Clou to web applications without resolution
**Question:** How does the verification protocol adapt beyond web applications?

**Decision:** Verification modalities (composable, coordinator-selected) replace the project type taxonomy. The verifier captures raw perceptual artifacts that Brutalist reads during the coordinator's VERIFY evaluation (mediated perception). Verification decomposes into three perception stages (materialize, walk golden paths, explore) plus coordinator-level Brutalist experience assessment within the VERIFY cycle. Manual walk-through is the declared residual, not the starting point.

## Design Constraints from Research

- **§9 (LLM-Modulo):** LLMs can't self-verify. The verification agent and implementation agents are the same model family. Without external assessment, the verifier's "looks good" is structurally self-reflection. Brutalist experience assessment within VERIFY is the external verification layer.
- **§9 (Compositionality at 2+ hops):** User flows are multi-hop. Compositional reasoning degrades past hop 2. Flows must stay unified within a single verification task — decomposition at stage boundaries, not mid-flow.
- **§10 (Multi-Agent Failure Modes):** Premature termination — declaring success based on happy-path passing — is a documented failure mode (3 of 14). Exploratory testing and Brutalist experience assessment are structural countermeasures.
- **§10 (Blackboard Architecture):** Clou's golden context is a blackboard. Agents coordinate through structured artifacts, not shared live-environment interaction. Brutalist reads the verifier's perceptual record — same pattern as Brutalist reading code on disk.
- **§1 (Context Is Adversarial):** Distractor interference degrades performance. Accumulated context from golden path walking becomes distractor content during exploratory testing. Stage decomposition provides fresh context at intent transitions. Tools the verifier won't use add distractor surface — configure dynamically.
- **§7 (Decomposition):** Decomposition at meaningful boundaries improves accuracy. But decomposition that splits semantically unified reasoning hurts (§9). Decompose by intent (walk vs. explore), not by flow (registration vs. login).
- **§11 (Event Segmentation):** Each verification stage is a distinct cognitive episode. Stage transitions are natural event boundaries.

## Decisions

### D1: Verification Modalities, Not Project Types

The proposed `project_type` enum (`web | api | cli | library | pipeline | mobile | mixed`) is replaced by **composable verification modalities** — interaction methods the verification agent uses, selected by the coordinator during PLAN based on what the milestone builds.

Why modalities over types:

1. **Projects are multi-modal.** A Next.js app has web pages, API endpoints, and possibly a CLI. A fixed type forces the catch-all "mixed" which loses specificity.
2. **Milestones scope the modality set.** A "user authentication" milestone might need Browser + HTTP. The coordinator determines this during PLAN.
3. **The coordinator already has judgment.** DB-02 gives the coordinator `compose.py` where it writes the `verify()` function. The verification approach is part of the plan — no new metadata field needed.

#### Modality Table

| Modality | Tools | What the verifier perceives | Capture completeness |
|---|---|---|---|
| Browser | Chrome DevTools Protocol via MCP (a11y tree, screenshot, navigate, interact, console, network) | Pages, transitions, visual state, accessibility, loading behavior, error displays | Partial — loses temporal/responsive feel |
| HTTP | Bash (curl) + response parsing | Request/response cycles, error shapes, flow coherence, headers, status codes | Complete |
| Shell | Bash (commands, output capture) | Command output, help text, error messages, workflow coherence, exit codes | Complete |
| Code | Bash (run scripts, import checks) | API surface, return values, type errors, edge cases | Complete |

Modalities compose via `compose.py`. The coordinator encodes selected modalities in the verification phase's typed functions. The orchestrator dynamically configures the verifier's tools — Browser modality includes CDP MCP tools, all modalities include Bash.

#### Manual Walk-Through as Declared Residual

The verifier automates perception to the maximum extent each modality allows. Manual walk-through in `handoff.md` covers only what automation cannot reach. Each manual step documents WHY it couldn't be automated, creating pressure toward better tooling.

By modality:
- **Browser, HTTP, Shell, Code:** Manual section should be near-empty. Everything meaningful can be automated.
- **Mobile / hardware-dependent:** The verifier automates environment setup (build, simulator launch, app install via CLI), basic interaction where possible (accessibility-based navigation), and screenshot capture. Manual steps cover touch gestures, responsive feel, visual polish — things automation can't reach.

This extends Principle 3 ("The agent experiences the software before the user does"): the agent experiences as much as possible before asking the user to experience the rest. Automation is the default. Manual is the declared exception.

### D2: Mediated Perception for Brutalist

The verifier captures raw perceptual artifacts during path walking and exploratory testing. The coordinator passes these artifacts to Brutalist `roast_product` during its VERIFY evaluation. Brutalist does not independently interact with the live environment.

#### Why Mediated Perception

Even with independent browsing, Brutalist's agents would perceive through `browser_snapshot` (accessibility tree) and `browser_take_screenshot` — the same artifacts the verifier captures. Independent perception produces the same artifact types as mediated perception. The difference is **exploration breadth** (who decides where to look), not perception quality.

| Factor | Independent Perception | Mediated Perception |
|---|---|---|
| Artifact types produced | accessibility snapshots, screenshots, response bodies | Same |
| Token cost | 3-4x (three agents each browse independently) | 1x (verifier captures, Brutalist reads) |
| State mutation risk | Agents may modify state others encounter | None — environment accessed once |
| Architecture consistency | Only place multiple agents interact with live environment simultaneously | Consistent with blackboard pattern (§10) |

**Mediated perception is lossless for HTTP, Shell, and Code modalities** — request/response pairs, command output, and script results are complete interaction records. For Browser modality, the loss (temporal/responsive feel) is a fundamental limitation of LLM perception through any browser protocol — independent Brutalist perception wouldn't improve things.

#### The Perceptual Record

The verifier writes to `verification/execution.md` per the DB-08 schema. Verification also produces raw perception artifacts stored in `phases/verification/artifacts/`:

- Accessibility snapshots at state transitions (page loads, submissions, navigation)
- Screenshots at key visual moments
- HTTP response bodies (headers, status, body)
- Command output (stdout, stderr, exit code)
- The complete action sequence and narrative interpretation

`execution.md` references artifacts by path. This keeps the narrative readable while making raw captures available to Brutalist and the coordinator — consistent with how code lives on disk and Brutalist reads it directly.

#### The Gap Safeguard

If Brutalist identifies a gap in the perceptual record — "I cannot assess error handling quality because no error state snapshots were captured" — that's a finding. The coordinator dispatches additional verification targeting the gap. Same pattern as ASSESS rework: Brutalist flags an issue → coordinator acts. The verifier's exploratory testing stage (D3) is the primary mechanism for covering edges beyond prescribed paths.

#### Brutalist Experience Assessment Within VERIFY

The coordinator invokes Brutalist within the VERIFY cycle, mirroring how it invokes Brutalist within ASSESS:

| | ASSESS Cycle | VERIFY Cycle |
|---|---|---|
| What's assessed | Code quality | Experience quality |
| Evidence | Code on disk | Perceptual record (execution.md + artifacts/) |
| Brutalist tool | `roast_codebase`, `roast_architecture`, etc. | `roast_product` |
| Criteria | Code standards, project.md conventions | Acceptance criteria from requirements.md |
| Coordinator's role | Evaluate Brutalist feedback → accept/override/escalate | Same pattern |

The coordinator's judgment loop is the same shape across cycle types. Inputs differ. Evaluator tool differs. Criteria differ. The pattern is identical.

**Brutalist experience assessment is structural** — it always runs during VERIFY. The coordinator can scope exploratory testing, but cannot skip Brutalist. This parallels ASSESS: the coordinator decides implementation scope, but cannot skip Brutalist code assessment (DB-05). §9 (LLM-Modulo): without external verification, the system is self-assessing.

### D3: Verification Stage Structure

Verification decomposes into three perception stages in `compose.py`, following the same typed-function composition model as implementation phases (DB-02). The coordinator evaluates after perception stages complete, then dispatches handoff preparation.

#### Why Three Stages, Not Per-Flow

User flows are multi-hop and semantically unified (§9). Register → login → navigate → modify → verify is one story. Breaking it into separate tasks splits narrative coherence — the login task needs the registration task's output (created user, auth token), adding friction without proportionate benefit. §7 warns against decomposition that "splits semantically unified reasoning."

But stages have different intents:
- **Walk** — "confirm prescribed paths work" (guided, structured, the test plan)
- **Explore** — "try to break things" (adversarial, unstructured, informed by walk results)

Walk results inform exploration targets, but walk context (details of each successful flow) becomes distractor content during exploration (§1). The explorer needs to know what works, then try to break it. §11 (Event Segmentation): each stage is a distinct cognitive episode with clear boundaries.

#### The Composition

```python
async def verify_environment() -> EnvironmentStatus:
    """Materialize dev environment.
    Criteria: all services reachable, migrations applied, test data seeded"""

async def verify_golden_paths(env: EnvironmentStatus) -> PathResults:
    """Walk all prescribed user journeys against live environment.
    Capture accessibility snapshots, screenshots, response bodies at each
    state transition. Record complete action sequences.
    Criteria: all acceptance criteria flows complete with evidence captured"""

async def verify_exploratory(env: EnvironmentStatus, paths: PathResults) -> ExploratoryFindings:
    """Adversarial and qualitative exploration informed by golden path results.
    Try to break things: invalid inputs, unexpected navigation, boundary values.
    Notice qualitative issues: error message helpfulness, flow intuitiveness.
    Criteria: error states tested, edge cases probed, qualitative observations recorded"""

async def prepare_handoff(env: EnvironmentStatus, paths: PathResults,
                          exploration: ExploratoryFindings) -> Handoff:
    """Synthesize perceptual record into user-facing handoff artifact.
    Criteria: handoff.md complete with walk-through, environment docs, known limitations"""

async def verify():
    env = await verify_environment()
    paths = await verify_golden_paths(env)
    exploration = await verify_exploratory(env, paths)
    return await prepare_handoff(env, paths, exploration)
```

#### Coordinator Sequencing Within VERIFY

The coordinator controls sequencing within the VERIFY cycle:

```
1. Dispatch verify_environment → verify_golden_paths → verify_exploratory
2. Read perceptual record (verification/execution.md + artifacts/)
3. Invoke Brutalist roast_product on perceptual record
4. Evaluate verifier findings + Brutalist assessment against acceptance criteria
5. If issues:
   ├─ Code issue → rework EXECUTE targeting the problem, then re-VERIFY
   ├─ Perception gap → dispatch additional verification pass
   └─ Experience issue → rework EXECUTE, then re-VERIFY
6. If satisfied → dispatch prepare_handoff → EXIT
```

**Exploratory testing scope:** The coordinator determines during PLAN whether to include `verify_exploratory` and at what scope. Complex milestones (web apps with many interaction paths) include thorough exploration. Simple milestones (CLI tool with three commands) may scope it minimally. The coordinator's judgment — not a structural mandate — determines scope. Brutalist experience assessment, by contrast, is structural and always runs.

## Prior Resolutions

| Aspect | Resolution | Source |
|---|---|---|
| Three-stage structure (materialize → walk → handoff) | Universal across modalities | Verification protocol |
| Verification as a phase with `verify()` function | Part of typed-function composition | DB-02 |
| Session-per-cycle for VERIFY | Fresh session, golden context as state transfer | DB-03 |
| `execution.md` schema for verification results | Summary + Tasks in execution order | DB-08 |
| Environment materialization failure is blocking | Verification can't proceed without running environment | DB-05 |
| CDP MCP failure during verification | Verifier distinguishes environment vs. tool issues | DB-05 |
| 20-cycle milestone cap applies to verification rework | Same cap, same escalation path | DB-05 |

## Verifier Tool Configuration

The orchestrator dynamically configures the verifier's tools based on the coordinator's verification plan:

| Category | Tools | When |
|---|---|---|
| Always | Bash, Read, Write, Grep, Glob, LS, WebSearch, WebFetch | Every verification |
| Browser modality | CDP MCP tools (navigate, accessibility_snapshot, screenshot, click, type, evaluate_javascript, console_messages, network) | When coordinator's plan includes Browser modality |
| Coordinator-only | Brutalist MCP tools (roast_product) | Invoked by coordinator within VERIFY, not by verifier |

The orchestrator's `build_agent_definitions` parameterizes the verifier's tool list. §1: tools the verifier won't use are distractors.

## Resolved Questions

- [x] **Project type taxonomy:** Replaced by verification modalities — composable, coordinator-selected, no enum
- [x] **How type is determined:** Coordinator selects modalities during PLAN, encodes in compose.py and verification phase.md
- [x] **HTTP testing approach:** Bash + curl — already available, response bodies are complete representations
- [x] **Mobile strategy:** Automate to maximum extent, manual walk-through as declared residual with documented WHY
- [x] **Mixed-type coordination:** Modalities compose naturally in compose.py, no special mechanism
- [x] **Brutalist perception:** Mediated through verifier's raw artifacts — lossless for HTTP/Shell/Code, same-as-independent for Browser
- [x] **Verification stages:** Three perception stages (materialize, walk, explore) + coordinator-level Brutalist — decompose by intent, not by flow

## Cascading Effects

- **Verification protocol:** Updated with modalities, perception stages, exploratory testing, Brutalist experience assessment, perceptual record requirements, manual-as-residual.
- **Coordinator protocol:** VERIFY cycle includes Brutalist `roast_product` invocation, mirroring ASSESS pattern.
- **Brutalist integration:** `roast_product` invoked during VERIFY on perceptual record — structural, not optional.
- **Orchestrator:** Verifier tools dynamically configured by modality. `build_agent_definitions` parameterized.
- **Golden context:** Verification phase includes `artifacts/` directory for raw perception captures.
- **DB-10 (Team Communication):** The verifier's perceptual record format (execution.md + artifacts/) is a communication artifact between verifier and coordinator within VERIFY.
