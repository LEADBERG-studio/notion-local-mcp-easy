# TASK-009 — Diagnostics and operator visibility

- Default status: `QUEUED`
- Required verification: `V2`

## Goal
Сделать диагностический слой, который показывает активный профиль, environment mode, plugin state и ошибки загрузки.

## Dependencies
- `TASK-006`
- `TASK-008`

## Required code/docs outputs
- diagnostics contract;
- обновлённый status surface / operator tools;
- список health indicators;
- documented observability limitations.

## Steps
1. Зафиксировать поля diagnostics: active profile, path, env mode, plugin modes, failed plugins, warnings.
2. Определить, как показывается source of current config для каждого плагина.
3. Добавить требования к healthcheck и last startup error.
4. Согласовать diagnostics с handoff-процессом.
5. Проверить, что после switch и migration диагностика отражает реальное состояние.
6. Реализовать код и покрыть его тестами/проверками не ниже V2.

## Required verification evidence
- тесты или walkthroughs для diagnostics после switch/migration;
- подтверждение видимости plugin scope и startup errors;
- отдельный список известных observability gaps.

## Expected outputs
- diagnostics contract;
- обновлённый status surface;
- health indicators.

## Done when
- оператор видит полный runtime state;
- можно понять, почему конкретный плагин disabled/failed;
- diagnostics пригодны для pause/resume разработки;
- health/visibility limitations явно задокументированы.

## Cannot be marked DONE unless
- diagnostics реально доступны в коде/инструментах;
- есть подтверждение после switch и migration;
- observability gaps перечислены явно, а не скрыты в общем тексте;
- задача не оставлена как неполный checkpoint.

## Handoff must include
- список обязательных diagnostic fields;
- health policy;
- open observability gaps;
- пройденные проверки;
- next task: `TASK-010`.
