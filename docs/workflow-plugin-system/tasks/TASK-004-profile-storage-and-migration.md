# TASK-004 — Profile storage and migration

- Default status: `QUEUED`
- Required verification: `V1`

## Goal
Выбрать storage strategy для workflow profiles и описать миграцию со старого формата `PATH[N]` без отказа от него.

## Dependencies
- `TASK-001`
- `TASK-002`
- `TASK-003`

## Steps
1. Сравнить варианты хранения: расширение `connections.cfg`, отдельный JSON storage, смешанная схема.
2. Выбрать canonical source of truth так, чтобы `PATH[N]` оставался базовой точкой входа.
3. Описать структуру файла/файлов профилей.
4. Спроектировать миграцию: старые `PATH[N]` получают profile metadata, `accessMode` и `environmentMode = DEFAULT`.
5. Добавить storage для plugin attach scope: global и area-specific.
6. Добавить schema version, rollback и repair notes.

## Expected outputs
- storage decision;
- пример структуры хранения;
- migration plan.

## Done when
- выбран один canonical storage;
- старые конфиги можно поднять без потери данных;
- `PATH[N]` продолжает использоваться как опорная структура;
- global и area-specific plugin state имеют понятные места хранения.

## Handoff must include
- формат хранения;
- правила миграции;
- где лежат global plugin settings и area-specific plugin settings;
- места чтения/записи старого и нового форматов;
- next task: `TASK-005`.
