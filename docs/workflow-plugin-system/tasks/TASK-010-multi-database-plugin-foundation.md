# TASK-010 — Multi-database plugin foundation

- Default status: `QUEUED`
- Required verification: `V3`

## Goal
Построить общий foundation layer для нескольких DB plugins и определить первую очередь коннекторов с учётом global и area-specific attach.

## Dependencies
- `TASK-007`
- `TASK-008`
- `PLUGIN_REGISTRATION_CONTRACT.md`

## Required code/docs outputs
- shared DB foundation layer или явно выделенный reusable contract;
- минимум два подтверждённых DB-family сценария поверх общего foundation;
- documented connector order;
- tests для family-level поведения.

## Steps
1. Выделить общие элементы DB plugin family: connection config, schema introspection, query/execute split, diagnostics.
2. Зафиксировать минимальный набор tools для каждой СУБД.
3. Спроектировать reusable shared layer для DB plugins.
4. Определить первую очередь: SQLite -> PostgreSQL -> MySQL/MariaDB.
5. Описать, как profile-specific config выбирает connection aliases и mode gating.
6. Описать, как подключение БД сохраняется глобально или только для текущей области.
7. Реализовать общий foundation и минимум два убедительных family-level доказательства совместимости.

## Required verification evidence
- tests на family-level abstractions;
- подтверждение, что новая СУБД подключается без переписывания registry contract;
- проверка global vs area attach;
- проверка migration/compatibility, если storage меняется.

## Expected outputs
- DB foundation spec;
- connector order;
- shared responsibilities list.

## Done when
- добавление новой СУБД не требует нового registry contract;
- для каждой целевой СУБД понятен minimum viable toolset;
- DB plugin может быть подключён глобально или только к текущей области;
- foundation годится как база для первых реальных плагинов;
- это доказано не только одним SQLite baseline.

## Cannot be marked DONE unless
- есть минимум два family-level подтверждения, а не один baseline plugin;
- shared layer/contract существует в коде или в чётко выделенном reusable модуле;
- есть tests или walkthroughs для global vs area DB attach;
- задача не сводится к “SQLite уже есть, значит достаточно”.

## Handoff must include
- порядок реализации БД;
- общий набор abstractions;
- global vs area DB attach rules;
- unresolved DB-specific risks;
- пройденные family-level проверки;
- next task: `TASK-011`.
