# TASK-005 — Create/save profile flow

- Default status: `QUEUED`
- Required verification: `V2`

## Goal
Перепроектировать сценарий создания и сохранения новой рабочей области так, чтобы путь сохранялся через `PATH[N]`, а вокруг него создавался полноценный workflow profile.

## Dependencies
- `TASK-003`
- `TASK-004`

## Steps
1. Разобрать текущий сценарий выбора новой папки через launcher/setup.
2. Зафиксировать, что при выборе пути пользователь одновременно выбирает рабочую область, режим доступа и исходную конфигурацию окружения.
3. Для новой области по умолчанию сохранять `environmentMode = DEFAULT`.
4. Описать, какие поля записываются сразу в `PATH[N]`/profile storage, включая `accessMode`.
5. Описать, какие действия позже переводят область в `CUSTOM` — например, area-specific plugin attach и сохранение plugin settings.
6. Определить rollback и validation behavior при ошибках пути, access mode или начальной конфигурации.

## Expected outputs
- create/save flow spec;
- launcher prompt design;
- запись persisted fields.

## Done when
- новая область сохраняется как profile-aware запись на основе пути;
- выбор пути явно фиксирует workspace, access mode и environment baseline;
- новая область стартует в `DEFAULT`, пока не появился area-specific plugin state.

## Handoff must include
- точный flow создания профиля;
- формат persisted data;
- когда область остаётся DEFAULT и когда переходит в CUSTOM;
- ошибки и rollback;
- next task: `TASK-006`.
