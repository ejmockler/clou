# Chrome DevTools Protocol Integration

## Overview

Clou's browser verification modality uses the Chrome DevTools Protocol (CDP) directly, wired through an MCP server. This replaces a higher-level browser automation framework with raw protocol access — the same approach OpenAI used in Codex: "We wired the Chrome DevTools Protocol into the agent runtime and created skills for working with DOM snapshots, screenshots, and navigation."

**Package:** `chrome-devtools-mcp` on npm (Google's official MCP server)
**Transport:** stdio, launched via `npx -y chrome-devtools-mcp@latest`

## Why CDP Over Playwright

| Factor | CDP | Playwright |
|---|---|---|
| Perception primitive | `Accessibility.getFullAXTree` — semantic, structured, token-efficient | `browser_snapshot` — same data, extra framework hop |
| Speed | 5x faster element extraction (Browser-use measured) | Node.js WebSocket hop per CDP call |
| Surface area | Exactly the calls we need | Full browser automation API (distractors) |
| Debugging | Protocol-level errors | Framework abstraction errors |
| Session reuse | Attach to running Chrome (state intact) | Typically fresh instance |
| Dependency | Chrome/Chromium only | Cross-browser |

The tradeoff is clear: Playwright handles crash recovery, auto-waiting, and multi-browser. CDP gives speed, directness, and minimal token waste. For agent verification where Chromium is the target and the verifier controls the environment, CDP wins.

## Core Skills (Three-Skill Pattern)

Following the Codex pattern, three primitives cover verification:

| Skill | CDP Domain | Purpose |
|---|---|---|
| **Accessibility snapshot** | `Accessibility.getFullAXTree` | Semantic page structure — the primary perception method. Returns a tree of roles, names, values, states. Far more token-efficient than raw DOM HTML. |
| **Screenshot** | `Page.captureScreenshot` | Visual evidence for handoff and Brutalist `roast_product`. Supplementary to the a11y tree, not primary. |
| **Navigate** | `Page.navigate` | Move through the application. Combined with a11y snapshots, this is how the verifier walks golden paths. |

## Extended Skills

Added when concrete verification needs exceed the core three:

| Skill | CDP Domain | Purpose |
|---|---|---|
| Click | `Input.dispatchMouseEvent` | Interact with page elements identified via a11y tree |
| Type | `Input.dispatchKeyEvent` | Fill form fields, enter text |
| Evaluate JS | `Runtime.evaluate` | Execute assertions, read state, trigger actions |
| Console messages | `Log.entryAdded` / `Runtime.consoleAPICalled` | Detect runtime errors, warnings, failed assertions |
| Network responses | `Network.getResponseBody` + `Network.responseReceived` | Verify API calls, catch CORS/404s, confirm data flow |

## Usage in Clou Verification

### Path Walking Pattern

```
1. navigate → target URL
2. accessibility_snapshot → read semantic page structure
3. Evaluate: does the a11y tree show expected content/controls?
4. click / type → interact with elements (identified by a11y name/role)
5. accessibility_snapshot → verify interaction produced expected result
6. screenshot → capture visual evidence at key state transitions
7. Document findings in execution.md, store artifacts in artifacts/
```

### Why the A11y Tree is Primary

The accessibility tree is the most LLM-friendly representation of page state:
- **Structured:** roles, names, values, states — not pixel data
- **Semantic:** "button 'Submit'" not "div.btn-primary"
- **Token-efficient:** fraction of the tokens vs. raw HTML or screenshot description
- **Tests accessibility as a side effect:** if the a11y tree is broken, that's a finding
- **Stable across styling changes:** layout CSS changes don't affect the tree

Screenshots are evidence, not the perception method. The verifier reasons about the a11y tree and captures screenshots to document what it saw.

### Agentic Testing (Not Scripted)

The verifier drives Chrome interactively via CDP, making decisions based on what the a11y tree shows. It does NOT write test scripts.

```
Verifier: Navigate to http://localhost:5173
→ accessibility_snapshot → sees: heading "Login", textbox "Email",
  textbox "Password", button "Sign in"
Verifier: Login form is present. Filling test credentials.
→ click on "Email" textbox → type "test@example.com"
→ click on "Password" textbox → type "password123"
→ click on "Sign in" button
→ accessibility_snapshot → sees: heading "Dashboard", text "Welcome, Test User",
  navigation with links "Orders", "Settings", "Logout"
Verifier: Login successful. Dashboard shows user name and navigation.
→ screenshot → artifacts/login-success.png
```

## Configuration

CDP MCP is added as an MCP server available to coordinator sessions (which run the verifier as a subagent):

```python
_CDP_MCP: Any = {
    "command": "npx",
    "args": ["-y", "chrome-devtools-mcp@latest"],
    "type": "stdio",
}

# In coordinator options:
mcp_servers={"brutalist": _BRUTALIST_MCP, "cdp": _CDP_MCP}
```

The verifier's tool list includes CDP tools:
```python
tools=[
    # Base tools (all modalities)
    "Read", "Write", "Bash", "Grep", "Glob", "WebSearch", "WebFetch",
    # CDP browser verification tools
    "mcp__cdp__navigate",
    "mcp__cdp__screenshot",
    "mcp__cdp__accessibility_snapshot",
    "mcp__cdp__evaluate_javascript",
    "mcp__cdp__click",
    "mcp__cdp__type",
    "mcp__cdp__network_get_response_body",
    "mcp__cdp__console_messages",
]
```

## Chrome Lifecycle

CDP connects to a Chrome instance via WebSocket. The MCP server handles Chrome lifecycle:

1. **Launch:** MCP server starts Chrome with `--remote-debugging-port`
2. **Connect:** Attaches to the debugging WebSocket
3. **Use:** Agent sends CDP commands through MCP tools
4. **Cleanup:** MCP server closes Chrome when the session ends

The verifier does not manage Chrome directly. The MCP server abstracts the connection. If Chrome crashes, the MCP tool call fails, which the verifier reports in execution.md as a blocking finding.

## What CDP Does NOT Replace

- **HTTP verification (curl):** For API-only milestones, Bash + curl is sufficient. CDP is Browser modality only.
- **Unit/integration tests:** CDP verifies user-facing behavior. Tests verify code behavior.
- **Brutalist assessment:** CDP captures evidence. Brutalist evaluates quality. The coordinator invokes `roast_product` on the verifier's CDP-captured artifacts.

## Limitations

- **Chromium only.** Firefox deprecated CDP support. If cross-browser matters, this is a gap. For development verification, Chromium is sufficient.
- **No auto-waiting.** CDP does not wait for elements to appear. The verifier must handle timing via `Runtime.evaluate` polling or explicit waits. The MCP server may provide convenience wrappers.
- **Crash recovery is manual.** If Chrome crashes mid-verification, the verifier must report the failure. The coordinator restarts verification.
