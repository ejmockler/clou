"""Recovery utilities -- re-exports for backward compatibility.

All implementation has moved to concern-specific modules:
  - recovery_checkpoint: Checkpoint parsing, cycle determination
  - recovery_escalation: Escalation file writers
  - recovery_selfheal: Self-heal logic and normalisation
  - recovery_git: Git subprocess operations
  - recovery_compaction: Decisions compaction and memory patterns
  - recovery_consolidation: Milestone consolidation and metrics parsing

Import from this module for the public API.
"""

from clou.recovery_checkpoint import (  # noqa: F401
    Checkpoint,
    ConvergenceState,
    _MEMORY_TYPE_FILTERS,
    _filter_memory_for_cycle,
    _safe_int,
    assess_convergence,
    determine_next_cycle,
    parse_checkpoint,
    read_cycle_count,
    read_cycle_outcome,
    validate_milestone_name,
)
from clou.recovery_compaction import (  # noqa: F401
    MemoryPattern,
    _accumulate_distribution,
    _apply_decay,
    _consolidated_milestones,
    _detect_contradiction,
    _invalidate_contradictions,
    _milestone_sort_key,
    _parse_memory,
    _reinforce_or_create,
    _render_memory,
    compact_decisions,
    compact_understanding,
)
from clou.recovery_consolidation import (  # noqa: F401
    _analyze_compose,
    _count_metrics_section_rows,
    _parse_metrics_header,
    consolidate_milestone,
    consolidate_pending,
    parse_obsolete_flags,
    run_lifecycle_pipeline,
)
from clou.recovery_errors import (  # noqa: F401
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_COOLDOWN,
    ErrorKind,
    classify_error,
)
from clou.recovery_escalation import (  # noqa: F401
    write_agent_crash_escalation,
    write_cycle_limit_escalation,
    write_staleness_escalation,
    write_validation_escalation,
)
from clou.recovery_git import (  # noqa: F401
    _STAGING_EXCLUDE_PATTERNS,
    archive_milestone_episodic,
    git_commit_phase,
    git_revert_golden_context,
)
from clou.recovery_selfheal import (  # noqa: F401
    attempt_self_heal,
    log_self_heal_attempt,
)
