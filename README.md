# Clou

Clou is an orchestrator for structured work — it manages a three-tier hierarchy of agent sessions, persistent planning state, quality gates, and verification. The planning layer — not generation — is the bottleneck in agentic systems. Clou maintains a persistent, human-readable golden context (`.clou/`) that serves as both the agent's working memory and the human's legibility surface.

## Prerequisites

Clou requires the [Claude CLI](https://docs.anthropic.com/en/docs/claude-code):

```bash
npm install -g @anthropic-ai/claude-code
```

## Installation

```bash
pipx install clou-ai
```

Or with pip:

```bash
pip install clou-ai
```

## Auth Setup

Verify that your Claude CLI is authenticated:

```bash
clou auth
```

If not logged in, the command will guide you through setup.

## Quick Start

```bash
# Initialize a project in the current directory
clou init

# Launch the TUI
clou

# Resume the most recent session
clou --continue

# Resume a specific session
clou --resume SESSION_ID
```

## License

[Apache 2.0](LICENSE)
