# TASK-008 — Plugin loader and effective mode

- Default status: `QUEUED`
- Required verification: `V2`

## Goal
Спроектировать и затем реализовать core loader/registry, который загружает плагины с учётом active workflow area, access mode, attach scope и safety mode.

## Dependencies
- `TASK-004`
- `TASK-007`

## Required code/docs outputs
- рабочий loader/registry слой в коде;
- documented lifecycle загрузки;
- effective mode rules;
- state model: loaded / disabled / failed / warnings;
- механизм пересборки registry state при смене профиля.

## Steps
1. Описать scan/load lifecycle для директории плагинов и их entrypoint-ов.
2. Зафиксировать разницу между global plugin attach и area-specific attach при загрузке конфигурации.
3. Определить, как active path/profile selection, access mode, global defaults и area overrides вычисляют effective mode.
4. Определить политику `optional` vs `required` plugin.
5. Спроектировать registry state: loaded / disabled / failed / warnings.
6. Добавить правило, что смена профиля пересобирает registry state.
7. Реализовать код и покрыть его тестами не ниже V2.

## Required verification evidence
- unit/integration tests для loader/effective mode;
- подтверждение различий между global attach и area attach;
- проверка хотя бы одного failure path;
- подтверждение, что safety model не обходится.

## Expected outputs
- loader design;
- effective mode rules;
- registry state model.

## Done when
- понятно и доказуемо, как сервер поднимает плагины без обхода safety model;
- global attach и area attach имеют разное, но явное поведение;
- profile switch пересобирает plugin state;
- архитектура готова к DB и AI plugin families;
- есть тесты, подтверждающие это поведение.

## Cannot be marked DONE unless
- реализованный loader существует в коде и проходит тесты;
- есть проверка backward compatibility;
- есть проверка failure-path behavior;
- задача не оставлена на уровне `plugin baseline checkpoint`.

## Handoff must include
- lifecycle загрузки;
- effective mode formula;
- state categories;
- различие global vs area-specific loading;
- пройденные тесты;
- next task: `TASK-009`.
