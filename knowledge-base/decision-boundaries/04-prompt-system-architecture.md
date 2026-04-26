# DB-04: Prompt System Architecture

**Status:** DECIDED — Light system_prompt + per-cycle protocol files + XML structure
**Severity:** High — the prompts ARE the product
**Question:** What is the structure of the prompt system that teaches Claude to be the supervisor, coordinator, and agent team member?

**DB-01 impact:** The orchestrator passes prompts via `ClaudeAgentOptions.system_prompt`. This resolves the delivery mechanism — SDK system_prompt, no CLAUDE.md.
**DB-03 impact:** Session-per-cycle means the orchestrator constructs a targeted prompt for each cycle, not just at coordinator spawn. The prompt includes the cycle type and pointers to the specific golden context files that cycle needs to read.
**Research impact:** See [Research Foundations](../research-foundations.md). Key findings that shaped this decision:

- System prompts have no architectural privilege over user messages (Geng et al. 2025, "Control Illusion")
- Instruction density degrades performance past a threshold — 68% accuracy at 500 instructions (IFScale 2025)
- First tokens are architecturally privileged due to attention sinks (ICLR 2024/2025)
- Decomposition outperforms monolithic prompts — the modular structure itself drives improvement (DecomP, ICLR 2023)
- XML outperforms prose for structured instructions — up to 40% performance variation (2024)
- Role prompting (elaborate personas) does not improve factual accuracy (Mollick et al. 2025)
- Context engineering > prompt engineering — 54% better on multi-step tasks (Anthropic 2025)

## Decision

### Two-Layer Architecture

Each session receives two prompt surfaces:

| Layer | Set when | Contents | Size |
|---|---|---|---|
| `system_prompt` | Session creation | Identity anchor, critical invariants, pointer to protocol file | ~800–1,200 tokens |
| Initial `query()` | Cycle start | Cycle type, protocol file pointer, golden context file pointers | ~200–400 tokens |

The agent reads the protocol file and golden context files as its first actions. The protocol contains the full behavioral specification for the cycle type. This is context engineering — the right information, at the right time, in the right format.

### Why Light System Prompts

Research shows:

1. **No architectural privilege.** System prompts are not more reliably followed than user messages. Social cues and instruction clarity matter more than position.
2. **Instruction density degrades.** A 4,000–6,000 token system prompt with dozens of behavioral rules hits diminishing returns. Fewer, higher-priority constraints are more reliably followed.
3. **Decomposition wins.** Per-cycle protocol files mean each session reads only the protocol it needs, not the entire specification.
4. **Attention sinks.** The first tokens get disproportionate architectural weight. The identity anchor exploits this — the single most important framing comes first.

### System Prompt Structure

All system prompts use XML tags (Claude is tuned for XML structure):

```xml
<identity>
[Brief identity — what you are, one sentence. First tokens — exploits attention sinks.]
</identity>

<invariants>
[5–7 critical constraints that MUST NOT be violated. These are behavioral rails, not protocol steps.]
</invariants>

<protocol>
[Pointer to the protocol file to read before doing anything else.]
</protocol>
```

### Coordinator System Prompt (~800–1,200 tokens)

```xml
<identity>
You are Clou's coordinator for milestone '{{milestone}}'.
You execute one cycle per session. Your cycle type and context files
are provided in the initial message.
</identity>

<invariants>
- Write all state to golden context before exiting. If it is not in
  golden context, it does not survive.
- You own .clou/milestones/{{milestone}}/ — compose.py, decisions.md,
  status.md, phase specs, escalations, active/coordinator.md.
- You do not write code. Agent teams write code.
- Every exercise of judgment is logged in decisions.md with reasoning.
- Escalations include analysis, options with tradeoffs, and a
  recommendation. Never delegate thinking upward.
- Brutalist feedback is evaluated critically against requirements.md,
  not accepted or rejected reflexively.
</invariants>

<protocol>
Read your cycle protocol file before doing anything else.
The initial message tells you which file to read and which
golden context files to load for this cycle.
</protocol>
```

### Supervisor System Prompt (~1,500–2,000 tokens)

The supervisor is a special case — long-running, user-facing, no cycle decomposition. Its system prompt is slightly heavier but follows the same structure:

```xml
<identity>
You are Clou's supervisor. You are the user's direct interface to
Clou. You manage project planning, milestone creation, and milestone
completion evaluation. You do not write code or manage agent teams.
</identity>

<invariants>
- You own project.md, roadmap.md, requests.md, milestone creation
  (milestone.md, requirements.md), escalation dispositions, and
  active/supervisor.md.
- You never touch code. You never see inter-agent messages.
- Milestones are sequential by default. One active coordinator at
  a time.
- You spawn coordinators via the clou_spawn_coordinator MCP tool.
  Once spawned, the coordinator runs autonomously.
- When evaluating milestone completion, you read handoff.md and
  decisions.md. You verify the handoff, not the code.
- Write your checkpoint to active/supervisor.md at each loop boundary.
</invariants>

<protocol>
Read .clou/prompts/supervisor.md for your full protocol.
Then read .clou/project.md and .clou/roadmap.md to orient yourself.
If .clou/active/supervisor.md exists, read it to resume from
your last checkpoint.
</protocol>
```

### Worker System Prompt (~400–600 tokens)

```xml
<identity>
You are an agent team member implementing code for milestone
'{{milestone}}', phase '{{phase}}'.
</identity>

<invariants>
- You write code and tests. Nothing else in .clou/ except
  execution.md in your assigned phase.
- Read your function signature in compose.py for inputs, outputs,
  and success criteria.
- Read phase.md for phase context and project.md for coding
  conventions.
- Write your results to execution.md: status, files changed, tests,
  notes.
</invariants>

<protocol>
Read .clou/prompts/worker.md for your full protocol.
</protocol>
```

### Verifier System Prompt (~600–800 tokens)

```xml
<identity>
You are the verification agent for milestone '{{milestone}}'.
You verify that the milestone meets its acceptance criteria by
walking golden paths against a live environment.
</identity>

<invariants>
- You write execution.md in the verification phase and handoff.md
  at the milestone level.
- You do not fix code. If something fails verification, you document
  the failure in execution.md.
- Verification has three stages: environment materialization, agentic
  path walking, handoff preparation.
- The dev environment must be left running for the user.
</invariants>

<protocol>
Read .clou/prompts/verifier.md for your full protocol.
</protocol>
```

## Per-Cycle Protocol Files

```
.clou/prompts/
├── supervisor.md              # Full supervisor protocol
├── coordinator-plan.md        # PLAN cycle: read requirements, write compose.py + phase specs
├── coordinator-execute.md     # EXECUTE cycle: dispatch agent teams, monitor
├── coordinator-assess.md      # ASSESS cycle: evaluate execution.md, invoke Brutalist, decide
├── coordinator-verify.md      # VERIFY cycle: dispatch verification agent
├── coordinator-exit.md        # EXIT cycle: evaluate handoff.md, write final status, exit
├── worker.md                  # Agent team member protocol
└── verifier.md                # Verification agent protocol
```

Each coordinator protocol file is small (~500–1,000 tokens) and focused on one cycle type's semantics. The coordinator reads exactly one per cycle.

### Protocol File Format

Protocol files use XML structure, front-load critical content (attention sinks apply to file reads too), and contain the specific procedures and schemas for that cycle type:

```xml
<cycle type="ASSESS">

<objective>
Evaluate phase execution results against requirements and compose.py criteria.
Determine: rework needed, phase advanceable (per the engine's gate verdict), or
escalation required.  M52: phase completion is judged by the engine's
phase-acceptance gate (DB-22), not by self-assessment.
</objective>

<procedure>
1. Read execution.md for the current phase.
2. Compare each task's results against its criteria in compose.py.
3. Invoke Brutalist (roast_codebase) on changed files.
4. Evaluate Brutalist findings against requirements.md — not all
   findings warrant action.
5. For each finding: accept (rework), override (log in decisions.md
   with reasoning), or escalate.
6. Read the engine's phase-acceptance verdict from cycle context
   (`prev_cp.last_acceptance_verdict`). Routing keys on the verdict:
   - `Advance` + more phases remain: increment `phases_completed` via
     `clou_write_checkpoint` (which enforces single-increment +
     verdict-phase match), set next_step to EXECUTE.
   - `Advance` + all phases complete: set next_step to VERIFY.
   - `GateDeadlock` (recoverable): set next_step to EXECUTE_REWORK.
   - `GateDeadlock` (structural): file `clou_halt_trajectory`.
   - Rework needed for valid findings: set next_step to EXECUTE_REWORK.
7. Write all judgments to decisions.md.
</procedure>

<schemas>
[decisions.md entry format, Brutalist evaluation criteria, etc.]
</schemas>

</cycle>
```

## build_cycle_prompt() — Updated

The initial query combines cycle type, protocol file pointer, and golden context file pointers:

```python
def build_cycle_prompt(
    project_dir: Path, milestone: str,
    cycle_type: str, read_set: list[str]
) -> str:
    milestone_prefix = f".clou/milestones/{milestone}"
    file_list = "\n".join(
        f"- {milestone_prefix}/{f}" if not f.startswith("project.md")
        else f"- .clou/{f}"
        for f in read_set
    )
    protocol_file = f".clou/prompts/coordinator-{cycle_type.lower()}.md"

    return (
        f"This cycle: {cycle_type}.\n\n"
        f"Read your protocol file first:\n- {protocol_file}\n\n"
        f"Then read these golden context files:\n{file_list}\n\n"
        f"Execute the {cycle_type} protocol. "
        f"Write all state to golden context before exiting."
    )
```

## Prompt Loading — Updated

The `load_prompt()` function now loads only the small system prompt, not the full protocol:

```python
def load_prompt(tier: str, project_dir: Path, **kwargs) -> str:
    """Load and parameterize a tier's system prompt (identity + invariants only)."""
    prompt_path = project_dir / ".clou" / "prompts" / f"{tier}-system.xml"
    prompt = prompt_path.read_text()

    for key, value in kwargs.items():
        prompt = prompt.replace(f"{{{{{key}}}}}", value)

    return prompt
```

System prompt templates live in `.clou/prompts/` as `*-system.xml` files. Protocol files are separate `*.md` files the agent reads during execution.

Updated file structure:

```
.clou/prompts/
├── supervisor-system.xml      # Supervisor system prompt template
├── supervisor.md              # Full supervisor protocol (agent reads)
├── coordinator-system.xml     # Coordinator system prompt template ({{milestone}})
├── coordinator-plan.md        # PLAN cycle protocol (agent reads)
├── coordinator-execute.md     # EXECUTE cycle protocol
├── coordinator-assess.md      # ASSESS cycle protocol
├── coordinator-verify.md      # VERIFY cycle protocol
├── coordinator-exit.md        # EXIT cycle protocol
├── worker-system.xml          # Worker system prompt template ({{milestone}}, {{phase}})
├── worker.md                  # Worker protocol (agent reads)
├── verifier-system.xml        # Verifier system prompt template ({{milestone}})
└── verifier.md                # Verifier protocol (agent reads)
```

## Context Window Budget

| Tier | System prompt | Protocol file | Golden context | Available for work |
|---|---|---|---|---|
| Supervisor | ~1,500–2,000 | ~2,000–3,000 | ~2,000–4,000 | ~190K+ |
| Coordinator | ~800–1,200 | ~500–1,000 | ~2,000–6,000 (varies by cycle) | ~190K+ |
| Worker | ~400–600 | ~300–500 | ~1,000–2,000 | ~195K+ |
| Verifier | ~600–800 | ~800–1,200 | ~2,000–3,000 | ~193K+ |

Session-per-cycle means the coordinator never accumulates context across cycles. Each cycle starts with ~4,000–8,000 tokens of prompt + context, leaving 190K+ for actual work. This is a direct benefit of the session-per-cycle decision (DB-03).

## Design Principles Applied

- **Front-load critical content.** Attention sinks mean the first tokens of system prompts AND read files get disproportionate weight. Identity anchors come first. Protocol files lead with the objective, not preamble.
- **Count instructions, not just tokens.** A 1,000-token system prompt with 5 invariants is more reliably followed than a 1,000-token prompt with 20 rules.
- **XML structure.** Claude is tuned for XML. Use XML tags to demarcate sections in both system prompts and protocol files.
- **No role backstory.** "You are Clou's coordinator" is sufficient. "You are an expert software architect with 20 years of experience..." adds nothing.
- **Protocol is read, not injected.** The coordinator reads its protocol file as context, not as system instructions. Research shows no reliability difference, and this enables decomposition.
