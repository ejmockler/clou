# DB-11: Harness Architecture

**Status:** DECIDED
**Decided:** 2026-03-24
**Severity:** High — without resolution, Clou is structurally limited to software construction
**Question:** How does Clou configure itself for tasks beyond software, and how are those configurations specified, selected, and applied?

**Decision:** Harness templates — pre-built capability profiles that specify tool configuration, quality gates, verification modalities, write permissions, and compose conventions per domain. The supervisor selects a template based on user intent. The orchestrator reads the template and configures agent definitions, hooks, and MCP servers accordingly. Templates ship with the package (`clou/harnesses/`); the active selection is recorded in golden context. Software construction is the first template, extracted from the current hardcoded configuration. The user never interacts with the harness directly.

## Design Constraints from Research

- **§9 (LLM-Modulo):** LLMs can't self-verify. External verification is the mechanism that makes the planning loop reliable. Quality gates must be structurally present — the specific gate changes per domain, but the requirement for external verification does not. A template without a quality gate degrades Clou to self-assessment, which research shows produces false beliefs that persist indefinitely (§10).
- **§10 (Multi-Agent Failure Modes):** 79% of multi-agent failures are specification/coordination problems. The harness template is a specification artifact — it defines what tools each tier has, what quality gates run, and what verification means. Ambiguity in the template is a specification failure. Templates must be precise enough that the orchestrator can configure itself without judgment calls.
- **§1 (Context Is Adversarial):** Tool descriptions compete for attention. Agent accuracy collapses past ~30 tools when descriptions overlap. Templates must curate tool sets — not provide everything available, but select what's relevant for the domain. Fewer active tools = less distractor surface = better accuracy.
- **§5 (Instruction Density):** Template-specific protocol variants must not bloat prompt instruction count. A template should adjust what tools are available and what quality gates run — not rewrite the coordinator's behavioral specification.
- **§7 (Decomposition):** The harness template is a decomposition of Clou's configuration into domain-specific and domain-agnostic components. The domain-agnostic parts (three-tier hierarchy, judgment loop, golden context, session-per-cycle) remain fixed. The domain-specific parts (tools, gates, verification, permissions) are parameterized.
- **§12 (NIAH Limits):** Models struggle to integrate information across large contexts. The template should be compact — a focused specification, not an exhaustive manual. The coordinator reads the template to understand its tools and gates; bloating it degrades comprehension.
- **§13 (Harness Engineering, new):** The 2026 consensus: the harness is the hard part, not the model. ToolMaker (ACL 2025) shows agents can create tools autonomously — 80% success rate. TEA Protocol (AgentOrchestra) treats tools, environments, and agents as first-class resources with lifecycles. MCP registries enable semantic tool discovery. These capabilities inform future template evolution but are not required for the initial design.
- **§14 (Tool Discovery, new):** MCP Gateway Registry implements FAISS-indexed semantic search over thousands of tools. Agent accuracy collapses past 30 tools with overlapping descriptions. Dynamic tool discovery is viable but must respect the accuracy ceiling. Templates curate; registries discover.

## Decisions

### D1: Template as Capability Profile

A harness template is a structured specification that defines what a Clou deployment can do. It is not a workflow, not a prompt, and not a domain ontology. It is a capability profile — the set of tools, gates, and verification mechanisms available to each tier.

**Template fields:**

| Field | Type | What it configures | Currently hardcoded in |
|---|---|---|---|
| `name` | str | Template identifier | N/A (implicit "software") |
| `description` | str | One-line purpose | N/A |
| `agents` | dict[str, AgentSpec] | Per-tier agent definitions (tools, prompt ref, model) | `orchestrator.py:_build_agents()` (now reads from template) |
| `quality_gates` | list[QualityGateSpec] | MCP servers used during ASSESS + VERIFY | `orchestrator.py` (was `_BRUTALIST_MCP`/`_CDP_MCP`, now removed) |
| `verification_modalities` | list[str] | Default modality set for VERIFY | DB-09 (Browser, HTTP, Shell, Code) |
| `mcp_servers` | dict[str, MCPServerSpec] | MCP server configurations for coordinator sessions | `orchestrator.py` (was hardcoded constants, now removed) |
| `write_permissions` | dict[str, list[str]] | Per-tier write boundary patterns | `hooks.py:WRITE_PERMISSIONS` (preserved at module level for backward compat) |
| `compose_conventions` | ComposeConventions | Constraints on compose.py structure | Implicit (verify() required, phase comments) |

**AgentSpec fields:**

| Field | Purpose |
|---|---|
| `description` | Agent capability description (passed to AgentDefinition) |
| `prompt_ref` | Protocol file path (e.g., `worker.md`, `assessor.md`) |
| `tools` | List of tool names available to this agent tier |
| `model` | Model to use (default: `opus`) |
| `tier` | Enforcement tier for write permissions (e.g., `"worker"`, `"coordinator"`). Maps agent names to permission groups — required because non-standard agent names would otherwise bypass write boundary enforcement in hooks.py. **Validation:** template loader must verify that every `tier` value has a corresponding key in `write_permissions`. Missing tier → load-time error, not late-binding failure during writes. |

**QualityGateSpec fields:**

| Field | Purpose |
|---|---|
| `mcp_server` | Name of the MCP server providing the gate |
| `assess_agent` | Agent name whose tools run during ASSESS (the agent's tool list is the single source of truth for available gate tools) |
| `verify_agent` | Agent name whose tools run during VERIFY (if different from assess_agent) |
| `required` | Whether the gate is blocking (essential infrastructure) or advisory |

Note: Earlier designs listed `assess_tools` and `verify_tools` as separate tool name lists, duplicating the agent's tool list in a different format. This created a dual source of truth — adding a tool to the gate spec without adding the prefixed version to the agent's tools would silently misconfigure. The agent reference design eliminates this: the gate spec says *which agent runs*; the agent spec says *what tools it has*.

**ComposeConventions fields:**

| Field | Purpose |
|---|---|
| `require_verify` | Whether compose.py must include a verify() function |
| `phase_comments` | Whether phase boundaries must be marked with comments |
| `validators` | List of validation functions to run (default: `graph.py:validate`) |

### D2: Template Format — Python Module (Pragmatic, Not Architecturally Superior)

Templates are Python modules, not YAML/TOML/JSON. This is a pragmatic choice for the single-template case, not a permanent architectural commitment.

**Why Python for now:**

1. **Consistency.** compose.py is Python. The orchestrator parses Python (graph.py). Adding another format parser creates a second code path with its own failure modes.
2. **Validation.** Python modules can be imported and type-checked (mypy). YAML/TOML validation requires a separate schema definition and validator.
3. **Familiarity.** The orchestrator, hooks, prompts, and graph validation are all Python. One language for the whole system.

**Known tradeoff:** Templates are human-authored configuration, not LLM-generated executable representation like compose.py. Python-as-config means templates execute at import time — a malformed template crashes the orchestrator before error handling runs, unlike a declarative format which gives a parse error. For a single-developer, single-template system this risk is acceptable. At template count > 3 or with untrusted template authors, the format decision should be revisited.

**The stable interface is the schema** (`HarnessTemplate`, `AgentSpec`, etc.), not the file format. The format is an implementation detail that may change.

**Template module structure:**

```python
# clou/harnesses/software_construction.py
"""Software construction harness template.

Configures Clou for building, testing, and deploying software systems.
This is the default template — extracted from the original hardcoded
configuration in orchestrator.py.
"""

from clou.harness import (
    AgentSpec,
    ComposeConventions,
    HarnessTemplate,
    MCPServerSpec,
    QualityGateSpec,
)

template = HarnessTemplate(
    name="software-construction",
    description="Build, test, and deploy software systems",
    agents={
        "implementer": AgentSpec(
            description=(
                "Implement code changes for assigned tasks. "
                "Read compose.py for your function signature, "
                "phase.md for context. Write results to execution.md."
            ),
            prompt_ref="worker",
            tier="worker",
            tools=[
                "Read", "Write", "Edit", "MultiEdit",
                "Bash", "Grep", "Glob",
                "WebSearch", "WebFetch",
            ],
        ),
        "assessor": AgentSpec(
            description=(
                "Invoke quality gate tools on changed code and "
                "structure findings into assessment.md."
            ),
            prompt_ref="assessor",
            tier="assessor",
            tools=[
                "Read", "Write", "Bash", "Grep", "Glob",
                "mcp__brutalist__roast_codebase",
                "mcp__brutalist__roast_architecture",
                "mcp__brutalist__roast_security",
                "mcp__brutalist__roast_product",
                "mcp__brutalist__roast_infrastructure",
                "mcp__brutalist__roast_file_structure",
                "mcp__brutalist__roast_dependencies",
                "mcp__brutalist__roast_test_coverage",
            ],
        ),
        "verifier": AgentSpec(
            description=(
                "Verify milestone completion by perceiving the "
                "output as a user would. Materialize the environment, "
                "walk golden paths, explore adversarially, prepare handoff.md."
            ),
            prompt_ref="verifier",
            tier="verifier",
            tools=[
                "Read", "Write", "Bash", "Grep", "Glob",
                "WebSearch", "WebFetch",
                "mcp__cdp__navigate",
                "mcp__cdp__screenshot",
                "mcp__cdp__accessibility_snapshot",
                "mcp__cdp__evaluate_javascript",
                "mcp__cdp__click",
                "mcp__cdp__type",
                "mcp__cdp__network_get_response_body",
                "mcp__cdp__console_messages",
            ],
        ),
    },
    quality_gates=[
        QualityGateSpec(
            mcp_server="brutalist",
            assess_agent="assessor",
            verify_agent="verifier",
            required=True,
        ),
    ],
    verification_modalities=["Browser", "HTTP", "Shell", "Code"],
    mcp_servers={
        "brutalist": MCPServerSpec(
            command="npx",
            args=["-y", "@brutalist/mcp@latest"],
            type="stdio",
        ),
        "cdp": MCPServerSpec(
            command="npx",
            args=["-y", "chrome-devtools-mcp@latest"],
            type="stdio",
        ),
    },
    write_permissions={
        "supervisor": [
            "project.md",
            "roadmap.md",
            "requests.md",
            "milestones/*/milestone.md",
            "milestones/*/requirements.md",
            "milestones/*/escalations/*.md",
            "active/supervisor.md",
        ],
        "coordinator": [
            "milestones/*/compose.py",
            "milestones/*/status.md",
            "milestones/*/decisions.md",
            "milestones/*/escalations/*.md",
            "milestones/*/phases/*/phase.md",
            "active/coordinator.md",
            "services/*/setup.md",
            "services/*/.env.example",
            "services/*/status.md",
        ],
        "worker": [
            "milestones/*/phases/*/execution.md",
        ],
        "verifier": [
            "milestones/*/phases/verification/execution.md",
            "milestones/*/handoff.md",
        ],
        "assessor": [
            "milestones/*/assessment.md",
        ],
    },
    compose_conventions=ComposeConventions(
        require_verify=True,
        phase_comments=True,
        validators=["graph.validate"],
    ),
)
```

### D3: Template Selection — Supervisor Proposes, User Confirms by Absence

The supervisor selects a template during project initialization. The selection mechanism:

1. **Default.** If the user's intent is clearly software construction (the dominant case), the supervisor selects `software-construction` without discussion. This is the path of least friction — most users will never know templates exist.

2. **Ambiguous intent.** If the user describes a task that doesn't clearly match any template, the supervisor proposes the closest match and briefly states what it provides: "I'll configure for software construction — code editing, quality analysis with Brutalist, browser-based verification. Does that fit, or is this a different kind of work?"

3. **No match.** If no template fits, the supervisor escalates: "I don't have a good configuration for [task type]. I could adapt the software template, but [specific limitation]. How would you like to proceed?"

4. **Recorded in golden context.** The active template name is recorded in `project.md` (a new field). The coordinator reads this to know what it's working with. The template itself lives in `clou/harnesses/` — it's not copied into `.clou/`.

5. **Changeable.** The supervisor can change the template mid-project if the user's needs shift. Template changes take effect at the next coordinator spawn — in-flight coordinators use the template they started with.

**Why not `.clou/harness.yaml`:** The template is not a per-project artifact. It ships with Clou. What's per-project is the *selection* — a single field in `project.md`. Copying the template into `.clou/` would create a maintenance problem (template updates don't propagate) and bloat golden context.

### D4: Quality Gate Pluggability

Quality gates are essential infrastructure (Design Principle 10, DB-05). The pattern is fixed: invoke gate → coordinator evaluates critically → accept/override/escalate. The gate itself is a template parameter.

**Levels of quality gate strength:**

| Level | What it means | Example |
|---|---|---|
| **External multi-tool gate** | Dedicated MCP server with domain-specific analysis tools. Multiple perspectives. | Brutalist (software): deploys Claude Code, Codex, Gemini CLI |
| **Multi-model critique** | Generic multi-model assessment using the Brutalist `roast` tool with a domain-appropriate prompt. No domain-specific tooling. | `roast_idea` or `roast_cli_debate` applied to non-software artifacts |
| **Coordinator self-assessment** | No external gate. Coordinator evaluates against requirements.md criteria directly. | Fallback when no gate exists for the domain |

The software template uses level 1 (Brutalist). Future templates may use level 2. Level 3 is the degraded mode — always available but weakest.

**The template's `quality_gates[].required` field determines behavior:**
- `required=True`: gate unavailability is a blocking error (escalate to supervisor and user). This is Brutalist's current status.
- `required=False`: gate unavailability triggers coordinator self-assessment (level 3 fallback) with a logged decision noting the degradation.

A template with no quality gates at all is valid but operates entirely at level 3 — the weakest configuration. The coordinator's judgment loop still runs; it just lacks external calibration. The system doesn't prevent this, but it's an explicit choice recorded in the template.

### D5: Capability Axes — Degradation Prediction, Not Domain Taxonomy

Templates do not classify tasks into domains (software, research, creative, etc.). Domain taxonomies imply coverage; coverage implies completeness; completeness is false.

Instead, each template implicitly occupies a position on five capability axes that predict how well Clou can operate:

| Axis | High | Low |
|---|---|---|
| **Structural formality** | Software: typed dependencies, AST-verifiable plans | Creative: fluid, context-dependent structure |
| **Verification automability** | Software: run it, test it, check it | Creative: subjective, requires human judgment |
| **Training data density** | Software: massive corpus | Niche industrial: sparse, proprietary |
| **Tool availability** | Software: rich MCP/CLI ecosystem | Physical/proprietary: few APIs |
| **State locality** | Software: files in a directory | Distributed: state across systems |

A template doesn't declare its axis positions. They're emergent properties of its capability profile. The value of the model is predictive: before building a new template, assess the axes. If verification automability is low, plan for more human-in-the-loop. If tool availability is sparse, expect capability gaps.

The software-construction template scores high on all five axes. This is why it works well. Future templates will score lower on some axes, and that's fine — the system degrades gracefully via the escalation protocol.

### D5b: Environment Detection as Template Capability

Templates can define domain-specific exploration capabilities that run before or during the PLAN cycle. For software construction, this means scanning: CLI tools available, project files (package.json, pyproject.toml, Dockerfile), git state, existing directory structure, test frameworks, build systems. This information enters the coordinator's PLAN cycle through the template's agent definitions — an "explorer" agent with read-only tools that produces a structured environment summary.

This is a template-level concern, not an architectural one. The supervisor-user dialogue is the sensemaking mechanism at the domain-agnostic level (see supervisor protocol, Research Foundations §11). Domain-specific exploration (what's in the directory, what tools are available) supplements the supervisor's understanding with material context the user may not have articulated.

**Not yet implemented.** The software-construction template currently defines implementer, assessor, and verifier agents. An explorer agent is a future addition — its value is empirically validated by LingmaAgent (+18.5% on SWE-bench) and Microsoft Code Researcher (58% vs 37.5% on kernel crashes), both of which implement repository understanding phases before planning.

### D6: Compose.py Stays

The compose.py format (DB-02) is already domain-general. Typed async functions with dependency edges, success criteria in docstrings, `gather()` for concurrency — this describes any decomposable task, not just software.

The typed-function call graph works because:
- The transformer has deep code-trained priors for recognizing function dependency structures
- Type annotations express semantic dependencies (`LiteratureMap` → `MethodologyDesign` means "literature informs methodology"), not runtime types
- AST validation checks structural properties (well-formedness, acyclicity, completeness) that are domain-independent
- The format is the strongest-performing representation for transformer comprehension of DAGs (DB-02 research)

What the template can customize via `compose_conventions`:
- Whether `verify()` is required (software: yes; a pure analysis task might not need verification)
- Whether phase boundaries must be comment-marked
- Additional validators beyond `graph.validate` (future: domain-specific structural checks)

What the template cannot change:
- The Python format itself (all templates use compose.py)
- The AST validation (structural soundness is always checked)
- The one-agent-per-function dispatch model (DB-10)

### D7: Where Templates Live

**Ship with package:** `clou/harnesses/*.py` — template modules that import from `clou.harness` (the schema). These are part of the Clou distribution, maintained by developers, version-controlled with the package.

**Active selection in golden context:** `project.md` gains a `template:` field. The orchestrator reads this field and imports the corresponding module from `clou.harnesses`.

```markdown
# Project Name

template: software-construction

## Description
...
```

**No per-project template copies.** Templates are code, not configuration. Copying them into `.clou/` creates versioning problems and bloats golden context. The project references the template by name; the orchestrator resolves the name to a module.

**Custom templates.** For projects with unusual needs, a user (or the supervisor during an escalation resolution) can specify a template path in `project.md`: `template: /path/to/custom_template.py`. The orchestrator loads via `importlib.util.spec_from_file_location` with validation, not bare `import`. This is an escape hatch, not the primary mechanism.

**Machine-readable extraction.** The `template:` field must appear on its own line in `project.md`, immediately after the `# Project Name` heading, in the format `template: <name>`. The orchestrator extracts this with a regex, not by parsing the full markdown. If the field is missing or malformed, the orchestrator defaults to `software-construction`.

### D8: Template Scope Boundary

The template parameterizes **agent definitions, quality gates, MCP servers, write permissions, and compose conventions**. It does not parameterize coordinator operational constants:

| Parameter | Where it lives | Why not in template |
|---|---|---|
| Cycle cap (`_MAX_CYCLES = 20`) | `orchestrator.py` | Operational limit, not a capability property |
| Budget cap (`_MAX_BUDGET_USD`) | `orchestrator.py` | Cost control is per-deployment, not per-domain |
| Effort policy (max/high by cycle type) | `orchestrator.py` | Judgment-loop tuning, orthogonal to domain |
| Crash/validation retry limits | `orchestrator.py` | Error recovery is structural (DB-05) |
| Sandbox settings | `orchestrator.py` | Security boundary, not domain config |
| Coordinator model | `orchestrator.py` | The coordinator is always the strongest available model |

The template says "what tools you have." The orchestrator says "how long you're allowed to run." These are different concerns. Mixing them bloats the template into a deployment manifest, which it is not.

If a future domain genuinely requires different operational parameters (e.g., a research template needing 50 cycles), the right response is a coordinator-level override mechanism, not a template field. Templates are capability profiles, not runtime policies.

### D9: Template Loading Fallback

Template loading can fail (import error, missing module, malformed template, missing `template` attribute). The fallback protocol:

1. **Attempt to load the named template.** Import `clou.harnesses.<name>`, read `template` attribute.
2. **On any failure** (`ImportError`, `AttributeError`, missing module, validation error): log the error with full traceback, fall back to the software-construction default via a three-tier chain: (a) try importing `clou.harnesses.software_construction`, (b) if that also fails, use `_INLINE_FALLBACK` — a hardcoded constant in `clou/harness.py` that reproduces the exact software-construction configuration.
3. **Report the fallback.** The coordinator's first cycle prompt includes a note: "Template loading failed for `<name>`. Running with default software-construction configuration. The supervisor should be informed."

This means template loading failures are never fatal. The worst case is running with the default configuration — which is the current behavior. The system degrades to what already works.

The `_INLINE_FALLBACK` constant is maintained in `clou/harness.py` alongside the template loading code. A test (`test_inline_fallback_matches_template_module`) enforces parity between the inline fallback and the `software_construction` module template, preventing drift.

**Future constraint:** When restrictive templates exist (e.g., a read-only audit template with no write permissions), falling back to software-construction would be a permission escalation. At that point, the fallback behavior must change: loading failure for a non-default template should be a fatal error (the orchestrator refuses to run with the wrong permissions), while loading failure for the default template itself remains a soft fallback to hardcoded constants. For the single-template case this distinction doesn't apply — the fallback IS the only template.

## Cascading Effects

### On existing decision boundaries

| DB | Effect |
|---|---|
| **DB-01 (Spawning)** | Orchestrator wrapper reads template at startup. No structural change. |
| **DB-02 (Task DAG)** | compose.py format unchanged. `compose_conventions` field allows templates to customize validation constraints. |
| **DB-03 (Context Lifecycle)** | Session-per-cycle unchanged. Template is read once per coordinator spawn, not per cycle. |
| **DB-04 (Prompt Architecture)** | System prompts gain template awareness. Coordinator's initial query includes template context (quality gate names, available tools). Minimal — one line in cycle prompt. |
| **DB-05 (Error Recovery)** | "Brutalist as essential infrastructure" becomes "required quality gates as essential infrastructure." The recovery protocol reads `quality_gates[].required` from the template. **Implemented:** `recovery.py` convergence detection now checks for "assess", "quality gate", and "brutalist" in cycle headers — supporting both generic quality gate headers and backward-compatible Brutalist headers. `validation.py` accepts the same header variants. |
| **DB-06 (Token Economics)** | "Query all relevant Brutalist domains" becomes "query all quality gate tools relevant to the domain." Model selection stays hardcoded in templates. |
| **DB-07 (Milestone Ownership)** | Unchanged. Ownership boundaries are a template parameter. |
| **DB-08 (File Schemas)** | `project.md` gains `template:` field. No other schema changes. |
| **DB-09 (Verification)** | Verification modalities are template defaults. The coordinator can still override per milestone. Verifier tool configuration reads from template. |
| **DB-10 (Team Communication)** | Unchanged. Stigmergy, one-agent-per-function, and mechanical dispatch are structural, not template-specific. |

### On protocols

| Protocol | Effect |
|---|---|
| **Supervisor** | Gains template selection responsibility. New step in supervisor loop. |
| **Coordinator** | ASSESS and VERIFY cycles reference quality gates from template, not Brutalist by name. Tool awareness from template context. |
| **Verification** | Modality defaults from template. Tool configuration from template. |
| **Escalation** | Unchanged. Escalation protocol is structural. |
| **Write** | Write permissions read from template. |

### On implementation

| Component | Effect |
|---|---|
| `orchestrator.py:_build_agents()` | Reads from template instead of hardcoded dict (done) |
| `orchestrator.py:_BRUTALIST_MCP`, `_CDP_MCP` | Removed; `template_mcp_servers(tmpl)` used instead (done) |
| `hooks.py:WRITE_PERMISSIONS` | Preserved at module level; `build_hooks()` accepts optional `template` kwarg (done) |
| `prompts.py:build_cycle_prompt()` | Accepts optional `template`, adds harness name + quality gate context (done) |
| `clou/harness.py` | Template dataclasses, loader with name validation, validator, `_INLINE_FALLBACK` (done) |
| `clou/harnesses/software_construction.py` | First template (extraction of current behavior) (done) |

## Prior Art

**AgentOrchestra TEA Protocol** (arXiv:2506.12508): Treats tools, environments, and agents as first-class resources with explicit lifecycles and versioned interfaces. Scored 89.04% on GAIA with dynamic tool creation and retrieval. Clou's template is a simpler, static version of TEA's resource model — appropriate for Clou's architecture where configuration is per-project, not per-turn.

**ToolMaker** (ACL 2025, arXiv:2502.11705): Autonomous tool creation from GitHub repos. 80% success rate. Relevant to future template evolution — templates could eventually declare *capabilities needed* rather than *specific tools*, with a ToolMaker-style resolver finding or creating the tools. Not needed for the initial design.

**MCP Gateway Registry** (agentic-community): FAISS-indexed semantic search over MCP tools. Enterprise tool discovery. Relevant to future capability resolution — the orchestrator could query a registry to populate a template's tool lists. The initial design uses static tool lists.

**Philipp Schmid** (philschmid.de/agent-harness-2026): "Start simple. Provide robust atomic tools. Let the model make the plan." The template provides the tools; compose.py is the plan the model makes. Alignment with the 2026 harness engineering consensus.

## Resolved Questions

- [x] **Template format:** Python module — pragmatic for single-template case. Schema is the stable interface; format may change at scale.
- [x] **Template location:** `clou/harnesses/` ships with package; `project.md` records selection
- [x] **Selection mechanism:** Supervisor proposes, user confirms by absence, escalation for ambiguity
- [x] **Quality gate pluggability:** Template parameter with required/optional flag and three strength levels. Gate spec references agents, not tool lists (single source of truth).
- [x] **Domain taxonomy:** Rejected. Capability axes for degradation prediction instead (kept informal, not in schema).
- [x] **Compose.py changes:** None. Format is already general.
- [x] **Per-project templates:** Escape hatch via path in project.md, not primary mechanism. Override/inheritance machinery deferred — zero current users.
- [x] **Write permissions:** Template parameter (extracted from current hooks.py constant). AgentSpec includes `tier` field for enforcement mapping.
- [x] **Template scope:** Agents + gates + permissions + compose conventions. Coordinator ops (budget, cycles, effort, sandbox) stay in orchestrator.py.
- [x] **Loading fallback:** On any failure, fall back to hardcoded software-construction defaults. Never fatal.
- [x] **Convergence detection:** Generalized from hardcoded "brutalist" to template-aware gate name matching. `recovery.py` and `validation.py` now check for "assess", "quality gate", and "brutalist" in cycle headers.
