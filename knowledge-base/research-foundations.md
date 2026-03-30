# Research Foundations

How transformers digest context at scale, what works for multi-agent prompt architectures, and what current approaches are missing. This document grounds Clou's design decisions in research rather than intuition.

Last updated: 2026-03-25

## 1. Context Is Adversarial at Scale

The dominant industry narrative treats expanding context windows as unalloyed progress. Research decisively refutes this.

### Context Rot Is Universal

Chroma Research (2025) tested 18 frontier models — GPT-4.1, Claude Opus 4, Gemini 2.5, Qwen3 — and found that **every model exhibits measurable performance degradation at every input length increment tested**. Three compounding mechanisms:

1. **Positional bias** — RoPE creates a U-shaped attention curve. Models attend to beginning and end, losing the middle. Traced to rotary position embedding decay (MIT 2025). Architectural, not a training artifact.
2. **Attention dilution** — At 10K tokens: 100M pairwise relationships. At 100K: 10B. Attention probability mass spreads thinner; any individual relevant sentence becomes statistically insignificant against distractor mass.
3. **Distractor interference** — Semantically similar irrelevant content actively degrades performance. Models perform *better* with shuffled, incoherent haystacks than logically structured ones — structure introduces more confusable signals.

### Volume Alone Hurts Reasoning

Gao et al. (EMNLP 2025 Findings) tested GPT-4o, Claude 3.5 Sonnet, Gemini 2.0, Llama-3.1-8B, Mistral-v0.3-7B across math, QA, and coding. Even with ~97% retrieval accuracy (near-perfect), task performance dropped 13.9–85% as input length grew. When all irrelevant tokens were replaced with whitespace — eliminating distractors entirely — performance still dropped 7.9–50%.

**Implication:** The sheer token count degrades reasoning independent of content quality. More context is not better. The right context, and only the right context, is better.

### The Lost-in-the-Middle Phenomenon

Liu et al. (TACL 2024) showed LLMs exhibit a U-shaped performance curve: strong at beginning and end, weak in the middle. In 20-document QA, accuracy dropped 30%+ when the answer moved from position 1 to position 10. Follow-up work:

- **"Found in the Middle"** (ACL 2024 Findings, Google/MIT/UW): Calibration mechanism adjusting positional bias improved RAG by up to 15pp.
- **"Context Rot"** (Chroma 2025): Confirmed the effect persists universally across all 18 tested models.
- The problem is reduced but not eliminated in 2025/2026 frontier models.

### Clou Implication

Session-per-cycle (DB-03) sidesteps context degradation by starting each cycle with a fresh, targeted context. The per-cycle-type read sets ensure only relevant files enter context. Golden context files should be concise and structured — every unnecessary token is actively harmful.

**Sources:**
- Gao et al., "Context Length Alone Hurts LLM Performance Despite Perfect Retrieval," EMNLP 2025: [arXiv:2510.05381](https://arxiv.org/abs/2510.05381)
- Chroma Research, "Context Rot," 2025: [research.trychroma.com/context-rot](https://research.trychroma.com/context-rot)
- Liu et al., "Lost in the Middle," TACL 2024: [arXiv:2307.03172](https://arxiv.org/abs/2307.03172)
- "Found in the Middle," ACL 2024: [aclanthology.org/2024.findings-acl.890](https://aclanthology.org/2024.findings-acl.890/)

---

## 2. Attention Head Specialization

### Retrieval Heads vs. Streaming Heads

Not all attention is equal. Research reveals a clear functional split:

- **Retrieval heads** (small fraction): Handle long-range dependency tracking, require full KV cache across all tokens.
- **Streaming heads** (majority): Attend only to recent tokens + attention sinks (first ~4 tokens), need only constant-length KV cache.

DuoAttention (ICLR 2025, MIT/NVIDIA) exploits this split for 2.55x memory reduction, enabling 3.3M token context on a single A100. Infini-attention (Google 2024) found heads self-specialize after training into local-only, long-range-only, and mixer categories.

### The Attention Sink Phenomenon

LLMs assign disproportionate attention to the first token regardless of semantic content (StreamingLLM, ICLR 2024). ICLR 2025 follow-up traced the mechanism: inactive heads dump attention onto sink tokens while value states are actively drained. Removing sink tokens causes severe degradation.

**The first tokens in a prompt occupy an architecturally privileged position.** This is structural, emerging after sufficient optimization, and requires softmax-based normalization.

### MoA and Heterogeneous Attention

Mixture of Sparse Attention (CoLM 2025) automatically assigns different sliding-window lengths to different heads/layers. Some heads expand focus for longer inputs while others maintain fixed local windows. Increased effective context length by 3.9x at the same average window size.

### Attention as Associative Memory

The attention mechanism is not analogous to pattern retrieval — it IS pattern retrieval. Ramsauer et al. (2020) proved that transformer attention is mathematically equivalent to the update rule of a modern Hopfield network with continuous states. Hopfield networks store patterns and retrieve the nearest match given a partial cue. Each forward pass through attention retrieves stored patterns (from weights and from the context window) that best match the current query.

**Induction heads** are the specific circuit. Crosbie & Shutova (NAACL 2025 Findings) showed ablating 1-3% of induction heads degrades performance by up to 32% on abstract pattern tasks — reducing accuracy to near-random. These heads perform "fuzzy prefix matching": prototype-like similarity matching over distributed representations.

**In-context learning is implicit Bayesian inference.** Xie et al. (ICLR 2022) showed that when pretraining data has long-range coherence, the transformer infers a latent "situation type" from in-context examples — computing a Bayesian posterior in its forward pass. Confirmed by Zhang et al. (ICLR 2024). Extended by Reuter et al. (ICML 2025), who demonstrated transformers performing full posterior inference comparable to MCMC. Li Ji-An et al. (NeurIPS 2024) showed induction heads share behavioral, functional, and mechanistic parallels with human episodic memory (the Contextual Maintenance and Retrieval model).

### Clou Implication

The attention sink means the opening of any Clou prompt — the very first tokens — gets disproportionate architectural weight. The single most important behavioral constraint should come first, not be buried after role preamble. The middle of a long prompt is a dead zone.

The Hopfield equivalence is mechanistically illuminating but operationally equivalent to §1's conclusion: curated read sets per cycle shape what the model retrieves. The Hopfield framing explains *why* targeted context works (it selects the retrieval pattern library); the context degradation research (§1) explains why untargeted context fails (dilution and distraction). Both converge on the same design constraint: include only what's relevant.

**Sources:**
- Xiao et al., StreamingLLM / Attention Sinks, ICLR 2024: [arXiv:2309.17453](https://arxiv.org/abs/2309.17453)
- When Attention Sink Emerges, ICLR 2025: [openreview.net/forum?id=78Nn4QJTEN](https://openreview.net/forum?id=78Nn4QJTEN)
- DuoAttention, ICLR 2025: [arXiv:2410.10819](https://arxiv.org/abs/2410.10819)
- Infini-attention, Google 2024: [arXiv:2404.07143](https://arxiv.org/abs/2404.07143)
- MoA, CoLM 2025: [arXiv:2406.14909](https://arxiv.org/abs/2406.14909)
- Ramsauer et al., "Hopfield Networks is All You Need," 2020: [arXiv:2008.02217](https://arxiv.org/abs/2008.02217)
- Crosbie & Shutova, "Induction Heads as Essential Mechanism for Pattern Matching," NAACL 2025: [arXiv:2407.07011](https://arxiv.org/abs/2407.07011)
- Xie et al., "In-context Learning as Implicit Bayesian Inference," ICLR 2022: [arXiv:2111.02080](https://arxiv.org/abs/2111.02080)
- Zhang et al., "In-Context Learning through the Bayesian Prism," ICLR 2024: [arXiv:2306.04891](https://arxiv.org/abs/2306.04891)
- Reuter et al., "Can Transformers Learn Full Bayesian Inference in Context?" ICML 2025: [arXiv:2501.16825](https://arxiv.org/abs/2501.16825)
- Li Ji-An et al., "Linking In-context Learning to Human Episodic Memory," NeurIPS 2024: [arXiv:2405.14992](https://arxiv.org/abs/2405.14992)

---

## 3. Positional Encoding and Context Extension

### RoPE Won, But Has Costs

RoPE (Rotary Position Embeddings) is the standard. ALiBi was abandoned (Falcon 2.x switched to RoPE). But RoPE does not natively extrapolate — rotation angles beyond training range produce unpredictable signals. RoPE is also a direct cause of the U-shaped attention bias.

### Extension Techniques

- **NTK-Aware Scaling** (bloc97, Reddit): Foundational breakthrough. Spreads interpolation pressure across dimensions.
- **YaRN** (ICLR 2024): NTK-by-parts + attention temperature. 10x fewer tokens, 2.5x fewer training steps. De facto standard — used by Qwen, DeepSeek, LLaMA, GPT-oss.
- **LongRoPE** (Microsoft, ICML 2024): Extended to 2048K tokens via non-uniform rescaling.
- **LongRoPE2** (Microsoft, ICML 2025): Evolutionary search + mixed-context-window training. Extended LLaMA3-8B to 128K retaining >98.5% short-context performance, using 80x fewer tokens than Meta's approach.

### Core Limitation

All extension techniques struggle with two issues: maintaining short-context performance while extending to long contexts, and ensuring information fidelity is uniform across all positions (not just beginning/end). The U-shaped bias is reduced but not eliminated.

### Clou Implication

Clou cannot rely on context window extension to solve information fidelity. The golden context approach — curating exactly the right files per cycle — is more robust than trusting the model to integrate across a large context.

**Sources:**
- YaRN, ICLR 2024: [arXiv:2309.00071](https://arxiv.org/abs/2309.00071)
- LongRoPE, ICML 2024: [arXiv:2402.13753](https://arxiv.org/abs/2402.13753)
- LongRoPE2, ICML 2025: [arXiv:2502.20082](https://arxiv.org/abs/2502.20082)

---

## 4. Context Compression and Its Limits

### Hard Prompt Methods (Token-Level)

Remove low-information tokens while keeping natural language form:

- **LLMLingua** (Microsoft, EMNLP 2023): Up to 20x compression, minimal performance loss.
- **LongLLMLingua** (ACL 2024): Boosted performance 21.4% with ~4x fewer tokens on NaturalQuestions. Addresses position bias in long contexts.
- **LLMLingua-2** (ACL 2024 Findings): Data distillation for task-agnostic compression.
- **CompactPrompt** (2025): Unified pipeline for prompt and data compression in LLM workflows.
- **Key-Information Density** (2025): Prompt compression based on information density scoring.

### Soft Prompt Methods (Embedding-Level)

Compress into learned continuous vectors (not human-readable):

- **Gist Tokens** (NeurIPS 2023): Up to 26x compression, 40% FLOPs reduction.
- **AutoCompressor**: Recursively compresses up to 30,720 tokens into summary tokens.
- **ICAE (In-context Autoencoder)** (ICLR 2024): LoRA-adapted encoder compresses long contexts into memory slots. Achieves 4x compression.
- **500xCompressor** (ACL 2025): Improves on ICAE by replacing compression carriers with KV values at each layer. Extreme compression ratios up to 500x.
- **Activation Beacon** (2024): Plug-in module compressing activations (keys/values at every layer) rather than soft prompts. 2x acceleration, 8x KV cache reduction, baseline performance maintained.
- **DAST** (2025): Dynamic Allocation of Soft Tokens. Addresses uneven information density by dynamically allocating compression capacity to information-rich regions.
- **PCC (Pretraining Context Compressor)** (ACL 2025): Decoupled compressor-LLM framework pretrained on text reconstruction/completion. Converts long contexts into embedding-based memory slots. 4x-16x compression.
- **CCF (Context Compression Framework)** (2025): Hierarchical latent representations with segment-wise semantic aggregation.

### What Is Lost

- Hard compression risks dropping tokens with implicit contextual dependencies.
- Soft compression produces non-interpretable representations — no human legibility.
- Both face the fundamental tradeoff: higher compression → greater information loss, especially for nuanced reasoning.
- JetBrains (2025): **Observation masking** (replacing older outputs with placeholders) outperforms LLM summarization — 2.6% higher solve rate at 52% lower cost. Summarization causes 13–15% trajectory elongation.

### The Compaction Landscape (2025–2026)

The problem of compacting *ongoing conversations* — as opposed to compressing static prompts — has become a first-class engineering concern. Every major AI CLI now implements some form of it.

**What gets lost is systematic, not random.** Research and field reports converge on the same hierarchy of loss:

| Information Type | Survival After Compaction |
|---|---|
| High-level intent ("build an auth system") | Survives well |
| Key decisions ("chose JWT over sessions") | Survives if recent |
| File paths modified | Genericized ("modified auth middleware") |
| Line numbers, exact error messages | Lost entirely |
| Architecture decision *reasoning* | Reasoning vanishes; decision may be noted |
| Debugging hypotheses, dead ends explored | Lost |
| Specific code snippets | Paraphrased or lost |
| User constraints/preferences | **Silently dropped after multiple compactions** |

**Goal drift is the dominant failure mode.** "Facts as First Class Objects" (arXiv 2603.17781) found that cascading compaction eroded **54% of project constraints** after three rounds, causing **silent reversion to defaults** — the model does not report uncertainty about lost constraints. At 36.7x compression of 2,000 facts, in-context storage lost 60% of recoverable facts. External structured storage (hash-addressed tuples) maintained 100% accuracy.

**The re-reading loop.** Compaction creates a self-reinforcing degradation cycle: context fills → auto-compact fires → agent lacks details → re-reads files → freed tokens consumed → auto-compact fires again. Cognition found coding agents spend 60% of time searching for code, making post-compaction re-discovery especially costly.

**Cumulative loss is exponential.** Each successive compaction round loses information the previous summary preserved. All frontier models show accuracy below 50% by 5x compression. Multiple compactions compound the loss.

### How Industry Tools Implement Compaction

**Anthropic's server-side compaction API** (beta `compact-2026-01-12`): Detects input tokens exceed a trigger threshold, generates a summary, creates a `compaction` content block, then automatically drops all prior messages on subsequent requests. Supports `pause_after_compaction` (returns `stop_reason: "compaction"`) so the client can inject additional context (recent messages, critical instructions) before continuing. Combinable with `clear_tool_uses` and `clear_thinking` strategies. Benchmark: 58.6% token savings (204K → 82K in a 5-ticket workflow).

**Claude Code** implements three layers: microcompaction (large tool outputs offloaded to disk), auto-compaction at ~95% capacity, and manual `/compact [instructions]`. Post-compaction, it rehydrates: boundary marker → summary → re-read recent files → restored todo state → plan state → hook context → continuation instruction. CLAUDE.md files survive every compaction by re-loading from disk.

**Codex CLI** preserves recent user messages verbatim (up to 20K tokens) alongside the summary. Explicitly warns about accuracy degradation on repeated compaction.

**Gemini CLI** (`/compress`) treats compression as lossy and ephemeral — facts that must survive belong in `/memory`, not in the conversation. Compression does not save to long-term memory.

**The consensus across all tools:** LLM summarization is necessary but insufficient. The reliable part of compaction is *what you persist to disk* and reload after. The summary is a bridge, not a foundation.

### The Layered Pipeline Pattern

Microsoft's Semantic Kernel formalized the most robust approach — run strategies in sequence from gentle to aggressive:

1. **Tool result clearing** — Remove old tool inputs/outputs, keep structure. Cheap, no LLM call, no hallucination risk.
2. **Observation masking** — Replace old outputs with placeholders (`[Tool: search_docs → 47 results]`). Matches summarization quality at ~52% lower cost.
3. **LLM summarization** — Model generates a structured summary. Expensive, risks hallucination, but captures semantic content.
4. **Sliding window** — Keep only last N turns verbatim. Aggressive but predictable.
5. **Truncation** — Emergency backstop. Drop oldest messages entirely.

Each layer has its own trigger threshold. Aggressive strategies fire only when gentler ones prove insufficient.

### Clou Implication

Golden context is not compressed conversation — it is structured, purpose-built artifacts (compose.py, execution.md, decisions.md). This is closer to observation masking than summarization: each file records structured facts, not compressed prose. The research validates this over conversation compression.

For the **supervisor session** specifically — the long-running dialogue between user and supervisor — compaction becomes relevant. The supervisor accumulates conversation history across milestone planning, status checks, escalation resolutions, and user discussions. Unlike coordinator sessions (which start fresh per-cycle), the supervisor session has no natural reset boundary.

The research suggests a specific design:
1. **Golden context files are the persistent memory.** They survive any compaction by being on disk. The supervisor re-reads them.
2. **Tool result clearing first.** The supervisor's tool calls (`clou_status`, `clou_spawn_coordinator`) produce verbose results that are safe to clear once processed.
3. **LLM summarization for the conversational residue.** The dialogue — user intent, design discussions, requirement negotiations — is genuinely lossy to compress. User-directed focus (`/compact keep the auth discussion`) helps the model prioritize.
4. **Warn on repeated compaction.** The exponential degradation curve means the second compaction is significantly worse than the first. Surface this to the user.
5. **The API's `pause_after_compaction` enables the layered approach.** After the API generates a summary, the client can inject: the golden context pointers, the current roadmap state, any user-specified preservation instructions. This rehydration step is what distinguishes good compaction from lossy truncation.

**Sources:**
- LLMLingua, EMNLP 2023: [arXiv:2310.05736](https://arxiv.org/abs/2310.05736)
- LongLLMLingua, ACL 2024: [arXiv:2310.06839](https://arxiv.org/abs/2310.06839)
- Gist Tokens, NeurIPS 2023: [arXiv:2304.08467](https://arxiv.org/abs/2304.08467)
- ICAE, ICLR 2024: [openreview.net/forum?id=uREj4ZuGJE](https://openreview.net/forum?id=uREj4ZuGJE)
- 500xCompressor, ACL 2025: [github.com/ZongqianLi/500xCompressor](https://github.com/ZongqianLi/500xCompressor)
- Activation Beacon, 2024: [arXiv:2401.03462](https://arxiv.org/abs/2401.03462)
- DAST, 2025: [arXiv:2502.11493](https://arxiv.org/abs/2502.11493)
- PCC, ACL 2025: [aclanthology.org/2025.acl-long.1394](https://aclanthology.org/2025.acl-long.1394/)
- Prompt Compression Survey, NAACL 2025: [arXiv:2410.12388](https://arxiv.org/abs/2410.12388)
- JetBrains, "The Complexity Trap," NeurIPS 2025 DL4Code: [blog.jetbrains.com/research/2025/12/efficient-context-management](https://blog.jetbrains.com/research/2025/12/efficient-context-management/)
- "Facts as First Class Objects," 2026: [arXiv:2603.17781](https://arxiv.org/abs/2603.17781)
- Anthropic, Compaction API: [platform.claude.com/docs/en/build-with-claude/compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)
- Microsoft Semantic Kernel Compaction: [learn.microsoft.com/en-us/agent-framework/agents/conversations/compaction](https://learn.microsoft.com/en-us/agent-framework/agents/conversations/compaction)
- Factory.ai, "Compressing Context," 2025: [factory.ai/news/compressing-context](https://factory.ai/news/compressing-context)

---

## 4b. Session Persistence and Resumption

How do you restart a long-running agentic coding conversation without losing what matters? This section surveys the state of the art across three verticals — agentic AI frameworks, IDE/workflow systems, and LLM coding tools — to ground Clou's design.

Last updated: 2026-03-23

### The Landscape: How Tools Persist Sessions

**Claude Code** persists full JSONL transcripts (messages, tool_use, tool_result, metadata, git branch, cwd) to `~/.claude/projects/<hash>/<session>.jsonl`. Resume via `--continue` (most recent) or `--resume` (picker/ID). On resume, the entire message history is deserialized and fed to the model. Auto-compaction fires at ~83% capacity. The raw JSONL always has everything; compacted sessions carry the compressed version forward.

**Codex CLI** uses JSONL rollouts in `~/.codex/sessions/YYYY/MM/DD/`. Resume via `codex resume --last` or by session ID. Appends to the existing rollout file on resume. `history.max_bytes` caps the global index; individual session files remain intact.

**Gemini CLI** stores JSON per session at `~/.gemini/tmp/<project_hash>/chats/<tag>.json`. `/chat save <tag>` / `/chat resume <tag>`. Auto-save added Dec 2025. Sessions are project-specific.

**Cursor** uses SQLite (`state.vscdb`) with JSON blobs, per-workspace. Chat history persists automatically within a workspace but starting a new chat loses all context. "Memories" extracts persistent facts but discards the conversational thread.

**Aider** stores `.aider.chat.history.md` (human-readable log) and `.aider.input.history` (readline recall). Git commits are the authoritative artifact trail. `--restore-chat-history` replays prior conversation. After a soft token limit, a weak model auto-summarizes older messages.

| Tool | Storage | Resume UX | What's Lost |
|------|---------|-----------|-------------|
| Claude Code | JSONL | `--continue` / `--resume <id>` | Nothing structurally; compacted context loses detail |
| Codex CLI | JSONL rollouts | `resume --last` / `resume <id>` | Nothing; full rollout preserved |
| Gemini CLI | JSON per session | `/chat save`/`resume` + auto | Pre-12/2025: everything unless explicit save |
| Cursor | SQLite + JSON | Automatic within workspace | Everything on new chat |
| Aider | Markdown + text | `--restore-chat-history` | Reasoning; git preserves artifacts |

### The Emerging Consensus: Three-Tier Memory

Across all verticals — MemGPT/Letta, Mem0, LangGraph, CrewAI, and the coding tools — a consistent architecture emerges:

**Tier 1: Recent turns verbatim.** The last N messages (or up to a token budget) stay in the context window unmodified. This is the "working memory." All tools do this.

**Tier 2: Compressed older turns.** Older conversation is summarized, observation-masked, or stored externally. LangChain's `ConversationSummaryMemory`, Aider's weak-model summarization, Claude Code's auto-compaction, and MemGPT's "recall memory" all implement this tier differently but serve the same purpose: bounded context with degraded fidelity.

**Tier 3: Persistent structured facts.** Facts, decisions, and preferences extracted from conversation and stored outside the context window in a durable, searchable form. CrewAI's scoped memory, Mem0's extracted facts (26% accuracy boost, 90% token savings), Cursor's "Memories," and MemGPT's "archival memory" all target this tier. **This is what Clou's golden context already is** — `.clou/` as structured artifacts, not compressed conversation.

The research validates Tier 3 as the most reliable. Factory.ai found multi-session information retention via summarization (Tier 2) drops to **37%**. Tier 3 (structured external storage) maintains 100% accuracy (arXiv 2603.17781). The conversation is the reasoning overlay; the artifacts are the ground truth.

### Architectural Patterns from Adjacent Domains

**Event Log + Periodic Snapshot (databases, Temporal, Google Docs).** Log every action as an append-only stream. Periodically snapshot derived state. On resume: load latest snapshot, replay events since snapshot. PostgreSQL's WAL, Temporal's Event History, and Google Docs' operation log all use this. The tradeoff: storage grows with log length, but snapshots at natural boundaries (task completion, mode transitions) keep recovery time bounded.

**Temporal's Orchestration/Activity Separation.** The closest analog to agentic coding. "Workflow code" (orchestration logic) is deterministic and replayed from the event log. "Activities" (side-effectful work) are not replayed — their recorded results are used instead. This maps directly to: the supervisor's reasoning chain is the "workflow," tool calls and file edits are "activities." On resume, you don't re-execute effects; you load their recorded results.

**Reconstruct, Don't Restore (tmux, game saves).** Some state is fundamentally non-serializable (running processes, LLM internal state, live connections). tmux-resurrect doesn't persist process memory — it persists enough to recreate a reasonable approximation (layout, working directories, program names). Games use "save enough to reconstruct" rather than "save everything." For agent sessions: persist the action log and let the agent reconstruct its understanding, rather than trying to serialize the model's internal state.

**Two-Tier Save (Jupyter).** Frequent autosave to "current state" (may be mid-operation). Less frequent checkpoint to "known-good state" (at stable points). Recovery offers both. The distinction matters: autosave captures progress; checkpoint captures consistency.

**Rotating Slots with Corruption Guards (game saves).** Never overwrite the only copy. Three slots: S0 (immutable safety), S1 (active progress), S2 (scratch). Atomic writes: write to temp file, fsync, rename. Checksums and commit markers detect corruption. The cost of this discipline is low; the cost of losing a session is high.

**Debounced Continuous Persistence (VS Code).** Don't wait for explicit save points. VS Code's hot exit writes unsaved files 1 second after typing stops. tmux-continuum auto-saves every 15 minutes. The question is frequency vs I/O cost — and for agent sessions, the cost of a JSONL append is trivial.

**Shareable vs Private State (VS Code `.vscode/`, JetBrains `.idea/`).** Distinguish project-scoped state (could be committed) from user/session-scoped state (private). For Clou: golden context is project-scoped. Conversation history, token counts, model preference, and UI mode are session-scoped.

### What Summarization Destroys

Factory.ai tested on 36,000+ production messages. Key findings:

- Overall accuracy: 3.7–4.0 / 5 (~1 in 5 facts distorted or lost)
- **Multi-session retention: 37%.** Nearly two-thirds of information lost when carried across sessions via summarization.
- File paths get paraphrased (`src/middleware/auth.ts:52` → "the auth middleware file")
- Error codes get genericized (`ECONNREFUSED 127.0.0.1:5432` → "a database connection error")
- The artifact trail (which files were touched) degrades

JetBrains (NeurIPS 2025): **Observation masking** (hiding tool outputs, preserving the action chain) outperforms LLM summarization — 2.6% higher solve rate at 52% lower cost. Summarization causes 13–15% trajectory elongation (agents re-derive what they already knew).

Anthropic's own guidance: "Find the *smallest possible set of high-signal tokens*... Transition from static pre-loaded data to autonomous, dynamic context management."

The implication: **what you persist to disk and reload is the reliable part.** The LLM summary is a bridge, not a foundation.

### What Matters for Clou

Clou's architecture already embodies the strongest pattern in the literature. Golden context is Tier 3 memory — structured artifacts on disk. Session-per-cycle is observation masking at the architectural level. The supervisor checkpoint (`.clou/active/supervisor.md`) is agent-written structured state.

What's missing is Tier 1+2 for the supervisor session: the conversation itself. The current gap:

| State | Currently Persisted? | Recovery Path |
|-------|---------------------|---------------|
| Golden context (milestones, decisions, compose.py) | Yes (.clou/) | Agent reads on startup |
| Supervisor checkpoint | Yes (supervisor.md) | Agent reads on startup |
| Coordinator checkpoint | Yes (coordinator.md) | Cycle loop reads |
| Conversation transcript | No (only if /compact) | **Lost on restart** |
| Token counts, cost | No (in-memory) | Lost; derivable from session log |
| Model preference | No (resets to opus) | Lost |
| Mode, DAG, animation | No (transient) | Reconstructable from orchestrator state |

The conversation transcript is the only truly unrecoverable state. Everything else is either persisted already or reconstructable.

### Design Principles for Session Resumption

From the research, the following principles emerge:

**1. The JSONL transcript is the source of truth.** Every tool surveyed uses append-only logs. JSONL per session, append on every turn. Storage is cheap; reconstruction from logs is expensive.

**2. Separate the transcript from the resumable context.** The full JSONL is the archive (forensics, replay, export). The "resumable context" is a derived, compressed representation optimized for the model's context window. These are different artifacts with different lifecycles.

**3. Persist decisions and results, not intermediate computation.** (Universal across all domains.) The conversation matters; verbose tool outputs don't. The decision to grep matters; the 500 lines of grep output don't. Observation masking > full replay.

**4. Git is the authoritative artifact trail.** Aider's insight. Git history records what changed and when. The conversation records why. Both are needed; neither alone suffices.

**5. Resume should reconstruct, not replay.** Feeding a full transcript back to the model means re-processing all those tokens. The efficient path: load the golden context (Tier 3), inject a summary of the conversation (Tier 2), and include the last N turns verbatim (Tier 1). This is what Claude Code's rehydration and Aider's `--restore-chat-history` both do.

**6. Auto-persist, don't wait for /exit.** VS Code's 1-second debounce, tmux-continuum's 15-minute interval, and Claude Code's per-message JSONL all agree: continuous persistence is safer than save-on-exit. Crashes happen.

**7. Session identity is per-project, per-invocation.** Claude Code, Codex, and Gemini all scope sessions to the project directory. A session UUID gates the active state. Multiple concurrent sessions in the same project need separate namespaces.

**Sources:**
- Claude Code session management: [kentgigger.com/posts/claude-code-conversation-history](https://kentgigger.com/posts/claude-code-conversation-history)
- Claude Code --continue/--resume: [pasqualepillitteri.it/en/news/366](https://pasqualepillitteri.it/en/news/366/claude-code-continue-resume-guide)
- Claude Code context buffer: [claudefa.st/blog/guide/mechanics/context-buffer-management](https://claudefa.st/blog/guide/mechanics/context-buffer-management)
- Codex CLI persistence: [deepwiki.com/openai/codex/3.3-session-management-and-persistence](https://deepwiki.com/openai/codex/3.3-session-management-and-persistence)
- Gemini CLI sessions: [developers.googleblog.com/pick-up-exactly-where-you-left-off](https://developers.googleblog.com/pick-up-exactly-where-you-left-off-with-session-management-in-gemini-cli/)
- Cursor architecture: [dasarpai.com/dsblog/cursor-chat-architecture-data-flow-storage](https://dasarpai.com/dsblog/cursor-chat-architecture-data-flow-storage/)
- Aider repo map: [aider.chat/docs/repomap.html](https://aider.chat/docs/repomap.html)
- Factory.ai compression evaluation: [factory.ai/news/evaluating-compression](https://factory.ai/news/evaluating-compression)
- Factory.ai compressing context: [factory.ai/news/compressing-context](https://factory.ai/news/compressing-context)
- JetBrains "The Complexity Trap": [blog.jetbrains.com/research/2025/12/efficient-context-management](https://blog.jetbrains.com/research/2025/12/efficient-context-management/)
- Anthropic context engineering: [anthropic.com/engineering/effective-context-engineering-for-ai-agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- MemGPT/Letta: [arXiv:2310.08560](https://arxiv.org/abs/2310.08560)
- Mem0: [arXiv:2504.19413](https://arxiv.org/abs/2504.19413)
- Zep/Graphiti temporal knowledge graphs: [arXiv:2501.13956](https://arxiv.org/abs/2501.13956)
- LangGraph checkpointing: [langchain-ai.github.io/langgraph/concepts/persistence](https://langchain-ai.github.io/langgraph/concepts/persistence/)
- Temporal durable execution: [temporal.io/blog/durable-execution-meets-ai](https://temporal.io/blog/durable-execution-meets-ai-why-temporal-is-the-perfect-foundation-for-ai)
- VS Code hot exit: [code.visualstudio.com/blogs/2016/11/30/hot-exit-in-insiders](https://code.visualstudio.com/blogs/2016/11/30/hot-exit-in-insiders)
- tmux-resurrect: [github.com/tmux-plugins/tmux-resurrect](https://github.com/tmux-plugins/tmux-resurrect)
- OpenAI Assistants Threads: [platform.openai.com/docs/assistants](https://platform.openai.com/docs/assistants)
- Lilian Weng agent memory survey: [lilianweng.github.io/posts/2023-06-23-agent](https://lilianweng.github.io/posts/2023-06-23-agent/)
- "Agents Are Databases": [prassanna.io/blog/agents-are-databases](https://prassanna.io/blog/agents-are-databases/)
- Recursive summarization for dialogue: [ScienceDirect 10.1016/j.neucom.2025.129658](https://www.sciencedirect.com/science/article/abs/pii/S0925231225008653)

---

## 5. System Prompts and Instruction Hierarchy

### System Prompts Have No Architectural Privilege

**"Control Illusion"** (Geng et al. 2025): The system/user prompt separation does not establish reliable instruction hierarchy. Models exhibit strong inherent biases toward certain constraint types regardless of priority designation. Show recency bias favoring newer instructions over established rules. Respond more to **social cues** (authority, expertise, consensus) than to architectural position.

**"A Closer Look at System Prompt Robustness"** (2025): Performance approaches **zero** when stress-tested with increasing guardrails in the system message (tested 1–20 guardrails). Models forget guardrails or fail to resolve conflicts.

### Instruction Density Degrades Performance

**IFScale** (2025) tested models with up to 500 simultaneous keyword-inclusion instructions:

- Best frontier models achieve only **68% accuracy at 500 instructions**
- Three degradation patterns:
  - **Threshold decay** (reasoning models like o3, Gemini 2.5 Pro): Near-perfect until a critical density, then collapse
  - **Linear decay** (Claude Sonnet 4, GPT-4.1): Steady decline
  - **Exponential decay** (GPT-4o, Llama 4 Scout): Rapid falloff
- Mid-range peaks around 150–200 instructions before converging toward uniform failure

### What Does Work

- **OpenAI's Instruction Hierarchy** (2024): Training-time alignment to prioritize system > user > third-party. Drastically increased robustness to prompt injection. But "Control Illusion" challenges generalization.
- **ISE (Instructional Segment Embedding)** (ICLR 2025): Embedding instruction-type info directly into the model yields up to 18.68% robustness boost.
- Fine-tuning with realistic data + inference-time classifier-free guidance can improve performance.

### Clou Implication

Clou's system prompts must minimize instruction count. Rather than encoding every behavioral rule in the system prompt, use a small set of high-priority constraints + pointer to a protocol reference file the agent reads as its first action. Count discrete instructions, not just tokens. Keep under the threshold decay point for the target model.

**Sources:**
- "Control Illusion," Geng et al. 2025: [arXiv:2502.15851](https://arxiv.org/abs/2502.15851)
- "System Prompt Robustness," 2025: [arXiv:2502.12197](https://arxiv.org/abs/2502.12197)
- "How Many Instructions Can LLMs Follow at Once?" 2025: [arXiv:2507.11538](https://arxiv.org/abs/2507.11538)
- OpenAI Instruction Hierarchy, 2024: [arXiv:2404.13208](https://arxiv.org/abs/2404.13208)
- ISE, ICLR 2025: [proceedings.iclr.cc](https://proceedings.iclr.cc/paper_files/paper/2025/file/ea13534ee239bb3977795b8cc855bacc-Paper-Conference.pdf)

---

## 6. Structured Formats and Prompt Architecture

### Format Significantly Affects Performance

**"Does Prompt Formatting Have Any Impact on LLM Performance?"** (2024): GPT-3.5-turbo performance varied by up to **40%** depending on format in code translation. GPT-4 was more robust. Different models show distinct format preferences.

**Convergence toward XML** for complex instructions:
- Claude is specifically tuned to attend to XML structure (Anthropic docs)
- XML uses more tokens (opening + closing tags) but requires less iteration
- Markdown saves ~15% tokens vs. XML for equivalent representations
- JSON shows inconsistent results, often underperforming

### Overly Rigid Output Constraints Hurt

**"Let Me Speak Freely?"** (2024): Overly rigid format restrictions on *output* degrade reasoning quality. Structure helps inputs; rigidity hurts outputs.

### Content and Format Must Be Co-Optimized

**"Beyond Prompt Content"** (2025): Content and format should be treated as a joint optimization problem, not independently.

### Clou Implication

Clou's prompt files should use XML tags to demarcate sections rather than relying solely on markdown headers. Output schemas (execution.md, decisions.md) should be structured markdown — human-readable but with enough structure for reliable extraction — not rigid JSON.

**Sources:**
- "Does Prompt Formatting Have Any Impact?" 2024: [arXiv:2411.10541](https://arxiv.org/abs/2411.10541)
- "Let Me Speak Freely?" 2024: [arXiv:2408.02442](https://arxiv.org/abs/2408.02442)
- "Beyond Prompt Content," 2025: [arXiv:2502.04295](https://arxiv.org/abs/2502.04295)
- Anthropic Claude Prompting Best Practices: [platform.claude.com/docs](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)

---

## 7. Decomposition vs. Monolithic Prompts

### Decomposition Consistently Wins

**Decomposed Prompting (DecomP)** (ICLR 2023) demonstrated that breaking tasks into subtasks with specialized prompts significantly outperforms Chain-of-Thought and Least-to-Most. Key insight: **the modular structure itself drives improvement**, not just the content. DecomP maintained near-100% accuracy as input length increased; monolithic approaches degraded.

Advantages:
- Each subtask handler independently optimizable, debuggable, upgradable
- Error-correcting subtask handlers improve overall accuracy
- Supports hierarchical and recursive decomposition
- Specialized handlers can use different tools or models per subtask

### When Decomposition Hurts

- Simple tasks with overhead costs
- Tasks requiring global context across all steps
- When decomposition boundaries split semantically unified reasoning
- Cascading compositional failures (see §9)

### Context Engineering > Prompt Engineering

Anthropic (2025) formalized **context engineering**: "designing dynamic systems that provide the right information and tools, in the right format, at the right time." Context-engineered agents achieved **54% better performance** on multi-step tasks vs. prompt-engineered equivalents.

The **ACE Framework** (Stanford/SambaNova/UC Berkeley) showed that editing input context outperformed model fine-tuning: 10.6% improvement on agentic tasks + 86.9% latency reduction.

### Clou Implication

Clou's per-cycle-type read sets are context engineering in practice: each cycle type gets exactly the golden context files it needs, nothing more. The system prompt should be decomposed into role identity (minimal, static) and cycle-specific protocol (loaded per cycle from golden context). This is not optimization — it is architecturally necessary.

**Sources:**
- Decomposed Prompting, ICLR 2023: [arXiv:2210.02406](https://arxiv.org/abs/2210.02406)
- Anthropic, "Effective Context Engineering," 2025: [anthropic.com/engineering/effective-context-engineering-for-ai-agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Anthropic, "Building Effective Agents," 2024: [anthropic.com/research/building-effective-agents](https://www.anthropic.com/research/building-effective-agents)

---

## 8. Role Prompting Is Mostly Theater

**"Playing Pretend"** (Mollick et al., December 2025): Tested six models on GPQA Diamond and MMLU-Pro (graduate-level science, engineering, law). Domain-matched expert personas had **no significant impact** on performance (one exception: Gemini 2.0 Flash). Domain-mismatched experts sometimes degraded performance. Low-knowledge personas (layperson, toddler) often reduced accuracy.

**162-role experiment:** No significant accuracy gain vs. neutral scenario; on average, role prompting slightly *reduced* accuracy.

**"When 'A Helpful Assistant' Is Not Really Helpful"** (2023): System prompt personas do not improve performance compared to no persona across 2,410 factual questions.

**"Two Tales of Persona"** (EMNLP 2024): Two valid use cases — role-playing (where persona is the goal) and personalization (adapting to user characteristics). For factual tasks, personas are not beneficial.

**What role prompting does:**
- Steers tone, style, and format effectively
- Can improve creative/high-openness tasks
- Does *not* add knowledge the model doesn't have
- Can create false sense of expertise while hallucinating

### Clou Implication

"You are Clou's coordinator" should be a brief identity anchor, not an elaborate persona. The system prompt's value comes from *protocol instructions*, not persona framing. Spend tokens on behavioral constraints and file pointers, not on role backstory.

**Sources:**
- Mollick et al., "Playing Pretend," 2025: [arXiv:2512.05858](https://arxiv.org/abs/2512.05858)
- "When 'A Helpful Assistant' Is Not Really Helpful," 2023: [arXiv:2311.10054](https://arxiv.org/abs/2311.10054)
- "Two Tales of Persona," EMNLP 2024: [aclanthology.org/2024.findings-emnlp.969](https://aclanthology.org/2024.findings-emnlp.969/)

---

## 9. The Planning Gap and Compositionality Failures

### LLMs Cannot Plan Reliably

Kambhampati (ICML 2024): **Only 12% of GPT-4's autonomously generated plans are executable.** Self-verification fails — models "can't recognize a correct coloring and thus merrily pass over fortuitously correct colorings." When action names are obfuscated (removing pattern-matching from training data), performance drops dramatically; classical planners are unaffected.

**What looks like planning is pattern retrieval from training distributions, not reasoning over problem structure.**

Fine-tuning produces minimal improvement. Self-critique iterations actually *worsen* performance by accepting flawed solutions.

### The LLM-Modulo Framework

The only configuration shown to produce reliable plans: LLM generates candidates, **external verifiers** check them. The verification must be external to the LLM. Generate-test-critique with external critics.

### Reasoning Models: Improvement Without Understanding

Kambhampati's follow-up (September 2024) evaluated OpenAI o1 on PlanBench:
- Standard Blocksworld: **97.8%** accuracy (vs. 62.6% for prior best LLM).
- **Obfuscated** Blocksworld (renamed actions/objects): 52.8%. Classical planners are unaffected.
- Larger problems (20-40 step solutions): **23.63%**.
- Unsolvable instances: only 27% correctly identified; 54% generated false plans.

Reasoning models improve on known distributions but still lack formal planning capabilities.

### Intermediate Tokens Are Not Reasoning

Kambhampati (2025) compiled evidence that chain-of-thought traces are statistical scaffolding, not interpretable reasoning:

- Models trained on **intentionally incorrect** intermediate traces paired with correct solutions **outperform** models trained on correct traces (Bhambri et al.).
- Arbitrarily removing steps from A* search traces (destroying semantic validity) still improved accuracy (Dualformer).
- DeepSeek R1-Zero (mixing incoherent English/Chinese tokens) outperformed R1 with human-annotated traces.

**Intermediate tokens function as prompt augmentations optimized for answer correctness, not as steps in a reasoning process.** This reinforces LLM-Modulo: verify outputs through external means. Do not trust chain-of-thought explanations as evidence of plan validity or quality.

### Failure-Driven Re-Decomposition

ADaPT (NAACL 2024 Findings): when an LLM fails to execute a sub-task, decompose it further into finer-grained steps. Success rates 28.3% higher in ALFWorld, 27% in WebShop, 33% in TextCraft over baselines. Dynamically adjusts decomposition granularity to both task complexity and executor capability.

ADaPT implements the downward half of aspiration adaptation (Simon): lower granularity on failure. The upward half (coarser plans after repeated success) remains unimplemented in any framework.

### Compositionality Breaks at Two Hops

- **Reversal Curse** (ICLR 2024): Trained on "A is B" → cannot answer "B is A." Fundamental, nothing mitigates it.
- **Multi-hop reasoning** (ACL 2024): Strong evidence for first hop (~80%), only "moderate" for second hop. Full multi-hop traversal unreliable.
- Root cause: attention module failures in middle transformer layers (ACL 2024 Findings, CREME).
- **Cascading errors**: Each subtask's output feeds the next. Compositional failures at each boundary amplify through the chain. Memory and reflection errors propagate most aggressively.

### Clou Implication

- `compose.py` validated by AST parsing (external verifier) — directly implements LLM-Modulo.
- Quality gates as external verification — the coordinator does not self-assess quality. The "intermediate tokens aren't reasoning" finding makes this non-negotiable: coordinator reasoning in `decisions.md` is pattern completion, not verifiable reasoning. The quality gate is the only reliable signal.
- Coordinator ASSESS cycle with explicit criteria — criteria-driven evaluation against requirements.md, not self-generated judgment.
- Agent team outputs (execution.md) inspected by the coordinator, not self-validated by workers.
- ADaPT's failure-driven re-decomposition could improve the ASSESS→rework transition: when rework is triggered, the coordinator could decompose the failed task more finely rather than retrying at the same granularity. This is a template-level optimization, not an architectural change — the compose conventions determine whether re-decomposition is available.
- Risk: cascading failures across phases if execution.md contains errors the ASSESS cycle doesn't catch. Quality gates help here.

**Sources:**
- Kambhampati et al., "LLMs Can't Plan, But Can Help Planning," ICML 2024: [arXiv:2402.01817](https://arxiv.org/abs/2402.01817)
- Kambhampati, "LLMs Still Can't Plan; Can LRMs?" 2024: [arXiv:2409.13373](https://arxiv.org/abs/2409.13373)
- Kambhampati, "Stop Anthropomorphizing Intermediate Tokens," 2025: [arXiv:2504.09762](https://arxiv.org/abs/2504.09762)
- Kambhampati, "(How) Do Reasoning Models Reason?" Annals NYAS 2025: [doi:10.1111/nyas.15339](https://nyaspubs.onlinelibrary.wiley.com/doi/10.1111/nyas.15339)
- Prasad et al., "ADaPT: As-Needed Decomposition and Planning," NAACL 2024: [aclanthology.org/2024.findings-naacl.264](https://aclanthology.org/2024.findings-naacl.264/)
- Berglund et al., "The Reversal Curse," ICLR 2024: [arXiv:2309.12288](https://arxiv.org/abs/2309.12288)
- Bao et al., "Understanding and Patching Compositional Reasoning," ACL 2024: [arXiv:2402.14328](https://arxiv.org/abs/2402.14328)

---

## 10. Multi-Agent Systems: Failure Modes and What Works

### The Uncomfortable Truth

- **41–86.7% of multi-agent systems fail in production**, most within hours
- **79% of failures are specification/coordination**, not technical capability
- **14 failure modes** across three categories: specification/design (5), inter-agent misalignment (6), verification/termination (3)
- **Best-of-N sampling** from a single model often matches multi-agent performance at lower cost
- ChatDev: as low as **25% correctness** with GPT-4o
- Simple interventions (improved prompts, topology redesign) yield only ~14% improvement
- Failures are inter-agent (organizational), not individual

### What Works

**Blackboard architectures** consistently outperform master-slave patterns (13–57% improvement). Central shared medium, agents self-select participation based on capability rather than being assigned.

**Structured intermediate artifacts** dramatically reduce error propagation. MetaGPT's typed outputs (PRDs, design specs) versus free-form chat. CrewAI's design guidance: "80% of effort should go into designing tasks, 20% into defining agents."

**Inception prompting** (CAMEL, ChatDev): Carefully crafted initialization prompt that assigns roles, prevents role-flipping, and encourages consistency. Prompt engineering occurs only at initialization — agents prompt each other autonomously after.

**Observation masking > LLM summarization** for context management (JetBrains 2025): 2.6% higher solve rate at 52% lower cost.

**Cognitive load theory validates selective parallelism.** The CoThinker architecture (NeurIPS 2025 submission) mapped Sweller's three cognitive load types (intrinsic, extraneous, germane) to multi-agent LLM systems. Multi-agent coordination showed strongest improvements on high-complexity tasks but **underperformed on low-complexity tasks** — coordination overhead creates extraneous load that exceeds the benefit of distributing intrinsic load. Attention entropy increased from 4.44 to 5.04 across complexity levels, confirming distributed attention under higher demand. The implication: split across agents only when intrinsic task complexity exceeds individual capacity.

### What Fails

- Free-form chat between agents without structured output requirements → hallucination amplification
- Static role assignments that cannot adapt to evolving tasks
- Self-reflection without external validation → false beliefs persist indefinitely
- Aggressive context compression → silently drops critical facts
- Majority voting → collapses reasoning into context-free counts

### Cognitive Architecture Parallels

The strongest multi-agent patterns align with cognitive science:

- **Blackboard ≈ Global Workspace Theory**: Specialized modules compete for access to shared workspace; winning coalition broadcasts globally
- **Hierarchical delegation ≈ Feudal reinforcement learning**: Manager outputs sub-goals at slower rate; workers execute at full rate
- **Stigmergy ≈ File-system-as-communication**: Agents coordinate through environment modification (structured artifacts) rather than direct messaging

### Clou Implication

Clou's `.clou/` directory *is* a blackboard architecture — the strongest performing multi-agent pattern. Golden context files are the shared medium; agents read and write structured artifacts rather than chatting. The specification rigor (compose.py, typed function signatures, structured execution.md) directly addresses the #1 failure category.

However: the 41–86.7% production failure rate demands Clou build explicit countermeasures against the 14 documented failure modes, especially inter-agent misalignment and verification failures.

**Sources:**
- Cemri et al., "Why Do Multi-Agent LLM Systems Fail?" 2025: [arXiv:2503.13657](https://arxiv.org/abs/2503.13657)
- MetaGPT, ICLR 2024 Oral: [arXiv:2308.00352](https://arxiv.org/abs/2308.00352)
- ChatDev, ACL 2024: [aclanthology.org/2024.acl-long.810](https://aclanthology.org/2024.acl-long.810/)
- CAMEL, NeurIPS 2023: [arXiv:2303.17760](https://arxiv.org/abs/2303.17760)
- AutoGen, COLM 2024: [arXiv:2308.08155](https://arxiv.org/abs/2308.08155)
- CoThinker, "Coordination of LLMs under Cognitive Load Theory," NeurIPS 2025: [arXiv:2506.06843](https://arxiv.org/abs/2506.06843)

---

## 11. Cognitive Science and Transformer Behavior

### Where Transformers Align with Cognitive Models

Recent research reveals structural parallels — not analogies, but shared mathematical substrates:

**Recognition-Primed Decision Making (Klein).** RPD describes expert decision-making: perceive a situation, match to a prototype from experience, mentally simulate one course of action forward. Transformers implement the pattern-matching half via attention-as-Hopfield-retrieval (§2). The prototype library is the training distribution plus the current context window. What transformers lack is the feedback loop that refines prototypes through domain experience with outcome feedback. The training distribution is frozen; experts accumulate.

**Situated cognition (Suchman).** Plans are resources consulted during action, not blueprints executed mechanically. Understanding emerges from interaction with the material, not from analysis before touching it. Multi-agent failure research (§10) validates this empirically — systems that treat plans as rigid commitments fail at specification/coordination. The architectural response is plans that adapt through execution-assessment loops.

**Satisficing (Simon).** The cost of searching for optimal solutions exceeds the marginal benefit. Generate one candidate, verify against aspiration criteria, proceed. SITAlign (2025) operationalized satisficing for LLM alignment: maximizing a primary objective subject to threshold constraints on secondary criteria achieved a 22.3% margin improvement. The pattern: don't search the solution space, verify the first plausible candidate.

**Dual-process mapping.** Base LLMs operate as System 1 — fast, associative, pattern-completing (Nature Reviews Psychology 2025, Bellini-Leite 2024). Chain-of-thought imposes System-2-like deliberation, but the mechanism is still pattern completion (§9: intermediate tokens are not reasoning). LLM "cognitive biases" reflect training data patterns, not genuine heuristic processing. LLMs also exhibit non-human biases (hallucinations) with no cognitive analogue.

**Episodic memory.** Li Ji-An et al. (NeurIPS 2024) showed induction heads share behavioral, functional, and mechanistic parallels with human episodic memory (the CMR model — see §2). Structured records of what happened, what was decided, what was produced serve the same role as episodic memory that transformers architecturally lack within a session.

### What Remains Missing

### Working Memory Limits Are a Feature

Human working memory holds 4±1 chunks (Cowan). This constraint **promotes efficient processing** through forced prioritization. LLMs treat all context tokens equally — no priority-based allocation. The Cognitive Workspace framework (2025) proposes hierarchical buffers (8K Immediate → 64K Task → 256K Episodic → 1M+ Semantic), achieving 54–60% memory reuse vs. 0% for standard RAG.

### Chunking Is Dynamic and Hierarchical

Humans dynamically group information into meaningful units, overcoming working memory limits. Static tokenization cannot simulate this. Adaptive cross-modal tokenization with hierarchical representations has been proposed but remains unimplemented in production.

### Forgetting Is Computationally Useful

Ebbinghaus curves are relevance-based information management, not deficiency. AI systems accumulate without principled decay. The memory survey (2025) identifies "missing forgetting mechanisms" as a critical gap.

### Metacognition Is Absent

No current architecture monitors or optimizes its own cognitive processes. Models cannot assess whether they are confused, overloaded, or making errors.

### Event Segmentation Is Missing

The hippocampus detects boundaries between episodes, enabling structured memory formation. LLM agents cannot autonomously identify task boundaries → context contamination across unrelated subtasks.

### Attention Gating Is Absent

Human prefrontal cortex actively gates what enters working memory. Neural architectures like WorkMATe model this through gated memory circuits. Production LLMs use uniform attention → positional bias instead of content-based salience.

### Memory > Model Scaling

The memory survey's central claim: "memory deserves the same engineering investment as the LLM itself." MemoryArena: agents without active memory dropped from 80%+ to 45% task completion. Voyager's skill library: **15.3x faster** progression. Memory improvements often yield larger performance gains than model scaling.

### Clou Implication

The architecture embodies the cognitive principles that align with transformer mechanics and addresses several of the gaps:

**Alignments already in the architecture:**

- **RPD alignment**: Per-cycle read sets provide the prototype library. The training distribution covers pattern matching. Quality gates compensate for unreliable "mental simulation" (chain-of-thought — see §9). The quality of decomposition depends on the training data density axis (DB-11): high-density domains (software) get better prototypes than sparse ones.
- **Situated cognition alignment**: Session-per-cycle treats plans as resources. The EXECUTE→ASSESS→rework loop is situated action — plans adapt to what execution reveals. The coordinator consults compose.py, doesn't mechanically execute it.
- **Satisficing alignment**: One decomposition, structural validation via AST, proceed. No plan comparison, no alternative enumeration.
- **Chunking**: Each golden context file is a semantic chunk (compose.py = plan, execution.md = results, decisions.md = reasoning). Files, not token ranges, are the unit of context management.
- **Forgetting via session-per-cycle**: Each cycle starts fresh. Only what's externalized to golden context persists — implicit forgetting of within-cycle reasoning.
- **Event segmentation via phases**: Each phase is an explicit boundary. Git commits at phase completion.
- **Hierarchical memory**: project.md (long-term), milestone.md (medium-term), execution.md (short-term), active/coordinator.md (working state).
- **Episodic memory**: Golden context files are structured episodic records that serve the function induction heads cannot maintain across sessions.

**Convergence with the user — the supervisor as sensemaking mechanism.** The supervisor-user dialogue is where domain context enters the system. The supervisor matches user intent to a harness template (RPD prototype matching at the project level), proposes one milestone spec (satisficing), and refines through conversation. The architecture does not need a separate "exploration phase" at the domain-agnostic level — the supervisor IS the exploration mechanism. Domain-specific exploration (e.g., codebase reading before planning in software) is a template-level concern: a template can define an exploration agent type with appropriate tools and read permissions.

**What Clou lacks**: metacognition (no self-monitoring of confusion or overload) and attention gating (the coordinator reads all pointed files equally, with no priority mechanism within the read set).

**Sources:**
- "Cognitive Workspace," 2025: [arXiv:2508.13171](https://arxiv.org/abs/2508.13171)
- "AI Meets Brain: Memory Systems Survey," 2025: [arXiv:2512.23343](https://arxiv.org/abs/2512.23343)
- "Memory for Autonomous LLM Agents," 2026: [arXiv:2603.07670](https://arxiv.org/abs/2603.07670)
- MemGPT / Letta, 2023: [arXiv:2310.08560](https://arxiv.org/abs/2310.08560)
- A-Mem, 2025: [arXiv:2502.12110](https://arxiv.org/abs/2502.12110)
- Klein, "Recognition-Primed Decision Model," 1993
- Suchman, "Plans and Situated Actions," 1987
- Simon, "Models of Bounded Rationality," 1982
- "Bounded Rationality for LLMs: SITAlign," 2025: [arXiv:2505.23729](https://arxiv.org/abs/2505.23729)
- "Dual-process theory and decision-making in LLMs," Nature Reviews Psychology 2025: [doi:10.1038/s44159-025-00506-1](https://www.nature.com/articles/s44159-025-00506-1)
- Bellini-Leite, "Dual Process Theory for LLMs," Adaptive Behavior 2024: [doi:10.1177/10597123231206604](https://journals.sagepub.com/doi/10.1177/10597123231206604)

---

## 12. NIAH and Beyond: What Benchmarks Actually Measure

Standard needle-in-a-haystack tests dramatically overstate long-context capability. They test retrieval, not integration.

- **RULER** (NVIDIA, COLM 2024): Multi-hop and aggregation. Half of models failed at 32K despite claiming 32K+ support.
- **NoLiMa** (Adobe, ICML 2025): Removed lexical overlap. 11/13 models below 50% of short-context baselines at 32K. GPT-4o: 99.3% → 69.7%.
- **HaystackCraft** (2025): Multi-hop over Wikipedia hyperlinks. Cascading failures in Gemini 2.5 Pro and GPT-5.
- **U-NIAH** (2025): RAG helps smaller LLMs (82.58% win rate) but advanced reasoning models show *reduced* RAG compatibility due to sensitivity to semantic distractors.
- **Anthropic MRCR v2**: Claude Opus 4.6 scored 76% vs. Gemini 3 Pro's 26.3% — significant model variation in real context utilization.

### Clou Implication

Clou should not rely on the model's ability to synthesize across large contexts. The architecture already avoids this by curating targeted read sets. But the ASSESS cycle — where the coordinator evaluates execution.md against requirements.md and compose.py — is a multi-document integration task. Keep golden context files concise to minimize the integration burden.

**Sources:**
- RULER, COLM 2024: [arXiv:2404.06654](https://arxiv.org/abs/2404.06654)
- NoLiMa, ICML 2025: [arXiv:2502.05167](https://arxiv.org/abs/2502.05167)
- HaystackCraft, 2025: [arXiv:2510.07414](https://arxiv.org/abs/2510.07414)
- U-NIAH, 2025: [arXiv:2503.00353](https://arxiv.org/abs/2503.00353)

---

## 13. Harness Engineering: The Hard Part Is Not the Model

The 2026 consensus in agent infrastructure: **the harness — tools, environments, and orchestration — is the hard part**, not the model. Models are commodity; what differentiates agent systems is how they're configured, what tools they have, and how quality is enforced.

### The Harness Thesis

Philipp Schmid (Hugging Face, 2026): "Start simple. Provide robust atomic tools. Let the model make the plan." The argument: elaborate planning frameworks add complexity without proportional improvement. What matters is tool reliability and clear invocation contracts. A well-harnessed weak model outperforms a poorly-harnessed strong one.

LangChain's agent anatomy (2026) decomposes agents into model, tools, instructions, and orchestration — with tools and orchestration as the engineering-dominant components. The model is a commodity input; the harness is the differentiator.

### Tool Creation as First-Class Capability

**ToolMaker** (ACL 2025, arXiv:2502.11705): Agents that create their own tools from GitHub repositories. Three-phase pipeline: dispatch (select tool-creation strategy), build (implement and test), and verify (end-to-end validation). 80% success rate on novel tool creation. Key insight: tool creation is decomposable into subtasks that existing LLMs handle well individually.

**Tool-R0** (arXiv:2504.15979): Self-evolving tool-use reasoning. Agents improve their tool usage through reinforcement learning without human annotation. Demonstrates that tool-use proficiency is trainable, not just prompt-engineered.

### Dynamic Tool Selection: The Accuracy Cliff

Agent accuracy collapses past ~30 tools when tool descriptions overlap semantically. This is documented across multiple systems:

- **Gorilla** (Berkeley, 2023): API call generation degrades with similar-description tools
- **AgentBench** (ICLR 2024): Tool selection accuracy inversely correlated with tool count
- **Empirical observation in MCP ecosystems**: Connecting all available MCP servers to an agent degrades performance vs. curated subsets

The implication: tool curation is essential. Providing "everything available" is worse than providing "what's relevant." This directly motivates template-level tool selection (DB-11).

### TEA Protocol: Tools, Environments, and Agents as Resources

**AgentOrchestra** (arXiv:2506.12508) introduces the TEA Protocol — treating tools, environments, and agents as first-class resources with explicit lifecycles, versioned interfaces, and a registry for discovery. Scored 89.04% on GAIA Level 3 (previous SOTA: ~67%). Key architectural elements:

- **Tool lifecycle management**: Tools are created, versioned, tested, and retired — not just listed
- **Environment synthesis**: Agents create execution environments on demand (sandboxed Docker containers, virtual filesystems)
- **Registry-based discovery**: Agents find tools by capability description, not by knowing tool names in advance

Clou's template system (DB-11) is a static version of TEA's resource model — appropriate for the current architecture where configuration is per-project, not per-turn. TEA informs future evolution: templates could eventually declare capabilities needed rather than specific tools.

### Clou Implication

The harness thesis validates Clou's architecture: the orchestrator, golden context, and quality gates ARE the hard part. The model is an input. DB-11's template system formalizes this by making the harness configuration explicit and parameterizable.

The ~30-tool accuracy cliff validates template-level tool curation. The assessor gets Brutalist tools; the verifier gets CDP tools; the implementer gets editing tools. No agent gets everything. Templates enforce this curation per domain.

ToolMaker and TEA are future capabilities, not current requirements. They inform the template schema design (tools as lists today, capability declarations tomorrow) but don't change the initial implementation.

**Sources:**
- Qian et al., "ToolMaker: Automatic Tool Creation for LLM Agents," ACL 2025: [arXiv:2502.11705](https://arxiv.org/abs/2502.11705)
- AgentOrchestra, "TEA Protocol," 2025: [arXiv:2506.12508](https://arxiv.org/abs/2506.12508)
- Tool-R0, "Self-Evolving Tool-Use Reasoning," 2025: [arXiv:2504.15979](https://arxiv.org/abs/2504.15979)
- Schmid, "The Agent Harness," 2026: [philschmid.de/agent-harness-2026](https://philschmid.de/agent-harness-2026)
- Gorilla, UC Berkeley, 2023: [arXiv:2305.15334](https://arxiv.org/abs/2305.15334)
- AgentBench, ICLR 2024: [arXiv:2308.03688](https://arxiv.org/abs/2308.03688)

---

## 14. Tool Discovery and MCP Registry Infrastructure

As the MCP ecosystem matures, tool discovery becomes a systems problem. The question shifts from "what tools does this agent have?" to "how does an agent find the right tools?"

### MCP Registry and Semantic Search

**MCP Gateway Registry** (agentic-community/mcp-gateway): Implements FAISS-indexed semantic search over MCP tool descriptions. An agent describes what it needs in natural language; the registry returns ranked tool matches. This decouples tool knowledge from the agent — the agent doesn't need to know tool names, only capabilities.

**Anthropic AAIF Registry Spec** (2026): Proposed standard for MCP tool registries with capability-based indexing, version management, and trust scoring. Not yet widely implemented, but signals the direction: tool discovery as infrastructure, not configuration.

### Environment Synthesis

**ScaleEnv / EnvScaler** (arXiv:2505.11389): Automated synthesis of execution environments for agent evaluation. Generates diverse, realistic environments from seed specifications. Relevant to Clou's verification phase — future templates could specify environment requirements and have the system synthesize them.

### The Discovery-Curation Tension

Registry-based discovery and template-based curation are complementary, not competing:

- **Templates curate**: The template author selects tools known to work for the domain. Static, reliable, tested.
- **Registries discover**: At runtime, an agent can query a registry for tools not in the template. Dynamic, broader, untested.

The tension: discovered tools haven't been tested with the template's quality gates, verification modalities, or compose conventions. A tool discovered at runtime might work perfectly or might produce outputs the coordinator can't evaluate.

Resolution (DB-11): templates use static tool lists today. Registry discovery is a future capability that requires:
1. A trust model for discovered tools (who verified this tool works?)
2. Integration with the quality gate (can the gate evaluate outputs from this tool?)
3. Sandbox execution (discovered tools run in isolation until validated)

### Clou Implication

MCP registries are relevant infrastructure for Clou's future evolution. The template schema (DB-11) is designed to accommodate this: tool lists today can become capability declarations tomorrow, with a registry resolver replacing static enumeration. But the initial implementation uses static lists — the registry infrastructure isn't mature enough to depend on.

The discovery-curation tension maps directly to DB-11's capability axes: high tool availability domains (software) can curate effectively; low tool availability domains might need discovery to fill gaps.

**Sources:**
- MCP Gateway Registry, agentic-community: [github.com/agentic-community/mcp-gateway](https://github.com/agentic-community/mcp-gateway)
- ScaleEnv, 2025: [arXiv:2505.11389](https://arxiv.org/abs/2505.11389)
- Anthropic, "Agent Interoperability Framework," 2026

---

## 15. Decomposition Topology

How task graphs are shaped — their width (parallelism) versus depth (sequential chaining) — determines both wall-clock execution time and the failure surface of multi-agent systems. Recent work on LLM-based planning reveals a systematic bias toward narrow topologies and identifies principled interventions.

### The Width-Defaulting Problem

Shi, Zheng, and Lou (UCF, 2025) introduced LAMaS (LLM-Augmented Multi-Agent Scheduling), demonstrating that without explicit critical-path supervision, LLM planners consistently default to narrow, deep topologies even when the problem structure admits significant parallelism. The mechanism is straightforward: training data overwhelmingly contains sequential plans (step-by-step instructions, linear narratives, serialized procedures). The model's pattern completion faithfully reproduces this distribution bias.

When LAMaS introduced explicit width guidance — prompting the planner to identify independent workstreams and express them as parallel branches — critical path length decreased by **38-46%** across benchmark scheduling problems. The improvement came not from better individual task decomposition but from restructuring the same tasks into wider graphs. The tasks were identical; only the topology changed.

This finding has a direct analog in Clou's observed behavior: task graphs consistently contain 2 nodes (implement, verify) regardless of milestone complexity. A milestone touching three independent modules still produces a serial chain, not because the work is inherently sequential but because the planner defaults to depth.

### Parallel DAG Construction and Pipelined Execution

Kim et al. (ICML 2024) presented LLMCompiler, which constructs DAGs from natural language task descriptions and dispatches parallel function calls automatically. Key results:

- **3.7x speedup** over sequential ReAct-style execution on multi-tool benchmarks
- **~9% accuracy improvement** over ReAct baseline — parallelism was not just faster but more accurate, because it reduced cascading error propagation through sequential chains (cf. §9 compositionality failures at 2+ hops)
- Planning and execution can be **pipelined**: the planner emits partial DAGs while earlier nodes execute, rather than planning the full graph before execution begins

The pipelining insight is architecturally significant. A planner need not produce the complete task graph upfront — it can emit independent branches as they become identifiable, allowing execution to begin on ready nodes while planning continues. This reduces the latency cost of planning without sacrificing parallelism.

### Granularity Sweet Spots

Li et al. (2024) developed HiPlan, a hierarchical planning framework that empirically identified the decomposition granularity that maximizes plan quality. Their central finding: **milestone-level granularity is the sweet spot** for LLM-based decomposition.

- **Action-level** decomposition (individual tool calls, single file edits) produces too many nodes. The planner's DAG reasoning degrades with node count — NLGraph (NeurIPS 2023) found frontier models handle 5-20 node DAGs at >95% accuracy, but accuracy drops as graphs grow. Action-level plans routinely exceed this range.
- **Task-level** decomposition (entire feature implementations, full subsystem builds) is too coarse. Dependencies between subtasks are hidden inside opaque nodes, preventing the planner from identifying parallelism.
- **Milestone-level** decomposition — chunks of work that produce a verifiable intermediate artifact — preserves enough structure for dependency reasoning while keeping node counts within the model's reliable DAG capacity.

This maps directly to Clou's phase-level decomposition. Each phase in compose.py is a milestone-level unit: small enough to complete in one EXECUTE cycle, large enough to produce a verifiable artifact (execution.md). The phase granularity was chosen for operational reasons (session-per-cycle, quality gate boundaries); HiPlan provides independent empirical validation that this granularity also optimizes plan quality.

### Principled Stopping Criteria for Decomposition

Zhou et al. (Columbia, 2025) proposed ACONIC, which uses **treewidth** — a graph-theoretic measure of structural complexity — as a principled stopping criterion for hierarchical decomposition. The question ACONIC addresses: when should a planner stop decomposing and start executing?

Key results:

- **9-40% improvement** in task completion when decomposition depth was matched to problem complexity via treewidth analysis
- Over-decomposition (too many fine-grained subtasks) degraded performance as severely as under-decomposition (too few coarse tasks)
- Treewidth provides a measurable signal: problems with high treewidth (many interacting constraints) benefit from deeper decomposition; problems with low treewidth (mostly independent subproblems) should be decomposed shallowly and executed in parallel

The practical implication: decomposition should not follow a fixed rule ("always decompose to N levels") but should adapt to the structural complexity of the specific problem. A milestone that touches one file with one concern needs minimal decomposition. A milestone that touches five independent modules needs wider decomposition, not deeper.

### Clou Implication

These four findings converge on a concrete design response: the coordinator's PLAN cycle (coordinator-plan.md) now includes explicit width-aware decomposition guidance.

**From LAMaS — explicit width guidance in the planning prompt.** Without it, the planner defaults to serial chains regardless of problem structure. The coordinator-plan.md guidance tells the planner to identify independent workstreams (changes to different files or modules that don't depend on each other's outputs) and express that independence via `gather()` in compose.py. This directly addresses the width-defaulting bias that LAMaS documented.

**From LLMCompiler — gather() as the parallel dispatch primitive.** Clou's `gather()` in compose.py is architecturally equivalent to LLMCompiler's parallel function dispatch. Independent phases placed in a `gather()` group execute concurrently, reducing wall-clock time proportional to the parallelism. The 3.7x speedup LLMCompiler achieved represents an upper bound — Clou's improvement depends on how much independence exists in a given milestone.

**From HiPlan — phase-level decomposition is the right granularity.** Clou's existing phase structure already operates at the granularity HiPlan identified as optimal. Each phase produces a verifiable artifact, keeps DAG node counts within the 5-20 node range where frontier models reason reliably (NLGraph, NeurIPS 2023), and avoids the extremes of action-level noise and task-level opacity. The width-aware guidance does not change the granularity — it changes the *topology* at that granularity, from serial chains to width-proportional graphs.

**From ACONIC — decomposition depth should match problem complexity.** The coordinator guidance includes a "when NOT to parallelize" clause: when the scope is genuinely single-dimensional (one file, one concern), a narrow graph is correct. This operationalizes ACONIC's treewidth insight without requiring formal treewidth computation — the planner reasons about whether the milestone's scope involves independent or interdependent workstreams.

**Connections to existing research foundations:**
- §9 (compositionality breaks at 2+ hops): Width reduces chain length. A gather() group with three parallel branches has critical path length 1, not 3. Shorter chains mean fewer opportunities for cascading composition failures.
- §10 (CoThinker / cognitive load theory): Multi-agent overhead hurts on simple tasks. The "when NOT to parallelize" clause preserves this — a narrow graph for a simple milestone is a feature, not a failure.
- §11 (satisficing): One decomposition, verify, proceed. The planner produces one topology (with appropriate width), validates it via AST parsing, and executes. It does not compare alternative topologies.
- NLGraph capacity (DB-02): Frontier models handle 5-20 node DAGs at 95%+ accuracy. Phase-level decomposition with gather() produces graphs well within this capacity.

**Sources:**
- Shi, Zheng, Lou, "LAMaS: LLM-Augmented Multi-Agent Scheduling," UCF, 2025: [arXiv:2601.10560](https://arxiv.org/abs/2601.10560)
- Kim et al., "LLMCompiler: An LLM Compiler for Parallel Function Calling," ICML 2024: [arXiv:2312.04511](https://arxiv.org/abs/2312.04511)
- Li et al., "HiPlan: Hierarchical Planning for LLM-Based Agents," 2024: [arXiv:2508.19076](https://arxiv.org/abs/2508.19076)
- Zhou et al., "ACONIC: Adaptive Complexity-Guided Decomposition," Columbia, 2025: [arXiv:2510.07772](https://arxiv.org/abs/2510.07772)
- Wang et al., "NLGraph: Natural Language Is All You Need for Graph Reasoning," NeurIPS 2023: [arXiv:2305.10037](https://arxiv.org/abs/2305.10037)

---

## Summary: Research-Grounded Design Principles

| Research Finding | Clou Design Response |
|---|---|
| Context volume alone degrades reasoning | Session-per-cycle with targeted read sets |
| First tokens are architecturally privileged (attention sinks) | Most important constraint first in system prompt |
| Middle of context is a dead zone | Keep prompts short; structure with XML tags |
| System prompts have no inherent priority over user messages | Social cues and instruction clarity matter more than position |
| Instruction density degrades performance beyond threshold | Minimize discrete instruction count in system prompt |
| Structured formats (XML) outperform prose | Use XML tags in Clou prompts |
| Decomposition outperforms monolithic prompts | Per-cycle-type prompts, not one mega-prompt |
| Role prompting doesn't improve accuracy | Brief identity anchor, not elaborate persona |
| LLMs can't self-verify plans | External verification: AST for compose.py, Brutalist for quality |
| Compositionality fails at 2+ hops, cascading through chains | Structured intermediate artifacts (execution.md), explicit ASSESS cycle |
| Blackboard architectures outperform master-slave | `.clou/` directory as shared medium |
| Observation masking > LLM summarization | Golden context as structured files, not compressed conversation |
| Memory improvements > model scaling | Golden context is the memory architecture |
| Forgetting is computationally useful | Session-per-cycle implicitly forgets within-cycle reasoning |
| Event segmentation enables structured memory | Phases as explicit boundaries, git at phase completion |
| 79% of multi-agent failures are specification/coordination | compose.py, typed function signatures, structured execution.md |
| The harness is the hard part, not the model | Orchestrator, golden context, quality gates are the engineering investment |
| Agent accuracy collapses past ~30 tools | Template-level tool curation; no agent gets all tools |
| Tool creation is decomposable (80% success) | Future: templates declare capabilities, not specific tools |
| MCP registries enable semantic tool discovery | Future: registry resolver replaces static tool lists in templates |
| Attention is associative memory (Hopfield equivalence) | Curated read sets select the retrieval pattern library; same design constraint as §1 through different mechanism |
| Intermediate tokens are not reasoning (Kambhampati 2025) | External verification is the only reliable signal; decisions.md is not evidence of reasoning |
| Reasoning models improve quantitatively, not qualitatively | Quality gates remain non-negotiable even as models improve |
| CLT: multi-agent overhead hurts on simple tasks | "Start serial, earn parallel"; parallelism only when complexity warrants |
| Failure-driven re-decomposition +28-33% (ADaPT) | Template-level optimization for ASSESS→rework: decompose finer on failure |
| Satisficing outperforms optimization for bounded agents | One decomposition, verify, proceed; no plan comparison |
| Situated cognition: plans as resources | Session-per-cycle + EXECUTE→ASSESS→rework loops; plans adapt to execution reality |
| RPD prototype matching ≈ transformer pattern retrieval | Quality of decomposition tracks training data density (DB-11 capability axis) |
| Supervisor-user dialogue is the sensemaking mechanism | No separate exploration phase at the domain-agnostic level; domain-specific exploration is a template concern |
| LLM planners default to narrow/deep topologies (LAMaS) | Explicit width guidance in coordinator-plan.md planning prompt |
| Parallel DAG construction yields 3.7x speedup (LLMCompiler) | gather() in compose.py for independent workstreams |
| Milestone-level granularity is the decomposition sweet spot (HiPlan) | Phase-level decomposition: verifiable artifacts within DAG capacity |
| Treewidth as decomposition stopping criterion (ACONIC) | "When NOT to parallelize" clause; depth matches problem complexity |
