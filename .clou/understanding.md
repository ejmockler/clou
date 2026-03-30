# Understanding

Durable conceptual memory. Each entry traces to a specific
user response via ask_user — never updated silently.

## What this project is becoming

## Active tensions

### Planner defaults to narrow/deep task graphs
- **Asked:** (user-initiated) User observed task graphs are consistently 2 nodes — one implement node connected to one verify node.
- **Response:** Confirmed by milestone data (M13-M15 all 2-task compose.py). Identified two layers: supervisor scopes milestones too narrowly, and planner has no width guidance. Research (LAMaS 2025) confirms controllers default to deep topologies without explicit critical-path supervision.
- **Framing:** The fix is in the planner prompt (coordinator-plan.md) — add width-aware decomposition guidance — and in the research foundations — ground the guidance in published findings. Supervisor scoping discipline is a separate behavioral change.
- **When:** 2026-03-29

## Continuity

## Resolved
