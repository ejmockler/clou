# Verification Protocol

## Principle

The agent experiences the software before the user does. Verification is not test suites passing. It's the agent perceiving software through the user's lens — walking user flows against a live environment, trying to break things, noticing what a user would notice — and handing off a prepared room, not a construction site.

## Verification as Phase

Verification is a full phase — not a step, not a check. It has its own:
- `phase.md` — acceptance criteria translated into a test plan, with selected verification modalities
- Its `verify()` function in `compose.py` — defining perception stages, inputs, outputs, and criteria
- `execution.md` — the perceptual record from the verification agent
- `artifacts/` — raw perception captures (accessibility snapshots, screenshots, response bodies)

It runs after all implementation phases complete. It is always the final phase in every milestone.

## Verification Modalities

The verification protocol adapts to different software through composable **verification modalities** — interaction methods the verification agent uses, selected by the coordinator during PLAN based on what the milestone builds. Modalities replace a fixed project type taxonomy. See [DB-09](../decision-boundaries/09-verification-generalization.md) for the full decision.

| Modality | Tools | What the verifier perceives |
|---|---|---|
| Browser | Playwright MCP (snapshot, screenshot, navigate, interact, console, network) | Pages, transitions, visual state, accessibility |
| HTTP | Bash (curl) + response parsing | Request/response cycles, error shapes, flow coherence |
| Shell | Bash (commands, output capture) | Command output, help text, error messages, exit codes |
| Code | Bash (run scripts, import checks) | API surface, return values, type errors, edge cases |

Modalities compose — a milestone may use Browser + HTTP, or Shell + Code. The coordinator encodes selected modalities in the verification phase's typed functions in `compose.py`. The orchestrator dynamically configures the verifier's tools based on the plan.

## Perception Stages

### Stage 1: Environment Materialization

Before anything can be tested, the system needs to be running. The verification agent's first job is to bring up the actual development environment.

**What this means:**
- Start dev servers (the same way a developer would: `npm run dev`, `docker compose up`, etc.)
- Run database migrations
- Seed test data if needed
- Ensure services are reachable
- Verify third-party service connections (sandbox/test mode)

**What this does NOT mean:**
- No Docker mocks of your own services
- No in-memory database substitutes
- No test doubles for your own APIs
- No skipping migrations

**The principle:** Mock at the boundary of your control, never within it.

**The one exception:** External third-party services you don't control AND that provide no testing infrastructure. For those, the project maintains controlled response fixtures. But this is rare — most services have test modes.

**If environment materialization fails, that is a blocking finding.** The developer experience is broken. The coordinator loops back to fix it before any path walking occurs.

### Stage 2: Golden Path Walking

The verification agent reads acceptance criteria from `milestone.md` and `requirements.md`, translates them into concrete user journeys, and executes those journeys against the live environment using the selected verification modalities.

**Browser modality:**
- Playwright MCP driving a real browser
- Navigate to pages, interact with elements, verify behavior
- Follow flows through to completion
- Test error states (invalid input, unauthorized access)
- Check that what appears makes visual/semantic sense

**HTTP modality:**
- Actual HTTP requests against the running server
- Check response shapes, status codes, data integrity
- Test authentication flows and authorization boundaries
- Walk multi-step flows (create → list → update → delete)

**Shell modality:**
- Real command invocation with real arguments
- Inspect real output, help text, error messages
- Test common workflows end-to-end

**Code modality:**
- Import the library and call key functions
- Verify return values and side effects
- Test error cases and type correctness

**Critical distinction from traditional e2e tests:** The agent interprets results, not just asserts against snapshots. A traditional test says "this div should contain 'Welcome'." An agentic test says "after logging in, the user should see something that indicates they're authenticated and can proceed." The agent handles UI changes that would break brittle selectors. It tests intent, not implementation.

#### The Perceptual Record

During golden path walking, the verification agent captures raw perception artifacts at every state transition:

- **Accessibility snapshots** at page loads, form submissions, navigation (Browser modality)
- **Screenshots** at key visual moments (Browser modality)
- **HTTP response bodies** including headers, status codes, and body (HTTP modality)
- **Command output** including stdout, stderr, and exit code (Shell/Code modality)
- **The complete action sequence** — what was done, in what order, what was observed

Raw artifacts are stored in `phases/verification/artifacts/`. `execution.md` references them by path and includes the verifier's narrative interpretation. This keeps execution.md readable while making raw evidence available to the coordinator and Brutalist.

### Stage 3: Exploratory Testing

After walking prescribed golden paths, the verification agent shifts intent — from confirming prescribed paths work to actively trying to break things.

**What exploratory testing covers:**
- Adversarial inputs (SQL injection attempts, XSS payloads, boundary values, empty strings)
- Unexpected navigation (back button, direct URL access, rapid clicks, browser refresh mid-flow)
- Error state quality (are error messages helpful? do they guide recovery?)
- Qualitative observations (is the flow intuitive? does the loading state make sense? is the API consistent?)
- Edge cases discovered during golden path walking

**What exploratory testing does NOT cover:**
- Comprehensive security audit (that's Brutalist `roast_security`)
- Performance testing (out of scope for verification)
- Full regression testing (that's implementation-phase unit/integration tests)

**Exploratory testing scope** is determined by the coordinator's judgment during PLAN. Complex milestones (web apps with many interaction surfaces) include thorough exploration. Simple milestones (CLI tool with three commands) may scope it minimally. The coordinator encodes this in `compose.py` — `verify_exploratory` may be included or omitted.

The same perceptual record protocol applies: capture raw artifacts, write structured results to execution.md.

## Coordinator's VERIFY Evaluation

After the verification agent completes its perception stages, the coordinator evaluates — mirroring how it evaluates implementation work during ASSESS.

### The Pattern

1. **Read the perceptual record** — verification/execution.md + artifacts/
2. **Invoke Brutalist `roast_product`** — pass the verifier's experience narrative, key raw artifacts (accessibility snapshots, screenshots, response bodies), and acceptance criteria from requirements.md
3. **Evaluate** — verifier findings + Brutalist experience assessment against acceptance criteria
4. **Decide next step:**
   - Code issue → rework EXECUTE targeting the problem, then re-VERIFY
   - Perception gap flagged by Brutalist → dispatch additional verification pass
   - Experience quality issue → rework EXECUTE, then re-VERIFY
   - All satisfied → dispatch handoff preparation → EXIT

**Brutalist experience assessment is structural** — it always runs during VERIFY. The coordinator can scope exploratory testing, but cannot skip Brutalist. This parallels ASSESS: the coordinator decides implementation scope, but cannot skip Brutalist code assessment. Without external assessment, the verifier's "looks good" is self-reflection by the same model family that wrote the code (see [Research Foundations](../research-foundations.md) §9).

**Mediated perception:** Brutalist receives the verifier's raw artifacts — the same evidence the verifier captured, not a summarized narrative. Brutalist's multiple models independently interpret the raw captures. This is consistent with how Brutalist assesses code (reads it on disk, doesn't independently write it). See [DB-09](../decision-boundaries/09-verification-generalization.md) for the full rationale.

## Handoff Preparation

Once the coordinator's VERIFY evaluation passes — golden paths verified, Brutalist experience assessment resolved — the coordinator dispatches handoff preparation.

**`handoff.md` schema:**

```markdown
# Handoff: <milestone-name>

## Environment
status: running
services:
  - <name>: <url>
  - <name>: <url>
startup_command: <exact command to reproduce this state>
teardown_command: <how to stop everything>

## What Was Built
<Concise description of what changed, from the user's perspective.
Not a changelog — a product description.>

## Walk-Through

### Flow 1: <name>
1. Navigate to <url>
2. You should see <what the agent verified>
3. Click <element> — this should <expected behavior>
4. <continue through the flow>

### Flow 2: <name>
1. ...

## Manual Steps
<Only present if some verification couldn't be automated.
Each step documents WHY it requires manual execution.>

### <step-name>
**Why manual:** <e.g., "touch gesture interaction not automatable via simulator CLI">
1. <manual instruction>
2. <expected observation>

## Third-Party Services

### <service> (<mode>)
status: connected
notes: <anything the user needs to know — e.g., "webhook forwarding
       requires stripe listen running in a separate terminal">

## What the Agent Verified
<Summary of agentic testing — which paths were walked, what was checked,
what passed. Include exploratory testing findings.>

## Known Limitations
<Anything the agent found that works but is rough, or edge cases
that weren't covered by the verification plan.>

## What to Look For
<Specific things the user should pay attention to that automated
verification couldn't fully assess — subjective quality, feel,
visual polish, real-world data behavior.>
```

**Manual walk-through as declared residual.** Automated walk-through steps are the default — the verification agent experienced and verified these flows. The `## Manual Steps` section appears only when some verification couldn't be automated, and each manual step documents WHY. This creates pressure toward better automation. For Browser, HTTP, Shell, and Code modalities, the manual section should be near-empty. For mobile or hardware-dependent flows, manual steps cover what automation can't reach (touch gestures, responsive feel, visual polish).

**The environment is left running.** The user walks into a prepared room. `handoff.md` includes startup commands so the user can recreate the environment later, but the default state after milestone completion is: services are up, data is seeded, browser can be opened immediately.

## Inter-Phase Smoke Tests

In addition to the full verification phase at milestone end, Clou runs integrative smoke tests at **phase completion boundaries** — before the coordinator advances to the next phase. These are dispatched by the coordinator and are lighter than the full verification phase but heavier than "does it compile."

### What Smoke Tests Cover

- **Golden path walking** — exercise the end-to-end journey across all completed phases, not just the latest phase in isolation
- **Integration points** — verify that the latest phase's outputs compose correctly with prior phases' outputs
- **Environment health** — confirm the dev environment still starts and services still connect after the new phase's changes

### What Smoke Tests Do NOT Cover

- Full Brutalist assessment (that's the ASSESS cycle)
- Comprehensive edge cases and error states (that's the VERIFY phase at milestone end)
- Code quality review (coordinator handles that in ASSESS)

### When Smoke Tests Fail

If a smoke test reveals a regression or integration failure:
1. The coordinator logs the finding in `decisions.md` with evidence
2. The coordinator enters a rework EXECUTE cycle targeting the specific integration failure
3. The rework cycle is scoped to the integration point, not the entire phase

This catches cascading compositional failures early. Research shows compositionality breaks at 2+ hops and errors amplify through agent chains (see [Research Foundations](../research-foundations.md) §9). Smoke tests at phase boundaries are the structural countermeasure — catching failures at the boundary where they're introduced, before they compound through subsequent phases.

### Relationship to Full Verification

The full verification phase remains the comprehensive end-of-milestone verification. Inter-phase smoke tests reduce the probability of discovering fundamental integration failures during final verification, when rework is most expensive.

## Coordinator's Exit Condition Change

The coordinator doesn't exit when implementation is done. It doesn't exit when Brutalist code assessment is satisfied. It exits when the verification phase has produced a `handoff.md` with a running environment, verified golden paths, and Brutalist experience assessment resolved.

```
plan → implement → brutalist code review → resolve feedback →
    verify (materialize → walk paths → explore →
            coordinator: brutalist experience assessment → evaluate →
            prepare handoff) →
    exit or loop
```

If verification fails — environment won't start, a golden path is broken, Brutalist flags experience issues, the agent encounters incoherent behavior — the coordinator loops back to implementation with the findings as input.

## Verification Phase Files

```
phases/verification/
├── phase.md        # Acceptance criteria from requirements.md,
│                   # translated into verifiable test plan.
│                   # Includes selected verification modalities
│                   # and exploratory testing scope.
│                   # User journeys defined in verify() function of compose.py:
│                   #   - Which pages/endpoints to visit
│                   #   - What data to input
│                   #   - What behavior to expect
│                   #   - Which error cases to test
├── execution.md    # Perceptual record:
│                   #   - Environment materialization outcome
│                   #   - Each journey walked with evidence captured
│                   #   - Exploratory testing findings
│                   #   - Qualitative observations
│                   #   - References to raw artifacts
└── artifacts/      # Raw perception captures:
                    #   - *.png (screenshots)
                    #   - *.json (accessibility snapshots, response bodies)
                    #   - *.txt (command output)
```

## Walk-Through Quality

The walk-through in `handoff.md` is the key user-facing innovation. Standards:

1. **Steps are concrete.** Not "check the dashboard" but "navigate to http://localhost:5173/dashboard, you should see three cards showing revenue, users, and orders."

2. **Expected behavior is described.** Not "it should work" but "clicking 'New Order' should open a modal with a form. Fill in 'Test Item' for name and '29.99' for price. Submit. The order should appear in the table below."

3. **Visual/behavioral expectations are stated.** "The chart should show data for the last 30 days. The Y-axis should auto-scale. Hovering over a data point should show a tooltip with the exact value."

4. **Error cases are included when relevant.** "Try submitting the form with an empty name field. You should see a validation error below the name input."

5. **Third-party service state is documented.** "The Stripe webhook panel at dashboard.stripe.com/test/webhooks should show successful deliveries after you complete a test purchase."

6. **Manual steps declare their reason.** Every step in `## Manual Steps` includes WHY it couldn't be automated. "Touch gesture interaction not automatable via simulator CLI" — not just "swipe right on the card."
