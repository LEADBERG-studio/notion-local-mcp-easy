# Русская документация Notion Local MCP Easy

Откройте локальный сайт:

```text
docs/ru/index.html
```

Можно открыть файл двойным кликом в браузере. Это статический сайт без сервера.

## Разделы

- `index.html` — стартовая страница.
- `quickstart.html` — быстрый старт.
- `oauth.html` — OAuth пошагово для новичка.
- `connections.html` — способы подключения.
- `tunnels.html` — Tunnellio, Serveo, sish.
- `reverse-proxy.html` — свой домен и reverse proxy.
- `plugins.html` — подключение и устройство плагинов.
- `troubleshooting.html` — диагностика частых ошибок.

- `tunnellio.html` — пользовательская настройка Tunnellio: кабинет, публичный URL, API docs, CLI, OAuth и типовые сценарии.

- `ide-gateway.html` — основной OpenAI-compatible мост из IDE в песочницу модели: одна команда «подними мост», keyless Tunnellio TCP bridge и резервный промт.
- `ide-bridge.html` — отдельный совместимый queue/poll мост через `ide_bridge`; не смешивайте его с прямым sandbox-сценарием.
