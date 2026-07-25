# TASK-014 — Release candidate

- Default status: `QUEUED`
- Current status: `DONE`
- Required verification: `V4`

## Goal
Довести инициативу до release candidate, обновить публичную документацию и подготовить финальные release gates.

## Dependencies
- `TASK-009`
- `TASK-013`
- `RELEASE_PATH.md`
- `MASTER_COMPLETION_CHECKLIST.md`

## Required code/docs outputs
- release candidate docs set;
- release checklist;
- known limitations list;
- final task-status matrix;
- финальная сводка по backward compatibility и migration safety.

## Completion checkpoint — 2026-07-25
- README / CHANGELOG synchronized with the explicit tunnel-mode UX and the 1.4.5 target version.
- Background command jobs hardened so heavier command output no longer blocks status polling through transport.
- Final verification captured:
  - `py_compile` OK
  - `tests.test_launcher` OK
  - `tests.test_process_limits` + `tests.test_server_smoke` OK
  - `tests.test_ai_plugin_foundation` + `tests.test_db_plugin_foundation` + `tests.test_workflow_profiles` OK
  - `tests.test_core` OK
  - `tests.test_server_profiles` OK
  - `tests.test_repo_context` OK
  - `python -m unittest discover -s tests -q` OK (`Ran 98 tests`)
- Local release package rebuilt for discussion: `release/notion-mcp-easy-1.4.5.zip`

## Required verification evidence
- выполненный `MASTER_COMPLETION_CHECKLIST.md`;
- таблица статусов всех задач;
- результаты regression/migration checks;
- список оставшихся только пользовательских approval-step-ов (если они есть).

## Expected outputs
- release candidate docs set;
- release checklist;
- known limitations list.

## Done when
- корневая документация обновлена;
- release candidate можно показывать пользователю;
- не осталось скрытых архитектурных долгов, мешающих обсуждать публикацию;
- все задачи `TASK-001` ... `TASK-014` имеют статус `DONE`.

## Handoff must include
- release readiness status;
- remaining blockers;
- publication discussion points;
- final task matrix;
- next step: согласование с пользователем.
