# TASK-003 — DEFAULT/CUSTOM state machine

- Default status: `QUEUED`
- Required verification: `V1`

## Goal
Формализовать поведение окружения для области и правила переходов между `DEFAULT` и `CUSTOM`.

## Dependencies
- `TASK-002`

## Steps
1. Описать семантику `DEFAULT`: путь уже выбран, access mode уже выбран, но у области ещё нет area-specific plugin state.
2. Зафиксировать правило: область остаётся `DEFAULT`, пока к ней не подключён хотя бы один плагин и не сохранены его настройки именно для этой области.
3. Описать семантику `CUSTOM`: для области уже существуют plugin-specific saved settings или другие area-specific overrides.
4. Построить таблицу переходов: create DEFAULT, attach plugin globally, attach plugin to current area, switch DEFAULT->CUSTOM, switch CUSTOM->DEFAULT.
5. Добавить failure cases: missing custom payload, invalid override, broken plugin config.
6. Зафиксировать, что mode выбирается и/или изменяется через понятные attach/save действия, а не неявно.

## Expected outputs
- state table;
- transition rules;
- fallback strategy.

## Done when
- нет неявного наследования окружения между областями;
- global plugin attach не переводит область в `CUSTOM` автоматически;
- area-specific plugin state однозначно переводит область в `CUSTOM`;
- launcher и plugin layer могут использовать одну и ту же state model.

## Handoff must include
- таблицу состояний;
- fallback policy;
- ограничения DEFAULT/CUSTOM;
- различие global attach vs area attach;
- next task: `TASK-004`.
