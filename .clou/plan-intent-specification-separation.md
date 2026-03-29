# Implementation Plan: Intent-Specification Separation (DB-14)

**Decision:** DB-14 — Introduce `intents.md` and `ArtifactForm` as first-class harness concept
**Approach:** Implementation → review cycles, one phase at a time

## Task Graph

Dependencies flow top-to-bottom. Tasks within a phase are independent unless noted.

### Phase 1: Schema (ArtifactForm in harness)

**1.1 — Add ArtifactForm dataclass to harness.py**
- File: `clou/harness.py`
- Add `ArtifactForm` frozen dataclass with fields: `sections`, `criterion_template`, `anti_patterns`
- Add `artifact_forms: dict[str, ArtifactForm]` to `HarnessTemplate`
- Default: empty dict (backwards-compatible)
- Update `validate_template()` to check artifact_forms reference valid form names

**1.2 — Define intents form in software-construction template**
- File: `clou/harnesses/software_construction.py`
- Add `artifact_forms` to the template:
  ```python
  artifact_forms={
      "intents": ArtifactForm(
          sections=(),
          criterion_template="When {trigger}, {observable_outcome}",
          anti_patterns=(
              "file paths or module names as criterion subject",
              "implementation verbs (extract, refactor, build) as criterion",
              "criteria verifiable by file inspection alone",
          ),
      ),
  }
  ```
- Add `"milestones/*/intents.md"` to supervisor write_permissions
- Update `_INLINE_FALLBACK` in harness.py to match

**1.3 — Update hooks.py write permissions**
- File: `clou/hooks.py`
- Add `"milestones/*/intents.md"` to `WRITE_PERMISSIONS["supervisor"]`

### Phase 2: Tooling (milestone creation)

Depends on: Phase 1

**2.1 — Update clou_create_milestone() to create intents.md**
- File: `clou/tools.py`
- Add `intents_content: str` parameter to `clou_create_milestone()`
- Create `intents.md` alongside milestone.md and requirements.md
- Update return message to include intents.md

**2.2 — Update orchestrator tool registration**
- File: `clou/orchestrator.py`
- Update the tool definition schema for `clou_create_milestone` to include `intents_content` parameter
- Update tool description

### Phase 3: Read sets (cycle routing)

Depends on: Phase 1

**3.1 — Update determine_next_cycle() read sets**
- File: `clou/recovery.py`
- PLAN: add `"intents.md"` → `["milestone.md", "requirements.md", "intents.md", "project.md"]`
- VERIFY: replace `"requirements.md"` with `"intents.md"` → `["status.md", "intents.md", "compose.py", "active/coordinator.md"]`
- VERIFY (convergence override): same replacement
- ASSESS: keep `"requirements.md"` (implementation constraints relevant to assessment)
- Update ALL PLAN fallback paths (lines 187, 193, 201, 236, 263)

### Phase 4: Prompts (agent directives)

Depends on: Phase 3

**4.1 — Update supervisor.md crystallization step**
- File: `clou/_prompts/supervisor.md`
- Add intents.md to step 4 (crystallize):
  ```
  - .clou/milestones/{name}/intents.md — observable outcomes only.
    Each criterion: "When [trigger], [observable outcome]."
    NOT implementation artifacts. NOT file structure. What a person
    standing outside the system sees when this milestone succeeds.
  ```
- Update step 5 to mention intents as key acceptance criteria

**4.2 — Update coordinator-plan.md**
- File: `clou/_prompts/coordinator-plan.md`
- Add: "3. Read intents.md for observable outcomes — these orient your decomposition."
- Existing: "2. Read requirements.md for acceptance criteria" → "2. Read requirements.md for implementation constraints."

**4.3 — Update coordinator-verify.md**
- File: `clou/_prompts/coordinator-verify.md`
- Replace all `requirements.md` references with `intents.md` for acceptance criteria
- Line 14: "Compare perceptual record against intents.md observable outcomes"
- Line 18: "Observable outcomes from intents.md"
- Line 20: "Evaluate quality gate findings against intents.md scope"

**4.4 — Update coordinator-assess.md**
- File: `clou/_prompts/coordinator-assess.md`
- Keep requirements.md references (implementation constraints relevant to assessment)
- Add: reference intents.md for understanding whether assessment findings threaten observable outcomes

**4.5 — Update verifier.md**
- File: `clou/_prompts/verifier.md`
- Replace requirements.md reads with intents.md
- Line 31: "Read intents.md — observable outcomes for golden path criteria"

**4.6 — Update system prompts**
- Files: `clou/_prompts/supervisor-system.xml`, `coordinator-system.xml`
- Add intents.md to golden context references where appropriate

**4.7 — Update coordinator-exit.md**
- File: `clou/_prompts/coordinator-exit.md`
- If it references requirements.md for handoff evaluation, consider adding intents.md

### Phase 5: Validation (form checking)

Depends on: Phase 1

**5.1 — Add intents form validation to validation.py**
- File: `clou/validation.py`
- Add `_validate_intents(path: Path) -> list[ValidationFinding]`
- Narrative tier: form-only, no content validation
- Check: at least one criterion present (WARNING if empty)
- Check: criteria match "When ... , ..." pattern (WARNING if not)
- Check: no file path patterns detected in criteria (WARNING)
- Call from `validate_golden_context()` if intents.md exists

### Phase 6: Tests

Depends on: Phases 1-5

**6.1 — Update test_recovery.py**
- All PLAN read set assertions: add "intents.md"
- All VERIFY read set assertions: replace "requirements.md" with "intents.md"
- Convergence override VERIFY: same replacement

**6.2 — Update test_tools.py**
- `clou_create_milestone()` tests: add intents_content parameter
- Assert intents.md is created with correct content
- Assert return message includes intents.md

**6.3 — Add test_validation.py intents tests**
- Test: valid intents.md with behavioral criteria → no findings
- Test: empty intents.md → WARNING
- Test: criteria without "When" pattern → WARNING
- Test: file paths in criteria → WARNING

**6.4 — Update test_orchestrator.py**
- VERIFY cycle mock setup: intents.md instead of requirements.md

**6.5 — Add harness validation tests**
- Test: ArtifactForm in template → validates correctly
- Test: template without artifact_forms → valid (backwards-compatible)

### Phase 7: Knowledge base alignment

Depends on: All prior phases

**7.1 — Update knowledge-base protocol docs**
- Files: `knowledge-base/protocols/coordinator.md`, `verification.md`, `supervisor.md`
- Update per-cycle read sets, artifact references
- Add intents.md to coordinator protocol's PLAN reads

**7.2 — Update knowledge-base integration docs**
- File: `knowledge-base/integration/orchestrator.md`
- Update per-cycle read set tables

**7.3 — Update affected decision boundaries**
- DB-07: Add intents.md to file tree, update per-cycle read set table
- DB-09: Verifier reads intents.md for golden path criteria
- DB-11: Note ArtifactForm extension to HarnessTemplate

**7.4 — Sync .clou/prompts/ project-local copies**
- Copy updated bundled prompts to `.clou/prompts/` for reference

## Review Checkpoints

After each phase, review:
1. Do tests pass? (run `pytest tests/ -x -q --ignore=tests/test_tools.py`)
2. Does the import chain work? (`python -c "from clou.harness import ArtifactForm"`)
3. Is the change backwards-compatible? (existing milestones without intents.md don't break)
4. Does the change amplify the principle? (form shapes cognition, not compliance)

## Backwards Compatibility

- `intents.md` is optional in `validate_golden_context()` — validated only if it exists
- `determine_next_cycle()` includes intents.md in read sets; the prompt builder skips missing files
- `clou_create_milestone()` adds `intents_content` parameter — callers without it get an error (supervisor prompt updated to always provide it)
- `ArtifactForm` field defaults to empty dict — existing templates work unchanged
