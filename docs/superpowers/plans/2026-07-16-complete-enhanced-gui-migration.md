# Complete Enhanced GUI Migration Plan

> **For agentic workers:** Execute task-by-task with TDD. Dual-repo commits. Do not declare complete until the migration matrix has no `missing` / `placeholder` / `raw-json-only` / `untested` rows.

**Goal:** Make Tauri Enhanced fully cover daily Python Qt GUI operations while keeping `legacy_gui` dual-path and never deleting old GUI / live2d-only.

**Architecture:** Python owns business state and structured `/gui` APIs. Rust is the sole authenticated transport, process ownership, keyring, and desktop activity source. React renders domain pages only through Rust allowlisted HTTP/WSS.

**Tech Stack:** Python 3 / aiohttp / pytest · Tauri 2 / Rust · React 19 / TypeScript / Vitest / lucide-react

---

## Repositories & Invariants

| Repo | Path | Branch / worktree |
|---|---|---|
| Python backend | `D:/Desktop/live2d-suzu/live2d-llm` | current dirty branch; stage only task files |
| Enhanced frontend | `D:/Desktop/live2d-suzu/live2d-enhanced-connection-profiles` | `codex/connection-profiles` |

Hard invariants:

1. Python and Tauri remain independently startable/installable/upgradable/remotable.
2. Rust/Tauri is the only desktop activity + sedentary capture source.
3. SQLite / characters / models / plugins keep existing single sources of truth; no frontend copies.
4. Tauri never reads Python DB or business files directly; only authenticated `/gui` APIs.
5. Secrets stay in Python secret store or Rust keyring; React sees `has_token` / masks only.
6. No raw JSON editor as a primary page; JSON only as collapsed “高级/原始配置”.
7. Do not rollback user dirty work; do not change old Qt behavior; extract shared services when reuse is needed.
8. No silent fallbacks, second routers, second save paths, or fake success.
9. TDD: failing test → implement → pass → targeted commit.
10. `legacy_gui` default; headless only when user explicitly chooses.

---

## Current Baseline (2026-07-16 audit)

### Old Qt entry points

| Entry | Location | Notes |
|---|---|---|
| Main chat tray | `modules/gui/app.py` `QtChatTrayApp` | timeline, TTS/voice/DND, costume, console, settings, codex |
| Settings center | `modules/gui/dialogs/settings.py` | 17 nav pages via `_tab_meta` |
| Character editor | `modules/gui/dialogs/character_editor.py` | persona/TTS/costume/motion/expression |
| Plugin manager | `modules/gui/dialogs/plugin_manager.py` | list/toggle/schema/editor |
| Memory editor | `modules/gui/dialogs/memory_editor.py` | persons/categories/records/transcript/vector |
| Diary | `modules/gui/dialogs/diary_manager.py` | list/edit/delete/export |
| Knowledge | `modules/gui/dialogs/knowledge_manager.py` + import wizard | docs/import/reindex/search |
| Expression library | `modules/gui/dialogs/expression_library_manager.py` | patterns/stats |
| Meme pack | `modules/gui/dialogs/meme_manager.py` | assets/triggers |
| Status screen | `modules/gui/dialogs/status_screen_manager.py` | metrics/images/test |
| App rules | `modules/gui/dialogs/screen_app_rules.py` | classification rules |
| Codex assistant | `modules/gui/dialogs/codex_assistant.py` | external agent UI |
| Console log | `modules/gui/dialogs/console_log.py` | live console |
| Model routing overview | `modules/gui/dialogs/model_routing_overview.py` | chain badges |
| Info sources page | `modules/gui/settings_pages/info_sources_page.py` | ALAPI endpoints |
| Sedentary page | `modules/gui/settings_pages/sedentary_page.py` | thresholds/popup |
| Thin wrapper | `modules/qt_gui.py` | re-exports app |

### Existing `/gui` HTTP API (Python)

Health/runtime, dashboard, settings blob CRUD (`custom_models`/`runtime`/`characters`/`mcp`), character costume meta + preview, plugins list/toggle/reload/config/schema, dependencies scan/install, memory items/episodes/transcript CRUD, QQ profiles CRUD, diagnostics events/outbound/reply-effects/deferred-tools/activity-events, activity-config/ingest, media tickets.

### Enhanced control center baseline

Pages: overview / chat / plugins / memory / settings(JSON+connection) / diagnostics(JSON dump).  
Nav is flat 6 items. Settings for models/characters/MCP are **raw JSON**. Many Qt domains have **no API and no page**.

### Allowlist gap

Rust `backend_api_route_allowed` mirrors the current small API set. Every new domain path must be added to both Python routes and Rust allowlists (`backend_transport.rs` primary; keep `backend.rs` legacy in sync if still compiled).

---

## Migration Matrix

Status values: `available` · `partial` · `raw-json-only` · `placeholder` · `missing` · `untested`

> Completion rule: every row must reach **`available` + tested** (matrix column `status=available`, `tests` non-empty and green).

| # | Domain | Old entry | Business service / module | Existing API | Enhanced page | Missing API | Tests | Status |
|---|---|---|---|---|---|---|---|---|
| 1 | Main chat | `app.py` tray chat | `services/chat_service.py`, GUI WS | WS hello/command/log/status/config/character | `ChatPage` | structured chat history query optional; command ack clarity | WS + chat page tests | partial |
| 2 | Connection profiles | n/a (new) | Rust profiles/supervisor | health/runtime | `BackendConnectionPanel` | none for baseline | rust+frontend contract | available |
| 3 | Overview dashboard | tray status chips | app runtime | `GET /dashboard` | `OverviewPage` | richer DTO (plugins/memory/deps summary typed) | dashboard contract | partial |
| 4 | Models & routing | settings llm/provider + routing overview | `modules/model_catalog.py`, `services/gui_api/models_service.py` | `GET /models`, upsert/delete model/provider, save router (+ legacy raw settings) | `ModelsPage` structured | connection test still pending | model service + page unit tests | partial |
| 5 | Characters | character editor | `modules/character_manager.py`, `services/gui_api/characters_service.py` | structured list/get/upsert/delete/activate + costume upsert/delete/wear + costume-meta/preview (+ legacy raw) | `CharactersPage` structured | TTS test play / motion preview wiring polish | character service + page unit tests | partial |
| 6 | Theme / colors | settings color page | runtime_settings palette | raw runtime | none dedicated | `GET/POST /theme` client-safe palette | theme tests | missing |
| 7 | Plugins | plugin manager dialog | `modules/plugin_manager.py` | list/toggle/reload/config/schema | `PluginsPage` | permissions/aliases/model select/deps/secret mask DTOs | plugin tests exist partial | partial |
| 8 | Info sources | info_sources_page | `services/info_sources/*` | none dedicated | none | list/save/test endpoints draft-generate | info source API tests | missing |
| 9 | Knowledge base | knowledge manager + import wizard | `modules/memory/knowledge_store.py` | none | none | dirs/docs/import/delete/reindex/search/import-chat | knowledge tests | missing |
| 10 | Expression library | expression_library_manager | plugin/data expression store | none | none | list/filter/edit/delete/stats | expression tests | missing |
| 11 | Meme pack | meme_manager | `plugins/meme_pack` | none dedicated GUI API | none | list/preview/edit triggers/stats | meme tests | missing |
| 12 | Diary | diary_manager | diary files + chat_support diary | none GUI list API | none | list/get/save/delete/export | diary tests | missing |
| 13 | Memory center | memory_editor | `modules/memory_sqlite.py` / advanced_memory / `services/gui_api/memory_service.py` | core list/upsert/delete/category + vector status/rebuild/embedding test + legacy items/episodes/transcript | `MemoryPage` core/vector + legacy tabs | profile overview polish, vector row inspector | memory service + page unit tests | partial |
| 14 | Status screen | status_screen_manager | status_screen modules | none | none | get/save/test-send image assets | status screen tests | missing |
| 15 | App recognition | screen_app_rules | `modules/screen_app_registry.py` | none | none | rules CRUD/test classify | app rules tests | missing |
| 16 | Sedentary | sedentary_page | runtime_settings + activity config | `GET /activity-config` client-safe | none dedicated UI | save sedentary UI fields, popup text/image, test reminder, notify enhanced | sedentary API+page | partial |
| 17 | Dependencies | settings dependency page | `modules/dependency_check.py` | scan/install | only via overview? not full page | typed dependency page | dep tests | partial |
| 18 | MCP | settings mcp page | mcp_tools plugin config | raw `/settings/mcp` | Settings JSON | structured servers, bridge, access, test connection | mcp tests | raw-json-only |
| 19 | QQ / NapCat | settings gateway page | gateway runtime + qq profiles | qq profiles only | none full | connection, permissions, lists, image/voice, profiles UI | qq tests | partial |
| 20 | Diagnostics events | diagnostics | event logger | `/events` etc | Diagnostics pre dump | typed tables, filters | diagnostics tests | partial |
| 21 | Console log | ConsoleLogDialog | logging bridge | none stream API | none | log tail stream or poll API | console tests | missing |
| 22 | App logs | (settings/runtime logs) | file logs | none | none | safe log list/tail | app log tests | missing |
| 23 | Codex / code assistant | CodexAssistantDialog | `modules/codex_*` | none GUI API | none | config + invoke status | codex tests | missing |
| 24 | Live2D model mgmt | desktop tray + resources | enhanced resources.rs + characters | local resource APIs only | desktop only | model list/import/scale persist via enhanced + optional backend costume path | resource+scale tests | partial |
| 25 | Global/model scale wheel | enhanced desktop | resources + config | local Tauri store | desktop | ensure persist sync control-center display | scale tests | partial |

---

## Target Navigation (Enhanced)

Grouped sidebar (not flat 6):

1. **常用** — 总览、聊天、连接档案
2. **AI 与角色** — 模型与路由、角色中心、主题
3. **记忆与知识** — 记忆中心、日记、知识库、表达学习库、表情包库
4. **接入与插件** — 插件中心、信息源、QQ/NapCat、MCP、Codex
5. **桌面行为** — 久坐提醒、应用识别、状态屏、Live2D/缩放
6. **系统诊断** — 依赖体检、诊断事件、控制台、应用日志、出站/延迟工具

Layout rules: independent page components under `src/control-center/pages/`; shared chrome in `components/`; no expanding single `pages.tsx`. Quiet dense desktop tool style; support 1180×760 and min 900×620.

---

## Implementation Phases

### Phase 0 — Plan & matrix (this document)
- [x] Audit Qt, services, APIs, enhanced pages
- [x] Write matrix and phases

### Phase 1 — Control-center shell + shared UI kit
**Repos:** enhanced  
**Deliverables:**
- Split `pages.tsx` into `pages/*` + `components/*`
- Grouped sidebar + route ids in `control-state.ts`
- Shared: `PageHeader`, `StateView`, `Tabs`, `DataTable`, `FormField`, `Toggle`, `Slider`, `Select`, `SchemaForm`, `SecretField`, `RemoteDisabledBanner`, `JsonAdvancedPanel`
- Loading/empty/offline/error/saving patterns
- Tests for nav groups and page normalize

### Phase 2 — Domain API foundation (Python)
**Repos:** Python  
**Deliverables:**
- `services/gui_api/` package: DTO helpers, redaction, validation errors
- Thin handlers in `integrations/gui_http.py` calling shared services (Qt may later call same services)
- Standard envelope: `{ ok, data?, error?, code? }` with no secrets

Order of domain APIs (each with tests before impl):
1. Models/providers/routing/embedding
2. Characters structured
3. Memory persons/categories/vector/search/rebuild
4. Diary
5. Knowledge
6. Plugins enrichment
7. Info sources
8. Expression + meme
9. QQ gateway settings
10. MCP structured
11. Sedentary save + test
12. App rules
13. Status screen
14. Dependencies page DTO
15. Console/app logs
16. Codex config/invoke
17. Theme

### Phase 3 — Rust allowlist & transport
**Repos:** enhanced  
For each API batch: extend allowlist, remote capability flags (`local_only` actions disabled in remote), tests.

### Phase 4 — React domain pages (by nav group)
Implement pages against real APIs; tests for load/edit/save/error/offline.  
Replace raw JSON settings tabs with structured UIs; keep JSON only under advanced collapse.

### Phase 5 — Dual-path parity & headless/remote acceptance
- legacy_gui: Qt + enhanced both work; mutual edit visibility
- headless: full matrix operable without Qt
- remote: no local path ops, no secrets, no process control
- real backend smoke scripts

### Phase 6 — Packaging
- version bump if needed
- `npm run check`, clippy `-D warnings`, release NSIS + SHA-256
- Python targeted full suites

---

## Task Backlog (execution order)

### Task M1: Shell split + grouped nav
- Files (enhanced): `control-state.ts(t)`, `main.tsx`, `components/*`, `pages/*`, `styles.css`
- Fail test: unknown grouped page ids / old pages still importable
- Commit: `feat: 重构控制中心分组导航与页面拆分`

### Task M2: Models & routing structured API + page
- Python: `services/gui_api/models.py`, routes `/models/*`, tests
- Enhanced: allowlist + `pages/ModelsPage.tsx` + tests
- Commit Python then Enhanced

### Task M3: Characters structured API + page
### Task M4: Memory center completion
### Task M5: Diary page
### Task M6: Knowledge page + import wizard
### Task M7: Plugins enrichment (schema form quality, secrets mask, aliases)
### Task M8: Info sources
### Task M9: Expression library + meme pack
### Task M10: QQ/NapCat + MCP structured
### Task M11: Sedentary + app rules + status screen
### Task M12: Theme + Live2D/scale management surfaces
### Task M13: Diagnostics tables + console/app logs + dependencies page
### Task M14: Codex assistant
### Task M15: Dual-path e2e matrix + package

Each task template:
1. Write failing Python API tests and/or React/Rust tests
2. Implement minimal service + route + allowlist + UI
3. Run targeted tests → green
4. Stage only task files → commit per repo
5. Update this matrix status column

---

## API Design Rules

- Prefer narrow resources: `/models`, `/models/test`, `/characters/{id}`, `/diary`, `/knowledge/search` …
- Validate with explicit fields; reject unknown dangerous keys
- Mask secrets on read (`api_key` → `has_api_key` / `****`)
- Writes accept `SecretUpdate`: keep | replace | clear where applicable
- Mutations call existing save paths (`save_runtime_settings`, character manager, etc.) — no second persistence
- After save, hot-refresh via existing notify/broadcast; if restart required, return `requires_restart: true`

## UI Rules

- No card-in-card stacks
- Tables for collections; side detail panel for edit
- Remote mode: banner + disabled local-only actions with reason
- Explicit states only — never pretend success

## Acceptance Checklist

- [ ] Matrix has zero missing/placeholder/raw-json-only/untested
- [ ] Old Qt GUI still launches (`legacy_gui`)
- [ ] Headless completes all daily ops via enhanced
- [ ] Remote HTTPS/WSS safe
- [ ] Python domain tests green
- [ ] `npm run check` green
- [ ] `cargo clippy -D warnings` green
- [ ] NSIS build + SHA-256 recorded
- [ ] No token plaintext in React/logs/fixtures

---

## Notes from connection-profiles work (already done)

Connection profiles, token keyring, supervisor ownership, activity-config/ingest, media tickets, contract tests, version `0.3.0-alpha.1` packaging baseline are complete. This plan builds **on top** of that foundation; do not regress those invariants.
