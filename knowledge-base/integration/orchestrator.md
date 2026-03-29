# Clou Orchestrator

The orchestrator is the thin Python layer that manages Clou's session lifecycles via the Claude Agent SDK. It is invisible plumbing — the user interacts with the supervisor session. The orchestrator spawns, monitors, restarts, and cleans up sessions.

## Responsibilities

1. **Load the active harness template** from `project.md`'s `template:` field (DB-11)
2. **Start the supervisor session** with the correct prompt, tools, hooks, and MCP servers
3. **Spawn coordinator sessions** configured from the active template (agent definitions, quality gates, MCP servers, write permissions)
4. **Monitor sessions** for context exhaustion, errors, and completion
5. **Enforce write boundaries** via PreToolUse hooks using template-sourced permissions
6. **Track token usage** per session and cumulative
7. **Handle crashes** by restarting from golden context checkpoints
8. **Provide custom MCP tools** to sessions (clou-specific tools running in-process)

## Entry Point

```python
# clou/orchestrator.py

import asyncio
from pathlib import Path
from claude_agent_sdk import (
    ClaudeSDKClient, ClaudeAgentOptions, AgentDefinition,
    HookMatcher, AssistantMessage, ResultMessage,
    create_sdk_mcp_server, tool,
)

async def main():
    project_dir = Path.cwd()
    clou_dir = project_dir / ".clou"

    if not clou_dir.exists():
        await init_clou(project_dir)

    await run_supervisor(project_dir)

if __name__ == "__main__":
    asyncio.run(main())
```

## Supervisor Session Management

```python
async def run_supervisor(project_dir: Path):
    """Start and manage the supervisor session."""

    # Build Clou MCP tools available to the supervisor
    clou_tools = build_clou_mcp_tools(project_dir)
    clou_server = create_sdk_mcp_server("clou", tools=clou_tools)

    # Load harness template for MCP servers and hook permissions (DB-11).
    tmpl_name = read_template_name(project_dir)
    tmpl = load_template(tmpl_name)
    hooks = build_hooks("supervisor", project_dir, template=tmpl)

    # Supervisor gets quality gate MCP servers + clou (not all template
    # servers — e.g., CDP is for the verifier, not the supervisor).
    gate_servers = {g.mcp_server for g in tmpl.quality_gates}
    all_mcp = template_mcp_servers(tmpl)
    mcp_dict = {
        name: spec for name, spec in all_mcp.items() if name in gate_servers
    }
    mcp_dict["clou"] = clou_server

    options = ClaudeAgentOptions(
        system_prompt=load_prompt("supervisor", project_dir),
        permission_mode="bypassPermissions",
        cwd=str(project_dir),
        model="opus",
        hooks=hooks,
        mcp_servers=mcp_dict,
    )

    async with ClaudeSDKClient(options=options) as supervisor:
        # Protocol files are bundled with the package.
        protocol_file = str(_BUNDLED_PROMPTS / "supervisor.md")
        checkpoint = project_dir / ".clou" / "active" / "supervisor.md"

        if checkpoint.exists():
            await supervisor.query(
                f"Read your protocol file: {protocol_file}\n\n"
                f"Then resume from checkpoint. Read {checkpoint} "
                f"and {project_dir / '.clou' / 'roadmap.md'} to reconstruct your state."
            )
        else:
            await supervisor.query(
                f"Read your protocol file: {protocol_file}\n\n"
                f"Then read {project_dir / '.clou' / 'project.md'} for context. "
                "Greet the user and ask what they'd like to build."
            )

        # Stream messages to user (stdout)
        async for msg in supervisor.receive_messages():
            display_message(msg)
            track_tokens(msg)
```

## Coordinator Session Management

The coordinator runs as a **session-per-cycle** loop. Each cycle (PLAN, EXECUTE, ASSESS, VERIFY, EXIT) is a fresh `ClaudeSDKClient` session. The golden context is the sole state transfer mechanism between cycles.

```python
async def run_coordinator(project_dir: Path, milestone: str) -> str:
    """Run a coordinator for a single milestone via session-per-cycle loop."""

    # Load the active harness template once per milestone (DB-11).
    tmpl_name = read_template_name(project_dir)
    tmpl = load_template(tmpl_name)

    clou_dir = project_dir / ".clou"
    checkpoint_path = clou_dir / "active" / "coordinator.md"
    validation_retries = 0
    crash_retries = 0
    pending_validation_errors = None
    MAX_VALIDATION_RETRIES = 3
    MAX_CRASH_RETRIES = 3
    MAX_CYCLES = 20
    cycle_count = 0

    while True:
        # Determine next cycle from golden context
        cycle_type, read_set = determine_next_cycle(checkpoint_path, milestone)

        if cycle_type == "COMPLETE":
            return "completed"

        # Check milestone cycle limit
        cycle_count = read_cycle_count(checkpoint_path)
        if cycle_count >= MAX_CYCLES:
            await write_cycle_limit_escalation(project_dir, milestone, cycle_count)
            return "escalated_cycle_limit"

        # Build targeted prompt for this cycle (with template context)
        prompt = build_cycle_prompt(
            project_dir, milestone, cycle_type, read_set,
            validation_errors=pending_validation_errors,
            template=tmpl,
        )
        pending_validation_errors = None  # consumed

        status = await _run_single_cycle(
            project_dir, milestone, cycle_type, prompt, template=tmpl,
        )

        if status == "failed":
            crash_retries += 1
            if crash_retries >= MAX_CRASH_RETRIES:
                await write_agent_crash_escalation(project_dir, milestone)
                return "escalated_crash_loop"
            continue  # Retry same cycle from checkpoint

        if status == "agent_team_crash":
            await write_agent_crash_escalation(project_dir, milestone)
            return "escalated_agent_crash"

        if status == "exhausted":
            # Agent wrote mid-cycle checkpoint. Skip validation (partial
            # golden context) and let determine_next_cycle route from it.
            crash_retries = 0
            continue

        # Validate golden context structure after cycle
        validation_errors = validate_golden_context(project_dir, milestone)
        if validation_errors:
            validation_retries += 1
            if validation_retries >= MAX_VALIDATION_RETRIES:
                await write_validation_escalation(
                    project_dir, milestone, validation_errors
                )
                return "escalated_validation"
            await git_revert_golden_context(project_dir, milestone)
            pending_validation_errors = validation_errors
            continue
        else:
            validation_retries = 0
            crash_retries = 0

        # Coordinator-only commit at phase completion
        if cycle_type == "EXECUTE" and checkpoint_path.exists():
            cp = parse_checkpoint(checkpoint_path.read_text())
            if cp.current_phase:
                await git_commit_phase(project_dir, milestone, cp.current_phase)

    return "completed"


async def _run_single_cycle(
    project_dir: Path, milestone: str, cycle_type: str,
    prompt: str, *, template: HarnessTemplate | None = None,
) -> str:
    """Run one coordinator cycle as a fresh session."""

    if template is None:
        template = load_template("software-construction")

    hooks = build_hooks(
        "coordinator", project_dir, milestone=milestone, template=template,
    )

    options = ClaudeAgentOptions(
        system_prompt=load_prompt("coordinator", project_dir, milestone=milestone),
        permission_mode="bypassPermissions",
        cwd=str(project_dir),
        model="opus",
        agents=_build_agents(project_dir, milestone, template),
        hooks=hooks,
        effort="max" if cycle_type in ("ASSESS", "VERIFY") else "high",
        mcp_servers=template_mcp_servers(template),
        sandbox=SandboxSettings(
            enabled=True,
            autoAllowBashIfSandboxed=True,
            allowUnsandboxedCommands=False,
        ),
    )

    try:
        async with ClaudeSDKClient(options=options) as coordinator:
            await coordinator.query(prompt)

            async for msg in coordinator.receive_response():
                _track(msg, tier="coordinator", milestone=milestone)

                # Mid-cycle context exhaustion — force checkpoint and restart
                if _context_exhausted(msg):
                    await coordinator.query(
                        "Context approaching limit. Write a mid-cycle checkpoint "
                        "to active/coordinator.md with partial progress, then exit."
                    )
                    return "exhausted"  # Loop will restart same cycle

                # Detect agent team crashes via SDK TaskNotificationMessage
                if isinstance(msg, TaskNotificationMessage) and msg.status == "failed":
                    await coordinator.query(
                        "Agent team member crashed. Preserve all execution.md "
                        "entries. Do NOT retry. Write checkpoint and exit."
                    )
                    return "agent_team_crash"

        return read_cycle_outcome(project_dir)

    except Exception:
        log.exception("Coordinator cycle crashed for %r", milestone)
        return "failed"


def determine_next_cycle(
    checkpoint_path: Path, milestone: str
) -> tuple[str, list[str]]:
    """Read checkpoint and determine what the next cycle should do."""

    if not checkpoint_path.exists():
        return "PLAN", ["milestone.md", "intents.md", "requirements.md", "project.md"]

    checkpoint = parse_checkpoint(checkpoint_path.read_text())

    match checkpoint.next_step:
        case "PLAN":
            return "PLAN", ["milestone.md", "intents.md", "requirements.md", "project.md"]
        case "EXECUTE" | "EXECUTE (rework)":
            return "EXECUTE", [
                "status.md",
                "compose.py",
                f"phases/{checkpoint.current_phase}/phase.md",
                "active/coordinator.md",
            ]
        case "ASSESS":
            return "ASSESS", [
                "status.md",
                "compose.py",
                f"phases/{checkpoint.current_phase}/execution.md",
                "requirements.md",
                "decisions.md",
                "active/coordinator.md",
            ]
        case "VERIFY":
            return "VERIFY", [
                "status.md", "intents.md", "compose.py",
                "active/coordinator.md",
            ]
        case "EXIT":
            return "EXIT", [
                "status.md", "handoff.md", "decisions.md",
                "active/coordinator.md",
            ]
        case "COMPLETE":
            return "COMPLETE", []

    return "PLAN", ["milestone.md", "intents.md", "requirements.md", "project.md"]


def build_cycle_prompt(
    project_dir: Path, milestone: str,
    cycle_type: str, read_set: list[str],
    validation_errors: list[str] | None = None,
    template: HarnessTemplate | None = None,
) -> str:
    """Construct targeted prompt for a single cycle.

    The system_prompt contains identity + invariants only (~800-1,200 tokens).
    This initial query provides: cycle type, protocol file pointer, and
    golden context file pointers. The coordinator reads the protocol file
    as its first action, then the golden context files.
    """

    milestone_prefix = f".clou/milestones/{milestone}"
    file_list = "\n".join(
        f"- .clou/{f}" if f.startswith(("project.md", "active/"))
        else f"- {milestone_prefix}/{f}"
        for f in read_set
    )
    # Protocol files are bundled with the package, not per-project.
    protocol_file = str(_BUNDLED_PROMPTS / f"coordinator-{cycle_type.lower()}.md")

    prompt = (
        f"This cycle: {cycle_type}.\n\n"
        f"Read your protocol file first:\n- {protocol_file}\n\n"
        f"Then read these golden context files:\n{file_list}\n\n"
        f"Execute the {cycle_type} protocol. "
        f"Write all state to golden context before exiting."
    )

    # Template context for ASSESS/VERIFY cycles (DB-11).
    if template:
        prompt += f"\n\nActive harness: {template.name}."
        if cycle_type in ("ASSESS", "VERIFY") and template.quality_gates:
            gate_names = [g.mcp_server for g in template.quality_gates]
            prompt += f"\nQuality gates: {', '.join(gate_names)}."

    if validation_errors:
        error_list = "\n".join(f"  - {e}" for e in validation_errors)
        prompt += (
            f"\n\nWARNING: Previous cycle produced malformed golden context. "
            f"Specific errors:\n{error_list}\n"
            f"Ensure all golden context writes conform to schema."
        )

    return prompt
```

## Custom MCP Tools

The orchestrator provides in-process MCP tools to Clou sessions. These run in the orchestrator's Python process — no subprocess overhead.

### Tools for the Supervisor

```python
def build_clou_mcp_tools(project_dir: Path) -> list:
    """Build Clou-specific tools available to the supervisor."""

    @tool("clou_spawn_coordinator",
          "Spawn a coordinator session for a milestone. "
          "The coordinator will run its full cycle autonomously.",
          {"milestone": str})
    async def spawn_coordinator(args):
        milestone = args["milestone"]
        result = await run_coordinator(project_dir, milestone)
        return {"content": [{"type": "text",
                "text": f"Coordinator for '{milestone}' {result}. "
                        f"Read .clou/milestones/{milestone}/status.md "
                        f"and handoff.md for results."}]}

    @tool("clou_status",
          "Get current Clou status: active milestone, open escalations, costs.",
          {})
    async def clou_status(args):
        status = read_clou_status(project_dir)
        return {"content": [{"type": "text", "text": status}]}

    @tool("clou_init",
          "Initialize .clou/ directory structure for a new project.",
          {"project_name": str, "description": str})
    async def clou_init(args):
        await init_clou(project_dir, args["project_name"], args["description"])
        return {"content": [{"type": "text",
                "text": f"Initialized .clou/ for '{args['project_name']}'"}]}

    return [spawn_coordinator, clou_status, clou_init]


```

**Note:** Verifier tools (including CDP browser tools) are defined in the harness template's `AgentSpec.tools` list. There is no separate `build_verifier_tools()` function — the template is the single source of truth for all agent tool configuration (DB-11).

## Hook Enforcement

The orchestrator enforces write boundaries via PreToolUse hooks:

```python
def build_hooks(
    tier: str, project_dir: Path,
    *, milestone: str | None = None,
    template: HarnessTemplate | None = None,
) -> dict[str, list[HookConfig]]:
    """Build tier-specific hooks for write boundary enforcement.

    When template is provided, write permissions and agent tier map are
    derived from the template instead of module-level constants.
    When milestone is provided, write patterns are scoped to that
    milestone (milestones/* → milestones/{milestone}).
    """

    # Derive permissions and tier map from template or module-level constants.
    if template is not None:
        permissions = template.write_permissions
        agent_map = template_agent_tier_map(template) if tier == "coordinator" else None
    else:
        permissions = None  # use WRITE_PERMISSIONS default
        agent_map = AGENT_TIER_MAP if tier == "coordinator" else None

    # PreToolUse: write boundary enforcement
    # The pre-hook resolves agent_type from subagent context and enforces
    # per-tier permissions. Unknown agent types are DENIED (fail-closed).
    pre_hook = _make_pre_hook(
        tier, project_dir,
        milestone=milestone, agent_tier_map=agent_map, permissions=permissions,
    )

    hooks = {
        "PreToolUse": [
            HookConfig(matcher="Write|Edit|MultiEdit", hooks=[pre_hook])
        ],
    }

    # PostToolUse: composition validation (coordinators only)
    if tier == "coordinator":
        post_hook = _make_post_hook(project_dir)
        hooks["PostToolUse"] = [
            HookConfig(matcher="Write|Edit|MultiEdit", hooks=[post_hook])
        ]

    return hooks
```

## Call Graph Validation

The orchestrator validates `compose.py` via Python's `ast` module. This is the structural enforcement layer — the coordinator writes a call graph, the orchestrator type-checks it.

**Module:** `graph.py` (~100 lines)

Validates:
1. **Well-formedness** — valid Python syntax
2. **Completeness** — every called function is defined
3. **Acyclicity** — no circular dependencies
4. **Type compatibility** — output types match downstream input types
5. **Convergence** — `execute()` entry point exists

See [DB-02](../decision-boundaries/02-task-dag-implementation.md) for the full decision and implementation.

## Cost Tracking

```python
# Global token tracker (DB-06: track in tokens, not USD)
tokens = {
    "supervisor": {"input": 0, "output": 0},
    "coordinators": {},
    "total": {"input": 0, "output": 0},
}

def track_tokens(msg, tier="supervisor", milestone=None):
    if isinstance(msg, ResultMessage) and msg.usage:
        input_t = msg.usage.get("input_tokens", 0)
        output_t = msg.usage.get("output_tokens", 0)
        tokens["total"]["input"] += input_t
        tokens["total"]["output"] += output_t
        if tier == "supervisor":
            tokens["supervisor"]["input"] += input_t
            tokens["supervisor"]["output"] += output_t
        elif milestone:
            tokens["coordinators"].setdefault(
                milestone, {"input": 0, "output": 0}
            )
            tokens["coordinators"][milestone]["input"] += input_t
            tokens["coordinators"][milestone]["output"] += output_t

def context_exhausted(msg, threshold=0.75):
    """Check if a single cycle is approaching context limit.

    This is an edge case handler, not the primary compaction mechanism.
    The primary mechanism is session-per-cycle — each cycle starts fresh.
    Mid-cycle exhaustion signals that a phase is too large and should be
    decomposed, but we handle it gracefully by checkpointing and restarting.
    """
    if isinstance(msg, ResultMessage) and msg.usage:
        input_tokens = msg.usage.get("input_tokens", 0)
        # Opus context: 200K tokens
        return input_tokens > 200_000 * threshold
    return False


async def git_commit_phase(
    project_dir: Path, milestone: str, checkpoint_path: Path
):
    """Coordinator-only commit at phase completion boundary.

    Agent teams write code but do not commit. The coordinator reviews
    execution.md and code changes, then commits tractable deltas —
    logically coherent changes focused on the implementation.
    No conversation artifacts or intermediate states.
    """
    checkpoint = parse_checkpoint(checkpoint_path.read_text())
    phase = checkpoint.current_phase
    # Commit all changes from this phase
    # (implementation detail — subprocess git commands)


def validate_golden_context(project_dir: Path, milestone: str) -> list[str]:
    """Validate golden context structure after a cycle.

    Checks form, not content — the orchestrator doesn't judge whether
    decisions are good, only whether files are well-formed.
    """
    errors = []
    clou_dir = project_dir / ".clou"
    milestone_dir = clou_dir / "milestones" / milestone

    # Check coordinator checkpoint structure
    checkpoint = clou_dir / "active" / "coordinator.md"
    if checkpoint.exists():
        content = checkpoint.read_text()
        for required in ["## Cycle", "## Phase Status"]:
            if required not in content:
                errors.append(f"active/coordinator.md missing '{required}'")

    # Check that referenced phase files exist
    # (expand based on DB-08 schema definitions)

    return errors


async def git_revert_golden_context(project_dir: Path, milestone: str):
    """Revert golden context files to pre-cycle state.

    Used when structural validation fails. Reverts .clou/ files
    to their state at the previous cycle boundary.
    """
    # git checkout HEAD -- .clou/active/ .clou/milestones/<milestone>/
    pass


async def write_cycle_limit_escalation(
    project_dir: Path, milestone: str, cycle_count: int
):
    """Write escalation when 20-cycle limit is reached."""
    pass


async def write_agent_crash_escalation(project_dir: Path, milestone: str):
    """Write escalation when agent team crashes.

    Preserves execution.md entries, reports to supervisor.
    """
    pass


async def write_validation_escalation(
    project_dir: Path, milestone: str, errors: list[str]
):
    """Write escalation after 3 consecutive validation failures."""
    pass
```

## Prompt Loading

Clou uses a two-layer prompt architecture (DB-04, decided). The system prompt is a small identity + invariants template (~800–2,000 tokens). The full protocol lives in separate files the agent reads as its first action. This is grounded in research: instruction density degrades past a threshold, decomposition outperforms monolithic prompts, and system prompts have no architectural privilege over read content. See [Research Foundations](../research-foundations.md).

Prompts are **bundled with the package** in `clou/_prompts/`, not loaded from the project's `.clou/prompts/`. The `.clou/prompts/` directory holds project-local copies for reference, but the orchestrator always reads from the bundled directory to prevent drift between Clou versions and project-local copies.

```python
#: Bundled prompt templates shipped with the package — global, not per-project.
_BUNDLED_PROMPTS = Path(__file__).parent / "_prompts"


def load_prompt(tier: str, project_dir: Path, **kwargs) -> str:
    """Load and parameterize a tier's system prompt (identity + invariants only).

    Prompts are global — loaded from the bundled _prompts/ directory,
    not from the project's .clou/prompts/.
    """
    prompt_path = _BUNDLED_PROMPTS / f"{tier}-system.xml"
    prompt = prompt_path.read_text()

    # Inject instance-specific context ({{milestone}}, {{phase}}, etc.)
    for key, value in kwargs.items():
        prompt = prompt.replace(f"{{{{{key}}}}}", value)

    return prompt
```

## Agent Team Definitions

```python
def _build_agents(
    project_dir: Path, milestone: str,
    template: HarnessTemplate | None = None,
) -> dict[str, AgentDefinition]:
    """Build AgentDefinition dict for coordinator's agent teams.

    When template is provided, agent definitions are derived from the
    template's agents dict. Otherwise falls back to the default
    software-construction template.

    Each agent gets a small system prompt (identity + invariants) via
    the prompt field, loaded from the bundled _prompts/ directory.
    The full protocol is in _prompts/<prompt_ref>.md, which the agent
    reads during execution.
    """
    if template is None:
        template = load_template("software-construction")

    return {
        name: AgentDefinition(
            description=spec.description,
            prompt=load_prompt(spec.prompt_ref, project_dir, milestone=milestone),
            tools=spec.tools,
            model=spec.model,
        )
        for name, spec in template.agents.items()
    }
```

Template loading and MCP server conversion live in `clou/harness.py` (not in the orchestrator):

```python
# clou/harness.py

def read_template_name(project_dir: Path) -> str:
    """Read the template name from project.md.

    Looks for a 'template: <name>' line. Returns
    'software-construction' if not found or malformed.
    """
    ...

def load_template(name: str) -> HarnessTemplate:
    """Load a harness template by name.

    Validates name against _TEMPLATE_NAME_RE (import injection defense),
    imports clou.harnesses.<name>, validates the template, and falls
    back to the software-construction default on any failure (D9).
    Three-tier fallback: module → software_construction import → _INLINE_FALLBACK.
    """
    ...

def template_mcp_servers(template: HarnessTemplate) -> dict[str, Any]:
    """Convert template MCP server specs to SDK-compatible dicts."""
    ...

def template_agent_tier_map(template: HarnessTemplate) -> dict[str, str]:
    """Derive agent-name-to-tier mapping from template agents."""
    ...
```

## File Structure

```
clou/                              # Orchestrator Python code
├── orchestrator.py                 # Entry point, session lifecycle management
├── harness.py                      # Template dataclasses, loader, validator (DB-11)
├── harnesses/                      # Harness templates (ship with package)
│   ├── __init__.py
│   └── software_construction.py    # First template (extracted from hardcoded config)
├── _prompts/                       # Bundled prompt templates (orchestrator reads these)
│   ├── *-system.xml                # System prompts (identity + invariants)
│   └── *.md                        # Protocol files (agents read during execution)
├── prompts.py                      # System prompt loading from _prompts/
├── hooks.py                        # Write boundary enforcement + composition validation
├── graph.py                        # Call graph parsing and validation (~100 lines)
├── tools.py                        # Custom MCP tool definitions
├── tokens.py                       # Token usage tracking
├── recovery.py                     # Checkpoint reading, crash recovery, escalation writers
├── validation.py                   # Golden context structural validation
└── utils.py                        # Shared utilities

.clou/prompts/                     # Project-local prompt copies (reference only)
├── supervisor-system.xml           # Supervisor system prompt (~1,500-2,000 tokens)
├── supervisor.md                   # Supervisor protocol (agent reads)
├── coordinator-system.xml          # Coordinator system prompt (~800-1,200 tokens)
├── coordinator-plan.md             # PLAN cycle protocol
├── coordinator-execute.md          # EXECUTE cycle protocol
├── coordinator-assess.md           # ASSESS cycle protocol
├── coordinator-verify.md           # VERIFY cycle protocol
├── coordinator-exit.md             # EXIT cycle protocol
├── worker-system.xml               # Worker system prompt (~400-600 tokens)
├── worker.md                       # Worker protocol
├── verifier-system.xml             # Verifier system prompt (~600-800 tokens)
└── verifier.md                     # Verifier protocol
```

## What the Orchestrator is NOT

- **Not a UI** — the user never sees it; they see the supervisor session
- **Not a decision-maker** — it doesn't reason about what to build
- **Not an LLM session** — it's Python code managing LLM sessions
- **Not complex** — lifecycle management and plumbing, estimated ~500-800 lines
- **Not the product** — the golden context, protocols, and prompts are the product; the orchestrator is infrastructure
