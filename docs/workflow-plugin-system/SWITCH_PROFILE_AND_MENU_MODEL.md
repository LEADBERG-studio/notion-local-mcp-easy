# Switch Profile and Menu Model

## Status
Covers TASK-006.

## Goal
Меню должно переключать не просто путь, а весь workflow profile, построенный на `PATH[N]`.

## Switch algorithm
1. Launcher показывает занятые `PATH[N]` из `connections.cfg`.
2. Для каждого слота он пытается найти профиль в `workflow-profiles.json`.
3. При выборе слота launcher:
   - проверяет доступность папки;
   - синхронизирует profile storage со slot map;
   - находит профиль по `pathSlot`;
   - помечает профиль активным;
   - зеркалит `workspacePath` и `accessMode` в legacy `config.json`;
   - передаёт `MCP_PROFILE_STORAGE` и `MCP_PROFILE_ID` в runtime env.
4. `server.py` поднимает runtime уже из активного профиля.

## What selection means now
Выбор слота означает одновременный выбор:
- рабочей области;
- режима доступа (`file_only` / `trusted`);
- profile environment state (`DEFAULT` / `CUSTOM`).

## Menu presentation
Для каждого saved slot launcher показывает:
- slot number;
- saved path;
- `mode=...`;
- `env=...`;
- marker текущей области;
- warning marker, если папка недоступна.

## Failure handling
- Недоступная папка не активируется.
- Пустой слот не активируется.
- Если профиль для слота отсутствует, он создаётся через sync-механику из slot anchor.
- Если storage повреждён, следующая безопасная стратегия — восстановление из `connections.cfg` + `config.json`.

## Global plugin semantics during switch
- `globalPlugins` загружаются вне профиля.
- Area-specific state загружается из `profiles[profileId].plugins`.
- Глобальный attach не маскируется под `CUSTOM`.
- После переключения профиля plugin registry должен быть пересобран уже для нового active profile.

## Current implementation boundary
- Launcher уже активирует полный profile state для workspace + access mode.
- Menu уже показывает `mode` и `env`.
- Runtime already receives active profile metadata through environment variables.
- Глубокая runtime-пересборка plugin registry относится к следующим задачам и требует отдельной verification step.
