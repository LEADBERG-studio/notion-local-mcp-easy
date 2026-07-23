# TASK-006 — Switch profile and menu

- Default status: `QUEUED`
- Required verification: `V2`

## Goal
Перепроектировать меню и переключение рабочей области так, чтобы активировался весь workflow profile, построенный на выбранном `PATH[N]`.

## Dependencies
- `TASK-005`

## Steps
1. Описать алгоритм switch: выбрать path/profile -> применить workspace path -> применить access mode -> применить environment mode -> обновить diagnostics.
2. Обновить presentation меню: slot/path, area name, access mode, DEFAULT/CUSTOM, active marker, warning marker.
3. Зафиксировать, что выбор пути означает выбор рабочей области, режима доступа и конфигурации окружения.
4. Определить поведение при битом CUSTOM config, недоступном пути или сломанном area-specific plugin state.
5. Зафиксировать, как хранится last active profile и как учитывается global plugin attach без перевода области в CUSTOM.
6. Убедиться, что отключение меню не теряет profile state.

## Expected outputs
- switch flow spec;
- menu presentation spec;
- error handling notes.

## Done when
- пользователь переключает profile, а не только slot;
- не остаётся хвостов окружения от предыдущей области;
- global plugin attach не маскируется под area-specific CUSTOM state;
- меню показывает состояние профиля, а не только путь.

## Handoff must include
- алгоритм switch;
- формат меню;
- failure cases;
- как учитываются global plugins при переключении;
- next task: `TASK-007`.
