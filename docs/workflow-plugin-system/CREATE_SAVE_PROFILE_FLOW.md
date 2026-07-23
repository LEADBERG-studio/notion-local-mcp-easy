# Create / Save Profile Flow

## Status
Covers TASK-005.

## Goal
Сделать создание новой рабочей области profile-aware, не ломая legacy-модель `connections.cfg` + `config.json`.

## Flow
1. Launcher читает `connections.cfg` и текущий `config.json`.
2. Если текущий `workspace` ещё не закреплён в `PATH[N]`, launcher сначала безопасно добавляет его в `connections.cfg`.
3. При выборе новой папки через меню (`0`) launcher:
   - валидирует, что папка существует;
   - сохраняет путь в свободный `PATH[N]` или в явно выбранный расширенный слот;
   - синхронизирует `workflow-profiles.json` со слотами;
   - создаёт профиль для нового слота, если его ещё нет;
   - явно спрашивает access mode для новой области;
   - записывает `accessMode` в профиль;
   - помечает профиль активным;
   - зеркалит активный профиль обратно в legacy `config.json`.
4. Новый профиль стартует с `environmentMode = DEFAULT`.
5. Token, port, tunnel settings и остальные legacy runtime fields сохраняются без пересоздания.

## Persisted fields

### `connections.cfg`
- `MENU`
- `PATH[N]`

### `workflow-profiles.json`
- `schemaVersion`
- `activeProfileId`
- `profiles[profileId].pathSlot`
- `profiles[profileId].workspacePath`
- `profiles[profileId].accessMode`
- `profiles[profileId].environmentMode`
- `profiles[profileId].displayName`
- `profiles[profileId].metadata.createdFrom`
- `profiles[profileId].metadata.lastSelectedAt`
- `profiles[profileId].metadata.lastKnownGood`
- `profiles[profileId].plugins`

### `config.json` mirror
- `workspace`
- `allow_commands`
- existing token/network/runtime fields unchanged

## DEFAULT / CUSTOM rule
- Новая область всегда создаётся как `DEFAULT`.
- Само по себе существование discoverable plugin folder ничего не меняет.
- Глобальный plugin attach без area-specific state не переводит профиль в `CUSTOM`.
- `CUSTOM` появляется только после area-specific plugin state в `profiles[profileId].plugins`.

## Validation and rollback
- Если папка не существует, профиль не создаётся.
- Если запись в `connections.cfg` не удалась, дальнейшая миграция/синхронизация не продолжается.
- `workflow-profiles.json` пишется атомарно.
- При ошибке profile storage launcher может восстановить состояние из legacy `connections.cfg` + `config.json`.
- `config.json` обновляется только после успешной активации профиля.

## Compatibility guarantees
- `PATH[N]` остаётся первичной пользовательской опорой.
- Старые `connections.cfg` и `config.json` остаются валидными.
- Переключение/создание области не пересоздаёт Bearer token.
- Без `workflow-profiles.json` launcher синтезирует profiles lazily.
