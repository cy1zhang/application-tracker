# Plan 4: Spreadsheet View (layout B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the list/detail pages with a spreadsheet-style screen. It has an editable Applications grid and bottom tabs for **Needs a look** and **AI changes**, plus the plan report and the one-time **Apply**.

**Architecture:**
- **Layout:** `App.tsx` keeps auth and data loading, then renders a new `Sheet` shell with three bottom tabs.
- **Grid:** a plain `<table>` with click-to-edit cells that save through the existing versioned PATCH.
- **AI history:** markers and undo come from Plan 2's `/ai-changes`.
- **Needs a look:** this tab reuses `ReviewQueue`.
- **Settings:** the settings components move into a panel behind a Settings button.
- **Libraries:** no grid library.

**Tech Stack:** React 18, TypeScript, Vite, Vitest + Testing Library, plain CSS (`frontend/src/styles.css`).

**Spec:** `docs/superpowers/specs/2026-10-24-auto-apply-sheet-design.md` (section 5, layout B; section 6; section 8 step 3).

## Global Constraints

- **Columns, in order:** Company, Role, Type, Cycle, Status, Applied, Last update.
- **Tabs, in order, with these labels:** `Applications (N)`, `Needs a look (N)`, `AI changes (N)`.
- **Markers and undo:** a cell whose latest change came from the AI shows a corner marker. Hovering or focusing it shows the source email date, **Open email** and **Undo**.
- **Edits** save on Enter or blur and cancel on Escape. A 409 shows "Changed elsewhere. Reload latest" and reloads the row.
- **Gmail, AI settings, Delete account and the Ollama panel** live behind a **Settings** button.
- **Apply** is visible only when the mode is `plan` and nothing is waiting. It needs a confirm step that shows the planned counts.
- **Libraries:** no new runtime dependencies.
- **Layout and access:** it works at 375 px width, where the grid scrolls horizontally inside its own container. It must also be keyboard accessible: Tab moves between cells and Enter edits.

## Review Focus

- **Editing a cell while the sync or worker updates the same row:** the stale version gets a 409, which shows the reload message and never silently overwrites. Covered in Task 2.
- **An empty cell (null) edited to blank:** nothing is sent, so nulls stay null. Clearing a set value sends `null`, which is a real change per `ApplicationPatch`. Covered in Task 2.
- **Undo from a marker after the cell was edited by hand:** 409 `changed_since` shows "This cell changed after the AI set it." Covered in Task 3.
- **Hundreds of AI changes:** the tab pages with "Show more", 50 at a time, and the marker map loads all pages in the background without blocking the grid. Covered in Task 3.
- **Sorting by Cycle or Status with nulls:** nulls sort last in both directions. Covered in Task 1.

---

### Task 1: Sheet shell, tabs, grid, sort and filters

**Files:**
- Create: `frontend/src/sheet/Sheet.tsx`, `frontend/src/sheet/SheetGrid.tsx`, `frontend/src/sheet/SettingsPanel.tsx`, `frontend/src/sheet/sorting.ts`
- Modify: `frontend/src/App.tsx` (render `Sheet` for the signed-in state; remove the `list`/`create`/`reviews` views; keep the access-denied and deleting states), `frontend/src/styles.css`
- Test: `frontend/src/sheet/Sheet.test.tsx`, `frontend/src/sheet/sorting.test.ts`; update `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: `listAllApplications`, `listAllReviews` and the `filtering.ts` filters (reused unchanged).
- Produces:
  - `SortKey = "company"|"role"|"role_type"|"cycle"|"status"|"application_date"|"updated_at"` and `sortApplications(apps, key, dir: "asc"|"desc") -> Application[]`. Nulls go last. The cycle order is year, then Spring, Summer, Fall, Winter.
  - `Sheet({applications, reviews, onReload, mode})`.

- [ ] **Step 1: Write failing tests:**
  - `test_tabs_render_with_counts`.
  - `test_header_click_toggles_sort`.
  - `test_nulls_sort_last_both_directions`.
  - `test_search_and_filters_reduce_rows`.
  - `test_settings_button_opens_panel_with_gmail_ai_delete_and_ollama`.
  - `test_new_row_button_opens_existing_create_form`.
- [ ] **Step 2: Run** `cd frontend && npx vitest run src/sheet src/App.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** the shell, grid, settings panel and sorting.
- [ ] **Step 4: Run** `npm run lint && npm test && npm run typecheck && npm run build`. Expected: pass.
- [ ] **Step 5: Commit** `feat: spreadsheet shell with tabs and sortable grid`.

### Task 2: Inline cell editing

**Files:**
- Create: `frontend/src/sheet/EditableCell.tsx`
- Modify: `frontend/src/sheet/SheetGrid.tsx`
- Test: `frontend/src/sheet/EditableCell.test.tsx`

**Interfaces:**
- Consumes: `updateApplication` (`api/client.ts:113`), which sends `expected_version`, and `buildPatch` (`applications/ApplicationForm.tsx:90`), imported as is.
- Produces: `EditableCell({application, field, onSaved, onConflict})`.
  - The editors are: text for Company and Role; a select for Type and Status, using the existing option lists; a season select plus a year number input for Cycle; a date input for Applied.
  - Last update is read-only.

- [ ] **Step 1: Write failing tests:**
  - `test_enter_saves_with_expected_version`.
  - `test_escape_cancels`.
  - `test_unchanged_value_sends_nothing`.
  - `test_clearing_sends_null`.
  - `test_409_shows_reload_message_and_reloads`.
  - `test_keyboard_tab_moves_between_cells`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the cell editors.
- [ ] **Step 4: Run** the frontend checks. Expected: pass.
- [ ] **Step 5: Commit** `feat: edit application cells in place`.

### Task 3: AI markers and the AI changes tab

**Files:**
- Create: `frontend/src/sheet/AiChangesTab.tsx`, `frontend/src/sheet/aiMarkers.ts`
- Modify: `frontend/src/api/client.ts` (`listAiChanges(cursor?)`, `undoAiChange(id)`, `reconsiderAiChange(id)`), `frontend/src/api/types.ts` (`AiChange`), `SheetGrid.tsx`
- Test: `frontend/src/sheet/AiChangesTab.test.tsx`, `frontend/src/sheet/aiMarkers.test.ts`

**Interfaces:**
- Consumes: Plan 2, Task 9 routes.
- Produces:
  - `latestAiFieldChanges(changes: AiChange[]) -> Map<string /*application_id*/, Map<string /*field*/, AiChange>>`. It skips undone changes and keeps the newest per field.
  - `AiChangesTab({onChanged})`. Each line reads "Added Northwind · Data Science Intern" or "Acme → Interviewing" or "Ignored: job alert". Lines carry **Undo**, **Open email**, and, for ignored ones, **This was an application**.
  - Error copy:
    - `changed_since`: "This cell changed after the AI set it."
    - `already_undone`: "Already undone."

- [ ] **Step 1: Write failing tests:**
  - `test_marker_shown_only_for_latest_ai_field`.
  - `test_undone_change_has_no_marker`.
  - `test_hover_shows_open_email_and_undo`.
  - `test_undo_calls_api_and_reloads`.
  - `test_undo_changed_since_message`.
  - `test_tab_pages_50_with_show_more`.
  - `test_reconsider_on_ignored_line`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the markers and tab.
- [ ] **Step 4: Run** the frontend checks. Expected: pass.
- [ ] **Step 5: Commit** `feat: show AI changes with undo`.

### Task 4: Row menu and Needs a look tab

**Files:**
- Create: `frontend/src/sheet/RowMenu.tsx`
- Modify: `frontend/src/api/client.ts` (`deleteApplication(id, expectedVersion)` calling `DELETE /applications/{id}?expected_version=`), `frontend/src/sheet/Sheet.tsx` (the Needs a look tab renders the existing `ReviewQueue`; the Ollama panel moves to Settings)
- Test: `frontend/src/sheet/RowMenu.test.tsx`

**Interfaces:**
- `RowMenu({application, onChanged})` has **History** (the existing `ApplicationDetail` in a side drawer), **Merge duplicate** (the existing `MergeForm`) and **Delete row**. Delete asks "Delete <company> · <role>?" and reports a 409 like an edit does.

- [ ] **Step 1: Write failing tests:**
  - `test_delete_confirms_and_sends_expected_version`.
  - `test_history_opens_drawer`.
  - `test_merge_opens_existing_form`.
  - `test_needs_a_look_tab_shows_review_queue`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the row menu and tab wiring.
- [ ] **Step 4: Run** the frontend checks. Expected: pass.
- [ ] **Step 5: Commit** `feat: row menu and Needs a look tab`.

### Task 5: Plan report and Apply

**Files:**
- Create: `frontend/src/sheet/PlanBanner.tsx`
- Modify: `frontend/src/api/client.ts` (`getPlanReport()`, `applyPlan()`, `setAutoApplyMode(mode)`), `frontend/src/api/types.ts`
- Test: `frontend/src/sheet/PlanBanner.test.tsx`

**Interfaces:**
- Consumes: Plan 2, Tasks 1 and 8 routes.
- Produces: `PlanBanner({mode})`.
  - In mode `plan`, it shows "AI analyzed {analyzed} of {analyzed + waiting} emails" and a **Review plan** button. The button opens the report:
    - counts by action and reason;
    - a sample table with Company, Role, Status, Cycle, Action and Open email.
  - **Apply** is shown only when `waiting == 0`. It confirms with "Create {create} and update {update} applications?", calls `applyPlan()`, and then shows "Applying…" until a reload shows mode `on`.
  - In mode `off`, Settings offers "Start automatic tracking (plan first)", which sets the mode to `plan`.

- [ ] **Step 1: Write failing tests:**
  - `test_banner_hidden_when_mode_off_or_on`.
  - `test_apply_hidden_while_waiting`.
  - `test_apply_confirms_with_counts`.
  - `test_report_lists_sample`.
  - `test_start_tracking_sets_mode_plan`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the banner and report.
- [ ] **Step 4: Run** `npm run lint && npm test && npm run typecheck && npm run build && npm audit`. Expected: pass.
- [ ] **Step 5: Commit** `feat: review and apply the AI plan from the sheet`.
- [ ] **Step 6 (operator):** deploy dev and check the sheet in Chrome at desktop width and at 375 px. The owner reads the report and decides whether to press **Apply**.
