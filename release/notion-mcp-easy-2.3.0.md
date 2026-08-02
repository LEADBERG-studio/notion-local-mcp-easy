# Notion Local MCP Easy 2.3.0

## Главное

Теперь нормальный IDE Gateway сценарий действительно начинается одной фразой: **«подними мост»**. Модель получает self-contained installer, запускает одну команду внутри собственной песочницы и возвращает готовые OpenAI-compatible настройки IDE.

## Что вошло

- Версия плагина `ide_gateway` поднята до `0.4.0` для нового installer/transport contract.
- Нативный keyless Tunnellio TCP bridge вместо нестабильного sandbox SSH-туннеля.
- Никаких SSH-ключей, регистрации public key или облачного API token в sandbox-клиенте.
- Защищённое локальное хранение внутреннего endpoint/key без вывода секретов в чат.
- Точные model IDs текущей песочницы в `/v1/models`; выбранная модель сохраняется при proxy-запросе.
- OpenAI- и Anthropic-style upstream, streaming и обычные chat completions.
- Detached resident server/bridge, automatic reconnect, idempotent route reuse и `repair`.
- Route propagation больше не убивает здоровые процессы при временном 404/502/504.
- Ротация: не больше пяти server/tunnel logs.
- Отдельный резервный промт, полные RU/EN guides и переписанная документация для новичков.

## Быстрый сценарий

1. `plugins\ide_gateway\SETUP.bat`: `current`, `full_access`, `sandbox`, обычно `ephemeral`.
2. Перезапустить MCP.
3. Написать модели: **«подними мост»**.
4. Вставить возвращённые `base_url`, `api_key`, `model` в OpenAI-compatible provider IDE.

Если короткая команда не распознана, используйте `docs/ru/IDE_GATEWAY_FALLBACK_PROMPT.md`. Не смешивайте этот режим с отдельным queue/poll плагином `ide_bridge`.

## Проверка

- Installer/protocol regression suite: 18 tests OK.
- Full unit suite: 297 tests OK after the final documentation/version pass.
- Live public route returned exact models `model-alpha`, `model-beta`.
- Public chat completion through `model-beta` returned `FULL_PROXY_OK` and preserved the requested model ID.

## Обновление и безопасность

Production config остаётся в `%LOCALAPPDATA%\NotionMcpEasy`; распакованную release-папку можно заменять. Обычный startup не должен перевыпускать token, менять tunnel backend или затирать сохранённые режимы. Перед config write создаётся backup, sensitive fields меняются только через явный setup.
