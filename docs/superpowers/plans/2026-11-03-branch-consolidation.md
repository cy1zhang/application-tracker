# Consolidate local work onto main

The owner requested consolidation and a push to GitHub so development can
continue on another computer. This authorizes integration into `main`.

## Inventory

One worktree exists. Every local branch is an ancestor of
`claude/auto-apply-sheet` at `eee7951`. No conflicting or independent branch
changes need combining. GitHub `main` was `7ebc3b1`; local `main` was `3c4f84f`.
There are 213 commits between GitHub `main` and `eee7951`.

| Branch | Tip | Work included |
| --- | --- | --- |
| `codex/milestone-4-hosted-auth-deploy` | `8be528e` | Hosted authentication and development deployment |
| `main`, `codex/milestone-6-deterministic-email` | `3c4f84f` | Gmail connection and deterministic email processing |
| `codex/milestone-7-review-reconciliation` | `533d579` | Review queue and application reconciliation |
| `codex/milestone-8-resilient-sync` | `b7eaa55` | Durable Gmail synchronization and recovery |
| `milestone-9-byok-ai` | `56591d8` | BYOK AI providers |
| `milestone-10-harden-evaluate` | `8820e70` | Logging, deletion, alarms, and synthetic evaluation |
| `codex/huggingface-provider` | `34e6f54` | Hugging Face provider integration |
| `codex/local-ollama-review` | `104a36d` | Local Ollama review suggestions |
| `claude/auto-apply-sheet` | `eee7951` | Automatic tracking, local worker, spreadsheet UI, and review cleanup |

## Decisions

- Use a fast-forward into `main`, preserving the complete commit history.
  Reason: all branches already form one stack. Cost if wrong: an omitted branch
  would lose visibility; verify every local tip is reachable from final `main`.
- Keep all existing branch names. Reason: the owner requested consolidation,
  not deletion. Cost: the local branch list remains long, but future work can
  start from `main` and the old names remain useful historical markers.
- Keep secrets, `.local` answer-key files, caches, and credentials out of Git.
  Another computer needs its own local setup and sign-in. No deployment or
  mailbox operations are part of this consolidation.
- Do not apply npm's suggested forced ESLint downgrade. `npm audit` reports
  11 moderate findings through `minimatch@10.2.6`, with the reported fix at
  `11.0.1`. This is an existing development dependency issue. A major override
  or lint-tool migration needs separate compatibility work; preserve the
  current application code and report the failing audit gate explicitly.

## Verification and integration

- [x] Fetch GitHub history and inventory all branches and worktrees.
- [x] Confirm every local branch tip is contained in `eee7951`.
- [x] Install locked backend dependencies and pass Ruff.
- [x] Pass all 1,284 backend tests and the synthetic evaluation gates.
- [x] Pass frontend lint, all 233 tests, TypeScript, and production build.
- [x] Validate SAM and pass all 14 offline deployment-script tests.
- [x] Run npm audit: fails with the dependency findings described above.
- [x] Preserve the pre-existing `HANDOFF.md` deletion in a named local stash;
  leave the committed file on GitHub pending the owner's choice. The stash is
  named `pre-consolidation: preserve pending HANDOFF.md deletion` and is local
  to this computer. It contains only that deletion, not application code.
- [x] Fast-forward local `main` to `eee7951` without changing application code.
- [ ] Push without force and verify GitHub SHA.

Environment: backend console scripts retain the pre-migration path, so tests
use `uv run python -m pytest`. The installed executable under `node@22` reports
Node 23; frontend checks use the official Node 22.23.3 archive under
`/private/tmp`, verified against its published SHA-256 checksum. No system
runtime or worker configuration is changed.
