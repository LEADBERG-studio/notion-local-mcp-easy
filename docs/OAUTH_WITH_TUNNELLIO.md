# OAuth через Tunnellio для Notion Local MCP Easy

Дата: 2026-07-25
Статус: production operator guide

## Когда использовать эту схему
Используйте этот путь, если MCP публикуется через **Tunnellio managed runtime** и внешний MCP-клиент должен подключаться по **OAuth**, а не по legacy bearer token.

## Что должно быть уже готово
- Локальный `notion-mcp-easy` настроен и запускается через `START.bat`.
- В корне проекта лежит рабочий `tunnellio.exe`.
- Серверная часть Tunnellio уже публикует discovery / OAuth endpoints.
- Клиент умеет discovery-first flow или ручной OAuth endpoint setup.

## Настройки в локальном config.json
Откройте `%LOCALAPPDATA%\NotionMcpEasy\config.json` и проверьте минимум такие поля:

```json
{
  "tunnel_backend": "tunnellio",
  "auth_mode": "oauth",
  "tunnellio_domain": "your-stable-domain",
  "tunnellio_key": "your-domain-or-reservation-key",
  "tunnellio_oauth_client_policy": "shared",
  "tunnellio_enable_pkce": true,
  "tunnellio_use_discovery": true
}
```

### Значение auth_mode
- `legacy` — старый bearer-token flow
- `oauth` — основной production OAuth flow
- `dual` — временный переходный режим, если нужно поддержать и старый токен, и новый OAuth

## Как задавать постоянный публичный адрес
Для постоянного домена используется `tunnellio_domain`.

Если конкретный runtime / reservation flow Tunnellio требует отдельную привязку, укажите также `tunnellio_key`.

После запуска итоговый публичный MCP URL определяется самим Tunnellio runtime. Именно этот URL и нужно считать базовым URL сервера для клиента.

## Пошаговое подключение
1. Остановите старый запуск MCP, если он ещё работает.
2. Обновите `%LOCALAPPDATA%\NotionMcpEasy\config.json`.
3. Запустите `START.bat`.
4. Дождитесь, пока launcher покажет рабочий `URL` и завершит health-check.
5. Откройте `SHOW_CONNECTION.bat` и зафиксируйте текущий публичный MCP URL.
6. Откройте кабинет Tunnellio и перейдите в раздел **OAuth apps**.
7. Создайте новое приложение или обновите существующее.
8. Вставьте в него **точный Redirect URI**, который показывает MCP-клиент.
9. Выберите scopes `mcp.read` и `mcp.write` как базовые.
10. Если клиенту действительно нужны административные операции, отдельно добавьте `mcp.admin`.
11. Для public client оставьте `client_secret` пустым и используйте PKCE.
12. Для confidential client сохраните `client_id` и `client_secret` и перенесите их в клиентский UI.

## Какие endpoint-ы использовать
Если клиент поддерживает discovery-first mode, используйте базовый публичный URL и не задавайте endpoints вручную.

Если клиент требует ручной ввод, используйте такие значения относительно публичного базового URL:

- Discovery: `/.well-known/oauth-authorization-server`
- Protected resource metadata: `/.well-known/oauth-protected-resource`
- Authorize endpoint: `/oauth/authorize`
- Token endpoint: `/oauth/token`
- Introspect endpoint: `/oauth/introspect`

## Public vs confidential clients
### Public client
- `client_secret` не нужен
- PKCE обязательно
- Redirect URI должен совпадать побайтно

### Confidential client
- нужен `client_secret`
- Redirect URI тоже должен совпадать побайтно
- PKCE всё равно желательно оставлять включённым, если клиент это поддерживает

## Какие scopes запрашивать
Рекомендуемый baseline:

```text
mcp.read mcp.write
```

`mcp.admin` запрашивайте только тогда, когда без него клиент не может выполнить действительно административную задачу.

## Что важно не перепутать
- Redirect URI берётся **из клиента**, а не придумывается вручную.
- Постоянный публичный домен задаётся через `tunnellio_domain`.
- OAuth app настраивается в кабинете Tunnellio, а не в `notion-mcp-easy`.
- Старый `Bearer token` нужен только для legacy / dual сценариев. Для чистого OAuth production-path ориентируйтесь на OAuth app и discovery.
