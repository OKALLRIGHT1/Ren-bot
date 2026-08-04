# Runtime Health GUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a top-bar runtime-health indicator and a read-only component detail dialog to the existing Qt GUI.

**Architecture:** Read the existing thread-safe `RuntimeHealthCenter.snapshot()` directly from the Qt process. Keep snapshot-to-view formatting in pure functions and Qt rendering in a focused dialog, while the main window owns only binding, periodic refresh, and dialog opening.

**Tech Stack:** Python 3, PySide6, pytest

---

### Task 1: Health snapshot presentation model

**Files:**
- Create: `modules/gui/runtime_health_view.py`
- Create: `tests/test_runtime_health_gui.py`

- [x] Write failing tests for overall labels/colors, component effective-state formatting, priority ordering, and invalid snapshots.
- [x] Run `python -m pytest tests/test_runtime_health_gui.py -q` and confirm the missing module failure.
- [x] Implement the minimal pure formatting functions and constants.
- [x] Re-run the target test and confirm it passes.

### Task 2: Read-only health detail dialog

**Files:**
- Create: `modules/gui/dialogs/runtime_health.py`
- Modify: `tests/test_runtime_health_gui.py`

- [x] Add an offscreen Qt test that supplies a fake health center and asserts the overall label and component table.
- [x] Run the test and confirm the missing dialog failure.
- [x] Implement the dialog with a four-column read-only table, manual refresh, and a 10-second timer.
- [x] Re-run the target test and confirm it passes.

### Task 3: Main-window indicator and application binding

**Files:**
- Modify: `modules/gui/app.py`
- Modify: `core/application.py`
- Modify: `tests/test_runtime_health_gui.py`

- [x] Add an offscreen Qt test for the top-bar indicator state and click target.
- [x] Run the test and confirm the missing constructor argument/control failure.
- [x] Pass `runtime_health` from `Live2DApplication`, add the top-bar button, refresh it every 10 seconds, and open/reuse the detail dialog.
- [x] Re-run the target tests and confirm they pass.

### Task 4: Verification and commit

**Files:**
- Review all files above.

- [x] Run `python -m pytest tests/test_runtime_health_gui.py tests/test_runtime_health.py tests/test_runtime_status.py -q`.
- [x] Run `python -m pytest -q`.
- [x] Inspect `git diff --check` and `git diff` for unrelated changes or duplicated health logic.
- [ ] Commit the implementation on local `main`.
