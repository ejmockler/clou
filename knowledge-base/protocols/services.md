# Services Protocol

## Principle

Mock at the boundary of your control, never within it. Third-party services use their own testing infrastructure — Stripe test mode, Auth0 dev tenants, Twilio test credentials, AWS sandbox accounts. The only acceptable mock is for external services that provide no testing infrastructure at all (rare).

Minimum viable human involvement, maximum clarity at each touchpoint. The agent writes the setup guide, the verification command, the expected environment variables. The human provides authorization. The agent confirms it worked.

## Service Lifecycle

### 1. Discovery

During the coordinator's planning phase, when it surveys the codebase and builds the task plan, it identifies third-party service dependencies.

For each service, the coordinator checks whether `.clou/services/<service-name>/status.md` exists and indicates the service is configured.

### 2. Setup Authoring

If a service is not configured, the agent writes:

**`services/<service-name>/setup.md`** — Precise, project-specific setup guide. Not generic documentation. Tailored to exactly what this project needs.

```markdown
# Service Setup: <service-name>

## What We Need
<Bulleted list of specific credentials, configurations, and tools required>

## Steps
1. <Exact step with URLs, commands, and expected output>
2. <Next step>
...

## Environment Variables
Add to `.env.local`:
```
KEY_NAME=<description of what goes here>
ANOTHER_KEY=<description>
```

## Verification
Run: `<exact command to verify credentials work>`
Expected output: <what success looks like>
```

**`services/<service-name>/.env.example`** — Template of expected environment variables with descriptive placeholders, no actual values.

**`services/<service-name>/status.md`** — Initial status:

```markdown
# Service Status: <service-name>

status: unconfigured
discovered_in: <milestone-name>
discovered_at: <timestamp>
required_by_phases:
  - <milestone>/<phase>
  - <milestone>/<phase>
verification_command: <command from setup.md>
last_verified: never
```

### 3. Escalation

The coordinator files a `credential_request` escalation with `degraded` severity:
- The escalation references `services/<name>/setup.md` for instructions
- It lists which tasks are blocked by the missing service
- It notes which tasks can proceed independently
- The coordinator parks dependent tasks and continues on independent work

### 4. User Resolution

The user follows `setup.md` — which might involve:
- Logging into a third-party dashboard
- Creating API keys
- Installing a CLI tool (`stripe`, `aws`, `gcloud`)
- Running an authentication flow (browser-based OAuth, device codes)
- Configuring webhook endpoints
- Setting up sandbox/test environments

The user provides credentials through:
- The supervisor conversation (supervisor writes to `.env.local` or equivalent)
- Direct file editing (updating `.env.local`)
- Updating `status.md` to indicate completion

### 5. Agent Verification

Before unblocking dependent tasks, the coordinator runs the verification command from `setup.md` to confirm credentials actually work.

- **Passes:** Update `status.md` to `configured`, unblock dependent tasks
- **Fails:** Re-escalate with specific error message, not a generic "didn't work"

Updated `status.md` after verification:

```markdown
# Service Status: <service-name>

status: configured
discovered_in: <milestone-name>
configured_at: <timestamp>
required_by_phases:
  - <milestone>/<phase>
verification_command: <command>
last_verified: <timestamp>
notes: <any caveats — e.g., "webhook signing secret rotates on CLI restart">
```

### 6. Cross-Milestone Reuse

Service configurations are project-level resources, not milestone-level. Once configured, a service is available to all subsequent milestones without re-asking.

When a later milestone needs the same service:
1. Coordinator checks `services/<name>/status.md`
2. If `status: configured`, runs verification command to confirm still valid
3. If still valid, proceeds without escalation
4. If invalid (expired token, revoked key), re-escalates with context about what changed

## Services Directory Structure

```
.clou/services/
├── stripe/
│   ├── setup.md          # Step-by-step: test mode keys, CLI, webhook forwarding
│   ├── status.md          # configured, last verified 2026-03-19
│   └── .env.example       # STRIPE_PUBLISHABLE_KEY, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET
│
├── auth0/
│   ├── setup.md          # Step-by-step: dev tenant, application setup, test users
│   ├── status.md          # configured
│   └── .env.example       # AUTH0_DOMAIN, AUTH0_CLIENT_ID, AUTH0_CLIENT_SECRET
│
└── aws-s3/
    ├── setup.md          # Step-by-step: sandbox account, bucket creation, IAM role
    ├── status.md          # unconfigured
    └── .env.example       # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET
```

## Handoff Integration

When the verification phase prepares `handoff.md`, it includes service state:

```markdown
## Third-Party Services

### Stripe (test mode)
status: connected
webhook forwarding: requires `stripe listen` running in a separate terminal
command: `stripe listen --forward-to localhost:3000/webhooks`
note: webhook signing secret rotates each time you restart the CLI

### Auth0 (dev tenant)
status: connected
note: test users are seeded — use test@example.com / password123
```

This ensures the user knows what's running, what needs a process in another terminal, and what state to expect during manual testing.

## What Makes a Good Setup Guide

The agent should write setup guides that:

1. **Are project-specific.** Not "go to Stripe docs." Instead: "Navigate to dashboard.stripe.com/test, go to Developers → API Keys, copy the publishable and secret keys."

2. **Include exact commands.** Not "install the CLI" but `brew install stripe/stripe-cli/stripe` (with alternatives for other package managers).

3. **State expected output.** "After running `stripe login`, you should see 'Your pairing code is: ...' and a browser window should open."

4. **Include a verification step.** Always. The agent must be able to confirm credentials work programmatically.

5. **Note caveats.** "The webhook signing secret changes every time you restart `stripe listen`. If tests fail after restarting, update `STRIPE_WEBHOOK_SECRET` in `.env.local`."

6. **Are idempotent.** Following the guide twice shouldn't break anything. If a step might fail on re-run, say so and provide the skip condition.
