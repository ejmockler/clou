# Clou Orchestrator

The orchestrator is the thin Python layer that manages Clou's session lifecycles via the Claude Agent SDK. It is invisible plumbing — the user interacts with the supervisor session. The orchestrator spawns, monitors, restarts, and cleans up sessions.

## Responsibilities

1. **Start the supervisor session** with the correct prompt, tools, hooks, and MCP servers
2. **Spawn coordinator sessions** when the supervisor requests (via custom MCP tool)
3. **Monitor sessions** for context exhaustion, errors, and completion
4. **Enforce write boundaries** via PreToolUse hooks running in-process
5. **Track token usage** per session and cumulative
6. **Handle crashes** by restarting from golden context checkpoints
7. **Provide custom MCP tools** to sessions (clou-specific tools running in-process)

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

    options = ClaudeAgentOptions(
        system_prompt=load_prompt("supervisor", project_dir),
        tools={"type": "preset", "preset": "claude_code"},
        permission_mode="acceptEdits",
        cwd=str(project_dir),
        model="opus",
        hooks=build_hooks("supervisor", project_dir),
        mcp_servers={
            "clou": clou_server,
            "brutalist": brutalist_mcp_config(),
        },
    )

    async with ClaudeSDKClient(options) as supervisor:
        # Initial prompt: read protocol, then checkpoint or start fresh
        checkpoint = project_dir / ".clou" / "active" / "supervisor.md"
        if checkpoint.exists():
            await supervisor.query(
                "Read your protocol file: .clou/prompts/supervisor.md\n\n"
                "Then resume from checkpoint. Read .clou/active/supervisor.md "
                "and .clou/roadmap.md to reconstruct your state."
            )
        else:
            await supervisor.query(
                "Read your protocol file: .clou/prompts/supervisor.md\n\n"
                "Then read .clou/project.md for context. "
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

    clou_dir = project_dir / ".clou"
    checkpoint_path = clou_dir / "active" / "coordinator.md"
    validation_retries = 0  # Track consecutive validation failures
    MAX_VALIDATION_RETRIES = 3
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

        # Build targeted prompt for this cycle
        prompt = build_cycle_prompt(
            project_dir, milestone, cycle_type, read_set
        )

        status = await run_single_cycle(project_dir, milestone, prompt)

        if status == "failed":
            # Crash mid-cycle — golden context has last cycle boundary state
            # Retry the same cycle (checkpoint hasn't advanced)
            continue

        if status == "agent_team_crash":
            # Agent team crashed — preserve execution.md, escalate to supervisor
            await write_agent_crash_escalation(project_dir, milestone)
            return "escalated_agent_crash"

        # Validate golden context structure after cycle
        validation_errors = validate_golden_context(project_dir, milestone)
        if validation_errors:
            validation_retries += 1
            if validation_retries >= MAX_VALIDATION_RETRIES:
                await write_validation_escalation(
                    project_dir, milestone, validation_errors
                )
                return "escalated_validation"
            # Revert golden context to pre-cycle state and retry
            await git_revert_golden_context(project_dir, milestone)
            prompt = build_cycle_prompt(
                project_dir, milestone, cycle_type, read_set,
                validation_errors=validation_errors,
            )
            continue
        else:
            validation_retries = 0  # Reset on success

        # Coordinator-only commit at phase completion
        if status == "phase_completed":
            await git_commit_phase(project_dir, milestone, checkpoint_path)

    return "completed"


async def run_single_cycle(
    project_dir: Path, milestone: str, prompt: str
) -> str:
    """Run one coordinator cycle as a fresh session."""

    options = ClaudeAgentOptions(
        system_prompt=load_prompt("coordinator", project_dir, milestone=milestone),
        tools={"type": "preset", "preset": "claude_code"},
        permission_mode="bypassPermissions",
        cwd=str(project_dir),
        model="opus",
        agents=build_agent_definitions(project_dir, milestone),
        hooks=build_hooks("coordinator", project_dir),
        mcp_servers={
            "brutalist": brutalist_mcp_config(),
            "cdp": cdp_mcp_config(),
        },
        max_turns=200,  # Safety limit per cycle
    )

    try:
        async with ClaudeSDKClient(options) as coordinator:
            await coordinator.query(prompt)

            async for msg in coordinator.receive_response():
                track_coordinator_progress(msg, milestone)

                # Mid-cycle context exhaustion — force checkpoint and restart
                if isinstance(msg, ResultMessage) and context_exhausted(msg):
                    await coordinator.query(
                        "Context approaching limit. Write a mid-cycle checkpoint "
                        "to active/coordinator.md with partial progress, then exit."
                    )
                    return "exhausted"  # Loop will restart same cycle

                # Detect agent team crashes via SDK notifications
                if is_agent_team_crash(msg):
                    await coordinator.query(
                        "Agent team member crashed. Preserve all execution.md "
                        "entries. Do NOT retry. Write checkpoint and exit."
                    )
                    return "agent_team_crash"

                # Detect Brutalist unavailability during ASSESS
                if is_brutalist_unavailable(msg):
                    await coordinator.query(
                        "Brutalist MCP is unavailable. Write a blocking "
                        "escalation with the error details, then exit."
                    )
                    return "brutalist_unavailable"

        return read_cycle_outcome(project_dir, milestone)

    except Exception as e:
        log_error(f"Coordinator cycle crashed for {milestone}: {e}")
        return "failed"


def determine_next_cycle(
    checkpoint_path: Path, milestone: str
) -> tuple[str, list[str]]:
    """Read checkpoint and determine what the next cycle should do."""

    if not checkpoint_path.exists():
        return "PLAN", ["milestone.md", "requirements.md", "project.md"]

    checkpoint = parse_checkpoint(checkpoint_path.read_text())

    match checkpoint.next_step:
        case "PLAN":
            return "PLAN", ["milestone.md", "requirements.md", "project.md"]
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
                "status.md", "requirements.md", "compose.py",
                "active/coordinator.md",
            ]
        case "EXIT":
            return "EXIT", [
                "status.md", "handoff.md", "decisions.md",
                "active/coordinator.md",
            ]
        case "COMPLETE":
            return "COMPLETE", []

    return "PLAN", ["milestone.md", "requirements.md", "project.md"]


def build_cycle_prompt(
    project_dir: Path, milestone: str,
    cycle_type: str, read_set: list[str],
    validation_errors: list[str] | None = None,
) -> str:
    """Construct targeted prompt for a single cycle.

    The system_prompt contains identity + invariants only (~800-1,200 tokens).
    This initial query provides: cycle type, protocol file pointer, and
    golden context file pointers. The coordinator reads the protocol file
    as its first action, then the golden context files.

    If validation_errors is provided, the prompt includes feedback about
    the previous cycle's malformed golden context writes.
    """

    milestone_prefix = f".clou/milestones/{milestone}"
    file_list = "\n".join(
        f"- {milestone_prefix}/{f}" if not f.startswith("project.md")
        else f"- .clou/{f}"
        for f in read_set
    )
    protocol_file = f".clou/prompts/coordinator-{cycle_type.lower()}.md"

    prompt = (
        f"This cycle: {cycle_type}.\n\n"
        f"Read your protocol file first:\n- {protocol_file}\n\n"
        f"Then read these golden context files:\n{file_list}\n\n"
        f"Execute the {cycle_type} protocol. "
        f"Write all state to golden context before exiting."
    )

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


def build_verifier_tools(project_dir: Path, milestone: str) -> list[str]:
    """Build verifier tool list based on verification modalities (DB-09).

    The coordinator's compose.py encodes which modalities are needed.
    Browser modality adds CDP MCP tools. All modalities get Bash.
    Tools the verifier won't use are distractors (Research §1).
    """
    base_tools = ["Read", "Write", "Bash", "Grep", "Glob", "LS",
                  "WebSearch", "WebFetch"]

    modalities = read_verification_modalities(project_dir, milestone)
    if "browser" in modalities:
        base_tools.extend([
            "mcp__cdp__navigate",
            "mcp__cdp__screenshot",
            "mcp__cdp__accessibility_snapshot",
            "mcp__cdp__evaluate_javascript",
            "mcp__cdp__click",
            "mcp__cdp__type",
            "mcp__cdp__network_get_response_body",
            "mcp__cdp__console_messages",
        ])

    return base_tools
```

## Hook Enforcement

The orchestrator enforces write boundaries via PreToolUse hooks:

```python
def build_hooks(tier: str, project_dir: Path) -> dict:
    """Build tier-specific hooks for write boundary enforcement."""

    clou_dir = project_dir / ".clou"

    # Define which paths each tier can write to
    write_permissions = {
        "supervisor": [
            "project.md", "roadmap.md", "requests.md",
            "milestones/*/milestone.md",  # creation only
            "milestones/*/requirements.md",
            "milestones/*/escalations/*.md",  # disposition only
            "active/supervisor.md",
        ],
        "coordinator": [
            "milestones/*/compose.py",
            "milestones/*/status.md",
            "milestones/*/decisions.md",
            "milestones/*/escalations/*.md",  # creation
            "milestones/*/phases/*/phase.md",
            "active/coordinator.md",
        ],
        "worker": [
            "milestones/*/phases/*/execution.md",
            # Plus any codebase files (not in .clou/)
        ],
        "verifier": [
            "milestones/*/phases/verification/execution.md",
            "milestones/*/handoff.md",
        ],
    }

    async def enforce_write_boundary(input_data):
        tool_name = input_data.get("tool_name", "")
        if tool_name not in ("Write", "Edit", "MultiEdit"):
            return {}

        file_path = input_data.get("tool_input", {}).get("file_path", "")

        # Allow all writes outside .clou/
        if str(clou_dir) not in file_path:
            return {}

        # Check against tier's write permissions
        relative = str(Path(file_path).relative_to(clou_dir))
        if not path_matches_patterns(relative, write_permissions[tier]):
            return {
                "hookSpecificOutput": {
                    "permissionDecision": "deny",
                    "reason": f"{tier} tier cannot write to .clou/{relative}"
                }
            }
        return {}

    # Composition validation (PostToolUse)
    async def validate_composition(input_data):
        """Validate compose.py call graph after coordinator writes it."""
        file_path = input_data.get("tool_input", {}).get("file_path", "")
        if not file_path.endswith("compose.py"):
            return {}

        code = Path(file_path).read_text()
        errors = graph.validate(code)
        if errors:
            return {"hookSpecificOutput": {
                "message": "Composition errors:\n"
                    + "\n".join(f"  - {e}" for e in errors)
                    + "\nFix the call graph."
            }}
        return {}

    hooks = {
        "PreToolUse": [
            HookMatcher(matcher="Write|Edit|MultiEdit",
                       hooks=[enforce_write_boundary])
        ],
    }

    # Only add composition validation for coordinators
    if tier == "coordinator":
        hooks["PostToolUse"] = [
            HookMatcher(matcher="Write|Edit|MultiEdit",
                       hooks=[validate_composition])
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

```python
def load_prompt(tier: str, project_dir: Path, **kwargs) -> str:
    """Load and parameterize a tier's system prompt (identity + invariants only).

    System prompts are small XML templates. The full protocol is in separate
    files the agent reads during execution — pointed to by build_cycle_prompt().
    """
    prompt_path = project_dir / ".clou" / "prompts" / f"{tier}-system.xml"
    prompt = prompt_path.read_text()

    # Inject instance-specific context ({{milestone}}, {{phase}}, etc.)
    for key, value in kwargs.items():
        prompt = prompt.replace(f"{{{{{key}}}}}", value)

    return prompt
```

## Agent Team Definitions

```python
def build_agent_definitions(project_dir: Path, milestone: str) -> dict:
    """Build AgentDefinition dict for coordinator's agent teams.

    Each agent gets a small system prompt (identity + invariants) via
    the prompt field. The full protocol is in .clou/prompts/worker.md
    or .clou/prompts/verifier.md, which the agent reads during execution.
    """
    return {
        "implementer": AgentDefinition(
            description=(
                "Implement code changes for assigned tasks. "
                "Read compose.py for your function signature, phase.md for context. "
                "Write results to execution.md."
            ),
            prompt=load_prompt("worker", project_dir, milestone=milestone),
            tools=["Read", "Write", "Edit", "MultiEdit", "Bash",
                   "Grep", "Glob", "LS", "WebSearch", "WebFetch"],
            model="opus",  # DB-06: Opus everywhere
        ),
        "verifier": AgentDefinition(
            description=(
                "Verify milestone completion by perceiving the software as a user would. "
                "Materialize the dev environment, walk golden paths with perceptual "
                "record capture, explore adversarially, and prepare handoff.md."
            ),
            prompt=load_prompt("verifier", project_dir, milestone=milestone),
            tools=build_verifier_tools(project_dir, milestone),  # DB-09: modality-based
            model="opus",  # DB-06: Opus everywhere
        ),
    }
```

## File Structure

```
clou/                              # Orchestrator Python code
├── orchestrator.py                 # Entry point, session lifecycle management
├── prompts.py                      # System prompt loading (small XML templates)
├── hooks.py                        # Write boundary enforcement + composition validation
├── graph.py                        # Call graph parsing and validation (~100 lines)
├── tools.py                        # Custom MCP tool definitions
├── tokens.py                       # Token usage tracking
├── recovery.py                     # Checkpoint reading, crash recovery, escalation writers
├── validation.py                   # Golden context structural validation
└── utils.py                        # Shared utilities

.clou/prompts/                     # Prompt files (part of golden context)
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
