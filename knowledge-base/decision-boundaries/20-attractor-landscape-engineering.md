# DB-20: Attractor Landscape Engineering

**Status:** PROPOSED — Engineering trajectory for transformer-dynamics-aware orchestration
**Severity:** High — affects reliability at long horizons
**Proposed:** 2026-04-10

## Decision

**Engineer the orchestrator's context assembly as an energy landscape problem, not a file-inclusion problem.** The read set defines the attractor basins of each coordinator session. Context curation determines which behavioral patterns the transformer retrieves, which determines orchestration quality. Eight engineering steps build toward a system that treats transformer dynamics as first-class design constraints.

This decision is informed by first-principles analysis of transformer attention mechanics (§2 Hopfield equivalence), compositionality limits (§9), serial fabrication (§19), long-horizon reliability research (§17), autonomous delivery patterns (§18), and identified gaps in transformer dynamics for agent architecture (§19).

## The Engineering Trajectory

### Step 1: Instrument Cognitive Metrics

Before engineering dynamics, measure them. Add to cycle telemetry:

- **Read set composition**: files, sizes, ratio of read-set tokens to output tokens
- **Reference density**: grep coordinator output for references to each read set file. Files read but never referenced are dead context — energy landscape flatteners.
- **Compositional span**: how many distinct source files does a single coordinator judgment integrate? Track per-decision.

Correlate: read set size vs. rework rate, compositional span vs. judgment quality, reference density vs. cycles to convergence.

Implementation: ~50-100 lines in telemetry.py and hooks.py. No architectural change.

### Step 2: Reduce ASSESS to Compositionality Boundary

§9 shows compositionality breaks at 2 hops. ASSESS currently reads 5+ files = 5-hop composition. Reduce to 2-hop maximum.

The two-dispatch protocol distributes composition:
- Brutalist reads execution.md + compose.py (2 hops) → assessment.md
- Evaluator reads assessment.md + pre-composed reference (2 hops) → classified findings
- Coordinator reads classified findings + prior reasoning (2 hops) → routing

Pre-compose the evaluator's reference mechanically: concatenate relevant requirements and intents sections. No LLM needed.

Remove compose.py, execution.md, and requirements.md from the coordinator's ASSESS read set. These are consumed by dispatched agents.

Implementation: read set change in recovery_checkpoint.py + prompt update to coordinator-assess.md.

### Step 3: Shape Read Sets as Attractor Landscapes

Use instrumentation data from step 1 to build scored read sets:
- Files that consistently influence correct decisions (high reference density + low rework) → always included
- Files that rarely influence decisions but add tokens (low reference density) → removed
- Files whose presence correlates with incorrect decisions → restructured

Rule-based initially, informed by accumulated telemetry. Extends DB-18's memory retrieval scoring to the full read set.

### Step 4: Sub-Cycle Verification

Exploit the verification asymmetry: tests run in seconds, ASSESS takes minutes.

Workers write structured test-status artifacts incrementally:
```
# phases/{phase}/test-status.md
last_run: 2026-04-10T12:15:00Z
passing: 347
failing: 2
new_failures:
- test_name: error_message
```

Orchestrator monitors at sub-minute frequency (hook on worker tool use). Circuit breaker on consecutive failing runs.

Implementation: worker prompt update + orchestrator monitoring hook.

### Step 5: Two-Phase Planning

Separate task identification from topology determination (§19, §19):

Phase 1: "List every distinct unit of work as a flat, UNORDERED set. No dependencies."
Phase 2: "Analyze the list for independence. For each pair: does B need A's output?"

Self-consistency check: validator compares independence claims (phase 1) against topology (phase 2).

Implementation: coordinator-plan.md prompt restructuring + validator addition to graph.py.

### Step 6: Information Channel Capacity Measurement

Measure end-to-end intent survival. For each intent in intents.md, trace through:
- compose.py (covered?) → phase.md (referenced?) → execution.md (mentioned?) → assessment.md (evaluated?) → handoff.md (confirmed?)

Each step: binary present/absent. Product = intent survival rate. Identify bottleneck channels.

Implementation: new telemetry function + post-milestone analysis.

### Step 7: Cross-Milestone Typed Dependencies

Express milestone relationships as a typed graph (roadmap.py):

```python
async def memory_lifecycle(recovery: RecoveryPipeline) -> MemorySystem:
    """All DB-18 memory mechanisms operational."""

async def execute():
    recovery, graph = await gather(
        build_recovery_pipeline(),
        build_graph_validator(),
    )
    memory, topology = await gather(
        memory_lifecycle(recovery),
        topology_integration(graph),
    )
```

Same validator, same execution model, same representation at every level.

### Step 8: Training Distribution Distance as Steering Signal

Use validation retry count as a proxy for training distribution distance:
- Many retries = model struggles to produce valid topology = far from familiar territory
- Correlate with downstream rework rates

When distance is high: decompose more conservatively, reduce gather() sizes, add intermediate verification.

Implementation: telemetry correlation + adaptive read set enrichment.

## Dependencies Between Steps

| Step | Depends On | Creates |
|------|-----------|---------|
| 1. Instrument | — | Measurement data for steps 3, 6, 8 |
| 2. ASSESS reduction | — | Immediate reliability improvement |
| 3. Scored read sets | 1 | Attractor landscape shaping |
| 4. Sub-cycle verification | — | Faster feedback loops |
| 5. Two-phase planning | — | Better topologies |
| 6. Channel capacity | 1 | Bottleneck identification |
| 7. Cross-milestone graph | 5 | Project-level topology |
| 8. Distribution steering | 1 | Adaptive decomposition |

Steps 1, 2, 4, 5 are independently actionable. Steps 3, 6, 8 require step 1's data. Step 7 extends step 5.

## Research Basis

- §2: Attention as Hopfield retrieval — mathematical foundation for attractor landscape model
- §9: Compositionality breaks at 2 hops — bounds orchestration complexity  
- §11: RPD + Hopfield equivalence — training distribution shapes prototype quality
- §19: Autoregressive edge hallucination — structural enforcement required
- §17: Reliability decay curves — decomposition is highest-leverage intervention
- §18: In-execution graph editing — graphs should evolve during execution
- §19: Transformer dynamics gaps — the 6 unexplored frontiers this trajectory addresses
