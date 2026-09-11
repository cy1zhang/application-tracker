# Application Tracker — Product Requirements Document

Status: Draft for review
Date: 2026-09-11

## 1. Purpose

Build a hosted application tracker that turns recruiting emails into an organized, private dashboard. Internships are the primary use case; co-ops and full-time/new-grad roles are also supported. Gmail sync runs daily even when the user's computer is off.

Success means users can see where each application stands, distinguish recruiting cycles, trace changes to emails, and correct mistakes without repeatedly entering routine updates.

## 2. Confirmed decisions

- Google sign-in with isolated data for each user.
- One connected Gmail account per user in v1 (proposed scope limit).
- Import the preceding 90 days on first connection; sync daily afterward.
- Dashboard defaults to internships, with filters for all supported role types.
- Track season and year separately from application date.
- Rules handle obvious emails; AI handles ambiguous cases; unresolved cases require review.
- Bring your own key (BYOK) for OpenAI, Anthropic, or Google Gemini.
- AWS hosting and a public GitHub repository.
- PRD and technical design reviewed before implementation.

## 3. User experience

### Onboarding

1. Sign in with Google.
2. Separately authorize read-only Gmail access, with an explanation of what is processed.
3. Optionally select an AI provider and supply its API key. Explain that relevant email text may be sent to this provider and usage is billed by the provider.
4. Start the 90-day import and show progress, errors, and completion.
5. Open the dashboard, initially filtered to internships.

Declining Gmail access leaves manual tracking available. Omitting an AI key leaves rules active and routes ambiguous cases to review.

### Dashboard

Proposed initial layout: summary counts and a sortable application table. A board view is outside v1.

Each row shows company, role, role type, cycle, status, application date when known, and last update. Filters cover type, season/year, status, and company/role search. Show last successful sync, current sync state, a Sync now action, and pending review count.

An application detail view provides editable fields, a chronological change history, and source email links. Users can manually create applications, including those that produced no email acknowledgment.

### Review queue

Show the proposed change, supporting email link, and a short explanation of the ambiguity. Users can accept, edit, reject, create a distinct application, or attach the event to an existing application. Rejected proposals must not reappear from the same email unless explicitly retried.

### Settings

Connect/disconnect Gmail; select, replace, or delete an AI key; enable/disable AI processing; view sync issues; delete account data. Never display a saved key in full.

## 4. Application model

| Field | Requirement |
|---|---|
| Company and role | Preserve extracted names; allow corrections; never invent missing values |
| Role type | Internship, co-op, full-time/new-grad, or unknown |
| Cycle | Season (Summer, Fall, Spring, Winter, or unknown) plus year, independently nullable |
| Status | Applied, OA received, Interviewing, Offer, Rejected, Withdrawn |
| Application date | Actual date if supported by evidence; otherwise unknown |
| Source | Gmail message/thread identifiers and a link, or manual entry |
| History | Timestamped changes with previous/new values and origin: rules, AI, or user |

An email received in September 2026 can describe Summer 2027. Do not infer the cycle from the email date. For winter spanning two years, retain explicit source wording and request a year when ambiguous. Full-time roles need not have a seasonal cycle.

Company alone is never a unique application identifier: the same company may have multiple roles and cycles. Prefer requisition identifiers and existing thread associations; otherwise require enough corroborating evidence or ask for review.

Statuses are not a simple numeric ladder. An interview can occur without an OA, and a later rejection can follow an interview or offer. Older messages must not regress newer state. Reopening a terminal status and conflicts with manual corrections require review.

## 5. Email processing and AI tiers

### Tier 1: deterministic rules

Use recruiting phrases, known sender/template patterns, role/requisition identifiers, and thread context. Rules must distinguish genuine application events from job ads, newsletters, and hypothetical statements. Only apply a change automatically when both the event and application association are clear.

### Tier 2: BYOK AI

Send only relevant candidate email content and minimal application context to the selected provider. Do not send the entire inbox. Extract typed fields and evidence; validate the output before proposing or applying changes. Confidence claimed by a model is not sufficient by itself for automatic updates.

Evaluate fields separately: an interview may be clear while its cycle remains unknown. AI must not fabricate dates or cycles, execute instructions inside emails, or access tools. Invalid output, contradictory evidence, ambiguous application matches, or missing evidence go to review.

Proposed initial controls: maximum 50 AI requests per user per UTC day, including retries, with at most two attempts per message per run. Display that this is a request cap, not a guaranteed monetary cap. Excess cases remain reviewable and can be retried explicitly.

### Tier 3: manual review

Use when a key is absent, invalid, over quota, or the interpretation remains uncertain. A provider outage must not prevent clear rule-based updates or mark unprocessed emails as successfully handled.

## 6. Sync behavior

- Initial import covers the 90 days preceding connection, with a fixed start boundary per import.
- Search for candidate recruiting emails; acknowledge that heuristic search cannot guarantee every relevant email is found. Support manual entry for misses.
- Daily sync is scheduled independently of the browser. Proposed timing: 09:00 UTC, with completion shown in the user's local timezone.
- Sync now starts background work and returns promptly; overlapping requests are deduplicated.
- Process imports in resumable batches; retries must not duplicate applications or history.
- Track partial failure separately from success. Show reconnect-required state for revoked/expired Gmail authorization.
- Disconnect stops future reads and deletes Gmail credentials. Existing tracker records remain until the user deletes them or the account.

## 7. Privacy and public release

Every read/write is scoped to the authenticated owner. Encrypt stored credentials; never expose keys, refresh tokens, email bodies, or private application data in logs, source control, browser analytics, or error reports.

Do not retain full raw email bodies after processing. Store only necessary application fields, message references, and concise evidence excerpts (proposed maximum 500 characters per event). Account deletion removes active application data, review items, credentials, and login account; operational logs contain no email content and expire after 14 days. Backups, if later enabled, need a documented expiry before release.

Gmail read-only access is a restricted scope. Public production access requires determining and completing applicable Google verification and security-assessment requirements; publishing the repository does not satisfy these. External OAuth apps in Testing generally receive Gmail refresh tokens that expire after seven days, so test deployments must surface reconnect needs.

Before public onboarding, verify each AI provider's applicable data-use terms and compatibility with Google user-data requirements. BYOK does not remove the application's obligations. Publish accurate privacy/deletion information and obtain consent for AI processing.

## 8. Acceptance criteria

1. User A cannot access User B's applications, settings, reviews, syncs, or source references by changing identifiers.
2. Initial import excludes messages older than its fixed 90-day boundary; repeat processing creates no duplicate event.
3. A September email explicitly naming Summer 2027 produces that cycle; an unspecified cycle stays unknown.
4. Two roles or cycles at one company remain separate unless evidence supports a match.
5. Clear application, assessment, interview, offer, and rejection fixtures produce the expected status without an AI call.
6. Ambiguous fixtures use only the selected user's configured provider; unresolved cases appear in review without an unsupported update.
7. Missing/invalid keys and provider failures preserve deterministic processing and expose actionable review/error states.
8. Older emails do not overwrite newer status; conflicting automation does not silently replace a manual correction.
9. A scheduled sync completes in a deployed environment with no browser open; failed batches resume without duplicated changes.
10. Gmail disconnection prevents queued work from reading mail; account deletion prevents queued work from restoring data.
11. Corrections and review decisions retain an audit history and link to the originating email where applicable.
12. Repository and build outputs contain no real credentials or user email data.

Use synthetic fixtures for automated tests; use explicitly connected test accounts for deployment verification.

## 9. Scope exclusions

No auto-applying, email sending, calendar booking, resume editing, spreadsheet integration, attachment parsing, multiple mailboxes per user, OpenRouter, or mobile app in v1. No subscription billing. No guarantee of recovering applications without relevant emails.

## 10. Delivery and decisions

1. Review this PRD and technical design.
2. Implement and test dashboard, account isolation, and manual tracking.
3. Add Gmail import, rules, review queue, and scheduled processing.
4. Add the three BYOK providers and validate with synthetic fixtures.
5. Deploy a controlled test environment; verify live sync and deletion.
6. Complete public-launch requirements before unrestricted onboarding.

Proposed defaults for review: one mailbox, table dashboard, 09:00 UTC schedule, 50 daily AI requests, and the retention rules above. AWS account/region, domain, budget alert threshold, OAuth application credentials, and provider model IDs are deployment configuration to resolve before provisioning. No cloud resources have been created by this document.

## References

- [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [Google OAuth token expiry in Testing](https://developers.google.com/identity/protocols/oauth2)
- [Restricted-scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)
