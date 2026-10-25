# Plan 3: Mac Background Worker

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run local Ollama on the AI queue automatically whenever the Mac is on, with no browser tab and no AWS admin credentials.

**Architecture:** A `local_tools worker` command signs in with Plan 1's Keychain token and works through the AI queue sequentially. It pulls queued emails, gets each prompt, runs Ollama and posts the result, using the error policy proven in the 2026-10-21 run. A user LaunchAgent starts it at login and every 30 minutes, and a lock file prevents overlapping runs. The browser panel moves to the same queue routes for a manual "run now".

**Tech Stack:** Python 3.12 + uv (`local` group), macOS launchd, Ollama HTTP API, React/TypeScript.

**Spec:** `docs/superpowers/specs/2026-10-24-auto-apply-sheet-design.md` (section 4; "Mac job sign-in").

## Global Constraints

- The worker prints and logs only an index, an HTTP status, a detail code, an outcome category, elapsed time and totals. It never prints email text, model output, ids or tokens. The log file is `~/Library/Logs/application-tracker/worker.log`.
- Ollama is called with the same body as Plan 1's `OllamaProvider`. The worker uses no cache in production.
- Installing the LaunchAgent changes the owner's macOS login configuration. The operator asks before running `install`.
- The worker sends no request when the mode is `off`, because the queue routes return 409 `auto_apply_off`. It exits 0 quietly.

## Review Focus

- **The Mac sleeps mid-request.** On wake, the in-flight request fails as a per-item failure. It is retried on the next run, and no duplicate is created, thanks to `save_plan`'s no-existing-plan condition. Covered in Task 1.
- **Two runs overlap** (LaunchAgent plus a manual run). The second run exits 3 with "already running". A stale lock older than 2 hours is taken over. Covered in Task 2.
- **Ollama is not running at login.** The worker retries the connection 3 times, 30 s apart, then exits with code 2 without marking items failed. Covered in Task 1.
- **The refresh token has expired** (30 days). The worker exits with code 4 and the log line `login required`. The browser panel keeps working. Covered in Task 2.
- **The queue is empty.** One request is made and the worker exits 0 in under 2 s. Ollama is not called and the model is not loaded. Covered in Task 1.

---

### Task 1: Worker loop

**Files:**
- Create: `backend/local_tools/worker.py`, `backend/local_tools/ollama.py`. Move `OllamaClient` here from Plan 1's `evaluation/answer_key/ollama.py`, which then imports it.
- Test: `backend/tests/test_local_worker.py`

**Interfaces:**
- Consumes: `TrackerApi` and `ApiError` (Plan 1, Task 2), and `GET /ai-queue`, `GET /ai-queue/{id}/input` and `POST /ai-queue/{id}/result` (Plan 2, Task 7).
- Produces:
  - `OllamaClient(base_url="http://127.0.0.1:11434", model="gemma4:latest", timeout=300)`.
    - `.chat(system, user, format) -> dict` raises `OllamaUnreachable` or `OllamaFailed(kind)`.
    - `.reachable(retries=3, delay=30) -> bool`.
  - `RunSummary(saved: int, executed: int, skipped: int, failed: int, stopped: str | None)`.
  - `run_once(api, ollama, *, limit: int | None = None, log=print) -> RunSummary`.
- The error policy matches the resume runner from 2026-10-21:
  - **Per-item** (counted, then continue): 404, 422, 409 `review_changed`, 503 `message_unavailable`, 500/504, invalid JSON, an Ollama HTTP error, an Ollama timeout.
  - **Stop:** Ollama unreachable after retries, 401/403 after a refresh, 409 `gmail_connection_changed`, 503 `gmail_connection_unavailable`, 429/502, 409 `auto_apply_off`.
  - Failed ids are skipped for the rest of the run.

- [ ] **Step 1: Write failing tests** with a fake api and fake Ollama:
  - `test_empty_queue_makes_one_request_and_no_ollama_call`.
  - `test_processes_queue_in_order_and_counts`.
  - `test_per_item_failures_continue`, one case per code.
  - `test_global_stops`, one case per code.
  - `test_auto_apply_off_exits_quietly`.
  - `test_log_lines_contain_no_payload_text`: feed a sentinel string through subject, body and model output, and assert it never appears in the captured log.
- [ ] **Step 2: Run** `uv run --group local pytest tests/test_local_worker.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the loop and client.
- [ ] **Step 4: Run** the tests and `ruff`. Expected: pass.
- [ ] **Step 5: Commit** `feat: add local Ollama queue worker`.

### Task 2: CLI, lock, exit codes

**Files:**
- Modify: `backend/local_tools/__main__.py` (subcommand `worker [--limit N]`)
- Create: `backend/local_tools/lock.py`
- Test: `backend/tests/test_local_worker_cli.py`

**Interfaces:**
- Produces:
  - `acquire_lock(path=~/Library/Application Support/application-tracker/worker.lock, stale_after=7200) -> Lock | None`. It writes the pid and a timestamp, and takes over a stale lock.
  - Exit codes:
    - 0: done, empty queue, or mode off.
    - 2: global stop.
    - 3: lock held.
    - 4: login required.
  - The worker appends one summary line per run to the log file.

- [ ] **Step 1: Write failing tests:**
  - `test_second_instance_exits_3`.
  - `test_stale_lock_taken_over`.
  - `test_login_required_exits_4`.
  - `test_summary_line_appended_to_log`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the CLI and lock.
- [ ] **Step 4: Run** the tests. Expected: pass.
- [ ] **Step 5: Commit** `feat: worker CLI with lock and exit codes`.

### Task 3: LaunchAgent and docs

**Files:**
- Create: `backend/local_tools/launchagent.py` (subcommands `install`, `uninstall`, `status`)
- Modify: `docs/DEVELOPMENT.md` (section "Automatic local analysis": login, install, status, logs, uninstall)
- Test: `backend/tests/test_launchagent.py`

**Interfaces:**
- Produces: `render_plist(repo_backend_dir: Path, uv_path: Path) -> bytes`. The plist has:
  - Label `com.applicationtracker.worker`.
  - `ProgramArguments: [uv, run, --group, local, python, -m, local_tools, worker]`.
  - `WorkingDirectory` set to the backend dir.
  - `RunAtLoad: true` and `StartInterval: 1800`.
  - Standard out and standard error sent to the log file.
- `install` writes to `~/Library/LaunchAgents/com.applicationtracker.worker.plist` and runs `launchctl bootstrap gui/<uid>`.
- `uninstall` runs `bootout` and deletes the file.

- [ ] **Step 1: Write failing tests:**
  - `test_plist_is_valid_and_has_interval_and_runatload`: parse it with `plistlib`.
  - `test_install_and_uninstall_call_launchctl`: mock `subprocess`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the LaunchAgent commands and the docs section.
- [ ] **Step 4: Run** the tests. Expected: pass.
- [ ] **Step 5: Commit** `feat: install the local worker as a LaunchAgent`.
- [ ] **Step 6 (operator, after the owner says yes):**
  1. Set the mode to `plan` (`PUT /settings/auto-apply`).
  2. Run `POST /ai-queue/backfill`.
  3. Run `worker --limit 5` by hand and check the summary.
  4. Run `launchagent install`.
  5. Record the backlog progress in counts only.

### Task 4: Browser panel on the AI queue

**Files:**
- Modify: `frontend/src/reviews/LocalOllamaPanel.tsx`, `frontend/src/reviews/localOllama.ts`, `frontend/src/api/client.ts` (`listAiQueue`, `getAiQueueInput`, `submitAiQueueResult`), `frontend/src/api/types.ts`
- Test: `frontend/src/reviews/ReviewQueue.test.tsx` (or a new `LocalOllamaPanel.test.tsx`)

**Interfaces:**
- Consumes: Plan 2, Task 7 routes.
- When the mode is `off`, the panel keeps today's review-based behavior. When the mode is `plan` or `on`, it reads from the AI queue. The cap, Stop, and the failure policy are unchanged.

- [ ] **Step 1: Write failing tests:**
  - `test_panel_uses_ai_queue_when_mode_plan`.
  - `test_panel_uses_reviews_when_mode_off`.
  - `test_queue_result_counts_executed_in_on_mode`.
- [ ] **Step 2: Run** `cd frontend && npx vitest run src/reviews/`. Expected: FAIL.
- [ ] **Step 3: Implement** the change.
- [ ] **Step 4: Run** `npm run lint && npm test && npm run typecheck && npm run build`. Expected: pass.
- [ ] **Step 5: Commit** `feat: run the browser Ollama panel on the AI queue`.
