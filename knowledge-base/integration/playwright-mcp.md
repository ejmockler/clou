# Playwright MCP Integration

> **Superseded by [CDP MCP](./cdp-mcp.md).** Browser verification now uses the Chrome DevTools Protocol directly via `chrome-devtools-mcp`. This document is retained for reference.

## Overview

Playwright MCP enables Claude to drive a real browser — navigating pages, clicking elements, filling forms, taking screenshots, and inspecting accessibility state. It was the original perception tool for the **Browser** verification modality in Clou's verification protocol.

## Available Tools

### Navigation
| Tool | Purpose |
|---|---|
| `browser_navigate` | Navigate to a URL |
| `browser_navigate_back` | Go back in browser history |
| `browser_tabs` | List open tabs |

### Interaction
| Tool | Purpose |
|---|---|
| `browser_click` | Click an element |
| `browser_fill_form` | Fill form fields |
| `browser_type` | Type text into focused element |
| `browser_select_option` | Select from dropdown |
| `browser_press_key` | Press keyboard key |
| `browser_hover` | Hover over element |
| `browser_drag` | Drag and drop |
| `browser_file_upload` | Upload files |
| `browser_handle_dialog` | Accept/dismiss dialogs |

### Observation
| Tool | Purpose |
|---|---|
| `browser_snapshot` | Get accessibility tree snapshot (primary observation method) |
| `browser_take_screenshot` | Visual screenshot |
| `browser_console_messages` | Read console output |
| `browser_network_requests` | Inspect network activity |

### Execution
| Tool | Purpose |
|---|---|
| `browser_evaluate` | Execute JavaScript in page |
| `browser_run_code` | Run Playwright code directly |
| `browser_wait_for` | Wait for element/condition |

### Management
| Tool | Purpose |
|---|---|
| `browser_install` | Install browser binary (must run first) |
| `browser_close` | Close browser |
| `browser_resize` | Resize viewport |

## Usage in Clou Verification

### Setup
Before verification, the verification agent must:
1. Call `browser_install` to ensure the browser binary is available
2. Environment must already be materialized (Stage 1 complete — dev server running)

### Path Walking Pattern

```
1. browser_navigate → target URL
2. browser_snapshot → read accessibility tree to understand page structure
3. Evaluate: does the page show what's expected?
4. browser_click / browser_fill_form → interact with elements
5. browser_snapshot → verify the interaction produced expected result
6. Continue through the flow
7. Document findings in execution.md
```

### Why Accessibility Snapshots Over Screenshots

`browser_snapshot` returns the accessibility tree — a structured representation of what's on the page. This is preferred over screenshots because:
- The agent can reason about structure, not pixels
- Element references are stable (accessibility labels vs. CSS selectors)
- It tests accessibility as a side effect
- It's more reliable across styling changes

Screenshots (`browser_take_screenshot`) are supplementary — useful for documenting visual state in `handoff.md` but not the primary observation method.

### Agentic Testing vs. Scripted Testing

Clou's verification agent does NOT write Playwright test scripts. It drives the browser interactively, making decisions about what to do next based on what it observes.

**Scripted test (NOT this):**
```javascript
await page.goto('/login');
await page.fill('#email', 'test@example.com');
await page.fill('#password', 'password');
await page.click('button[type="submit"]');
await expect(page.locator('.dashboard')).toBeVisible();
```

**Agentic test (this):**
```
Agent: "I need to verify the login flow. Let me navigate to the app."
→ browser_navigate to http://localhost:5173
→ browser_snapshot → sees a login form with email and password fields
Agent: "I see a login form. Let me log in with the test credentials."
→ browser_fill_form with email and password
→ browser_click on the submit button
→ browser_snapshot → sees a dashboard with user's name
Agent: "Login successful. I can see the dashboard with the user's name
       displayed. The navigation bar shows logged-in state."
```

The agent interprets what it sees rather than asserting against selectors. This makes verification resilient to UI changes that would break brittle test scripts.

## Constraints

### Browser Required
Playwright MCP needs a browser binary. `browser_install` must be called at least once. This adds startup time to verification.

### Browser Modality Only
Playwright MCP is used when the coordinator selects the **Browser** verification modality. Other modalities (HTTP, Shell, Code) use Bash — no Playwright required. The orchestrator includes Playwright MCP tools in the verifier's tool set only when the coordinator's plan calls for Browser modality. See [DB-09: Verification Generalization](../decision-boundaries/09-verification-generalization.md) for the full modality system.

### Element References
Playwright MCP uses `ref` attributes from the accessibility snapshot to target elements for interaction. The agent must `browser_snapshot` before clicking/typing to get valid refs.

### Single Browser Context
The default is one browser context. Testing multi-user scenarios (e.g., "user A sends message, user B receives it") requires careful sequencing or multiple browser contexts via `browser_run_code`.

### Performance
Browser-driven verification is slower than API tests. Each page load, interaction, and snapshot takes time. Verification plans should prioritize golden paths over exhaustive coverage.

## Handoff Documentation

When the verification agent documents flows in `handoff.md`, it translates its browser observations into human-readable walk-through steps:

**What the agent did:**
```
browser_navigate → http://localhost:5173/orders
browser_snapshot → table with 3 rows, columns: ID, Customer, Amount, Status
browser_click → "New Order" button
browser_snapshot → modal with form: Name, Amount, Description fields
browser_fill_form → {name: "Test Order", amount: "29.99"}
browser_click → Submit
browser_snapshot → modal closed, table now has 4 rows, new row at top
```

**What handoff.md says:**
```markdown
### Flow: Create Order
1. Navigate to http://localhost:5173/orders
2. You should see an orders table with existing test data (3 rows)
3. Click "New Order" — a modal should open with Name, Amount, and Description fields
4. Fill in "Test Order" for name and "29.99" for amount
5. Click Submit — the modal should close and the new order should appear at the top of the table
```
