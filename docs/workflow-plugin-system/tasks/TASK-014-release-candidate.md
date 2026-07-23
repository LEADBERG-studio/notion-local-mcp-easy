# TASK-014 — Release candidate

- Default status: `QUEUED`
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

## Steps
1. Проверить, что README и другие публичные docs отражают workflow profiles и plugin-system.
2. Подготовить release notes и список breaking/behavior changes.
3. Сверить superprompt и authoring kit с реальным кодом и тестами.
4. Зафиксировать known limitations и post-release backlog.
5. Пройти финальный V4 review.
6. Сверить статус каждой задачи `TASK-001` ... `TASK-014` и убедиться, что все они `DONE`.
7. Подготовить пакет к обсуждению публикации, но не публиковать его.

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

## Cannot be marked DONE unless
- существует финальная таблица статусов всех task ids;
- выполнен master completion checklist;
- нет открытых blocker-ов уровня migration/regression/diagnostics;
- задача не завершена формулировкой вида `checkpoint complete` или `resume later`.

## Handoff must include
- release readiness status;
- remaining blockers;
- publication discussion points;
- final task matrix;
- next step: согласование с пользователем.
