# Automatic Tracking: Plan Index

**Spec:** `docs/superpowers/specs/2026-10-24-auto-apply-sheet-design.md` (owner-approved 2026-10-24)
**Branch:** `claude/auto-apply-sheet`, stacked on `codex/local-ollama-review`

The spec spans four subsystems. Each plan ships working, tested software on its
own. Execute them in this order:

| # | Plan | Delivers | Needs |
|---|------|----------|-------|
| 1 | `2026-10-24-auto-apply-1-answer-key.md` | Mac sign-in + API client, owner-scoped message routes, answer-key export, Codex labeling, baseline score | — |
| 2 | `2026-10-24-auto-apply-2-pipeline.md` | Filters, kind, matching, decisions, create/update, AI queue, plan report, apply, undo, new-pipeline score | 1 |
| 3 | `2026-10-24-auto-apply-3-mac-worker.md` | Background Ollama worker on the Mac, LaunchAgent, browser panel on the AI queue | 1, 2 |
| 4 | `2026-10-24-auto-apply-4-sheet.md` | Layout B sheet: Applications grid, Needs a look, AI changes, plan report + Apply | 2 (3 for live data) |

Plan 4 can start once Plan 2's Interfaces are merged on the branch; its tests use
mocked API responses.

## Rollout gates (from the spec, owner-operated)

1. Plan 1 done: answer key labeled; baseline score recorded in the Plan 1 file.
2. Plan 2 done: `evaluation.answer_key.score --pipeline auto --check` passes
   (automatic-change precision >= 0.98, application-email recall >= 0.90) on the
   answer key. Iterate filters/prompt/matching until it does.
3. Plan 3 done: the worker analyzes the backlog with mode `plan`.
4. Plan 4 done: the owner reads the plan report in the sheet and presses **Apply**.
   Only that action sets mode `on`.

## Agent coordination

Per `AGENTS.md`: one agent edits at a time, commit after each verified step, check
off steps in the plan files, record open decisions there. Never merge without the
owner's approval. Real email content never enters Git, logs, fixtures, or terminal
output; the answer key lives only under the git-ignored `.local/`.
