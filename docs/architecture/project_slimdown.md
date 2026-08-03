# Project Slimdown

## Goal

Keep the Live2D assistant feature-complete while reducing hidden coupling, duplicated routing logic, and God Object growth.

## Non-goals

- Do not replace Qt.
- Do not replace the plugin system.
- Do not replace SQLite memory storage.
- Do not rewrite `ChatService`, `SettingsDialog`, or `ScreenSensor` in one pass.
- Do not add new registry/contract layers unless they delete existing duplicated decisions.

## Slimming Rules

1. A new module must delete or centralize existing scattered logic.
2. A route, permission, model choice, output target, or runtime setting must have one source of truth.
3. Runtime sensors and sidecars must expose read-only status before adding more behavior.
4. Refactors must preserve current user-facing behavior unless a test states the intended change.
5. Any temporary wrapper or partially extracted service must have a removal trigger.
6. When touching a module that reads mutable values from `config.py`, prefer passing a narrow runtime snapshot instead of adding new global reads.
7. `py_compile` is not enough for shared behavior changes; add or reuse a focused pytest before moving the behavior.

## Baseline Metrics

Run these before and after each phase (do not trust stale numbers in git history):

```powershell
python -c "from pathlib import Path; targets=['services/chat_service.py','modules/gui/dialogs/settings.py','modules/screen_sensor.py','core/application.py','modules/plugin_manager.py','integrations/gui_http.py','config.py']; [print('{}: {} lines'.format(p, len(Path(p).read_text(encoding='utf-8', errors='ignore').splitlines()))) for p in targets if Path(p).exists()]"
```

Current baseline (measured 2026-07-31):

```text
services/chat_service.py: 5184 lines
modules/gui/dialogs/settings.py: 5761 lines
modules/screen_sensor.py: 1972 lines
core/application.py: 3399 lines
modules/plugin_manager.py: 1554 lines
integrations/gui_http.py: 3394 lines
config.py: 839 lines
```

相对历史基线（chat 4620 / settings 5483 / application 2959 / gui_http 1530），**gui_http 与 application 明显恶化，settings / chat 仍 Critical**。

## Bloat Thresholds

- **Critical:** over 3000 lines, or owns unrelated runtime flows. Requires an active split track.
- **High:** 1500-3000 lines. Requires a boundary note before adding new features.
- **Watch:** 800-1500 lines. New behavior should usually land in a focused helper module.
- **Healthy:** under 800 lines with one clear responsibility.

Current critical/high files:

| File | Size | Status | First split target |
| --- | ---: | --- | --- |
| `modules/gui/dialogs/settings.py` | 5761 | Critical | extract one settings page at a time into `settings_pages/` |
| `services/chat_service.py` | 5184 | Critical | integrate `ChatFlowResult`, then move final orchestration/output paths |
| `core/application.py` | 3399 | Critical | avoid new orchestration; pass narrow dependencies into services |
| `integrations/gui_http.py` | 3394 | Critical | HTTP surface only; business via `services/gui_api/*` |
| `modules/screen_sensor.py` | 1972 | High | extract sedentary session state and GUI work-session snapshot |
| `modules/plugin_manager.py` | 1554 | High | draw dependency graph before splitting policy/executor/prompt modules |

## Target Boundaries

- `ChatService`: conversation orchestration only; no plugin media-send implementation and no ad hoc flow result parsing after `ChatFlowResult` migration.
- `services/chat_support/*`: small services for routing, flow results, output, active alerts, gateway sending, and sensor replies.
- `modules/gui/dialogs/settings.py`: shell dialog and tab composition only; page-specific UI and save/load logic move to `modules/gui/settings_pages/*`.
- `modules/screen_sensor.py`: sensor loop coordination only; sedentary state and GUI snapshot logic move to focused modules first.
- `modules/plugin_manager.py`: plugin loading and dispatch coordination only; access policy, executor, prompt metadata, and config normalization must be one-way dependencies.
- `integrations/gui_http.py`: HTTP surface only; no business rules.
- `config.py`: boot defaults only; runtime changes flow through `modules.runtime_settings` and injected snapshots.

## Mandatory Gates

- **Settings gate:** any new settings page or major settings option must not add more page-specific save/load logic to `modules/gui/dialogs/settings.py` unless a matching extraction is already scheduled.
- **ScreenSensor gate:** 久坐提醒 and 屏幕吐槽 remain separate paths. Sedentary state extraction must not change screen commentary trigger behavior.
- **Flow gate:** do not broaden `OutputCoordinator` until at least one flow returns `ChatFlowResult` and `tests/test_chat_service_smoke.py` covers that path.
- **Gateway gate:** do not delete `_send_gateway_*` wrappers until `rg` shows plugins no longer call them directly.
- **PluginManager gate:** do not split child modules until dependencies are proven one-way.
- **Runtime config gate:** touched code should not add new direct mutable reads from `config.py`; use a value object or runtime snapshot.

## Required Guardrail Tests

- `tests/test_chat_service_smoke.py`: non-stream reply, stream reply, search, tool/direct plugin output, QQ reply.
- `tests/test_plugin_access_control.py`: local/remote/owner/non-owner/group mention access matrix.
- `tests/test_runtime_status.py`: read-only sidecar/sensor/work-session snapshot.
- Existing sedentary/sensor tests remain required when touching `modules/screen_sensor.py`.

## Plan Entry

Historical executable plans and one-off reviews have been pruned.
Use this document as the current slimming reference; record active state in `PROJECT_STATUS.md`.
Open security items live in `docs/SECURITY_REMEDIATION_PLAN.md`.
