# Notion Local MCP Easy 1.7.4







One-click Windows MCP-сервер для личного использования с Notion Agent. Агент получает инструменты для чтения, поиска и изменения файлов в выбранной рабочей папке. При необходимости можно отдельно включить доверенный developer-режим с Python, Git, Node и тестами.







> Проект предназначен для собственного компьютера и доверенного Notion Agent. Это не многопользовательский публичный сервис.









## Документация для начинающих

Подробная русская документация оформлена как локальный статический сайт:

```text
docs/ru/index.html
```

Откройте этот файл двойным кликом в браузере. Там есть пошаговые инструкции по auth modes, OAuth, Tunnellio, Serveo, self-hosted sish, reverse proxy, подключению MCP-клиентов, workflow profiles и плагинам.

## Запуск за несколько минут







1. Распакуйте архив в любую папку.



2. Дважды кликните `START.bat`.



3. При первом запуске выберите рабочую папку.



4. Затем выберите tunnel mode: **Tunnellio managed runtime**, **Serveo temporary domain**, **Serveo stable domain** с reserved hostname и SSH key, **Self-hosted sish relay** для собственного SSH relay, либо **Custom public URL / reverse proxy** без встроенного туннеля.



5. Оставьте **trusted developer mode выключенным**, если нужны только файловые инструменты. Для написания и запуска кода его можно включить ответом `y`.



6. Для bearer-only сценария используйте показанные `URL` и `Authorization=Bearer <token> (Bearer token)` в Custom MCP вашего Notion Agent. Для OAuth/dual сценария сначала выполните `OAUTH_SETUP.bat`, затем используйте публичный MCP URL, discovery endpoint и owner code, который показывает launcher.



7. Не закрывайте окно запуска во время работы.







При последующих запусках повторная настройка не требуется. Конфигурация хранится в `%LOCALAPPDATA%\NotionMcpEasy` и не входит в архив проекта. Начиная с 1.4.2 список быстрых переключений между рабочими областями хранится в `connections.cfg` рядом с `launcher.py`: при `MENU = on` сервер показывает сохранённые пути и меняет только текущий `workspace` в `config.json`, не пересоздавая токен доступа и не заставляя заново переподключать MCP к агенту.







Дополнительно launcher теперь ведёт локальное profile-aware хранилище `%LOCALAPPDATA%\NotionMcpEasy\workflow-profiles.json`: `PATH[N]` остаётся базовым anchor saved-областей, но каждая область разворачивается в workflow profile с `accessMode`, `environmentMode`, metadata и area-specific plugin state. Активный профиль по-прежнему зеркалится обратно в legacy `config.json`, чтобы старый launcher/config/runtime flow не ломался.



Поверх этого работает универсальная plugin-system: плагины подключаются в `global` или `current` scope, получают effective mode (`read_only` или `full_access`) и пересобираются при смене активной рабочей области. В комплекте уже подтверждены DB-family плагины `sqlite` и `postgres`, а также AI/subagent family `openai_compat` с secret-ref моделью через env.








## IDE Provider для локальной IDE

Начиная с 1.7.4 в комплект входит плагин `ide_provider`. Он позволяет поднять локальный OpenAI-compatible endpoint на `127.0.0.1`, подключить его в IDE как обычного AI provider и обслуживать IDE-запросы активной MCP-моделью.

Короткий сценарий:

1. Запустите рабочую область в trusted developer mode.
2. Подключите плагин `ide_provider` к нужному workflow profile.
3. Вызовите `ide_provider_start`.
4. Скопируйте в IDE `base_url`, `api_key` и `model`.
5. Когда IDE отправляет запрос, активная MCP-модель забирает его через `ide_provider_wait_request` и отвечает через `ide_provider_send_response`.

Подробная инструкция для новичков: `docs/ru/ide-provider.html`.


## OAuth через Tunnellio

Начиная с 1.5.1 launcher умеет не только запускать OAuth/dual режим, но и отдельно проводить operator setup через `OAUTH_SETUP.bat` и pre-register BYO clients через `REGISTER_OAUTH_CLIENT.bat`. Production-путь для внешних MCP-клиентов выглядит так:

1. В `%LOCALAPPDATA%\NotionMcpEasy\config.json` включите `"tunnel_backend": "tunnellio"` и выберите `"auth_mode": "oauth"` либо `"dual"` для переходного периода.
2. Для постоянного публичного адреса задайте `tunnellio_domain` и, если этого требует ваш runtime / reservation flow, `tunnellio_key`.
3. Запустите `START.bat` и дождитесь рабочего публичного URL.
4. В кабинете Tunnellio создайте или обновите OAuth app для MCP-клиента.
5. Вставьте в OAuth app **точный Redirect URI**, который показывает клиентский UI. Не сокращайте и не переписывайте его вручную.
6. Для обычной работы начинайте со scopes `mcp.read mcp.write`. `mcp.admin` добавляйте только если клиент действительно должен выполнять административные операции.
7. Для public client оставляйте `client_secret` пустым и используйте PKCE. Для confidential client сохраните и `client_id`, и `client_secret`.
8. Если клиент умеет discovery-first flow, используйте публичный базовый URL MCP/Tunnellio и стандартные discovery endpoints. Если клиент требует ручной ввод, используйте: `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource`, `/oauth/authorize`, `/oauth/token`, `/oauth/introspect`.

Подробная пошаговая инструкция вынесена в `docs/OAUTH_WITH_TUNNELLIO.md`.

## Self-hosted sish relay

Начиная с 1.7.0 launcher поддерживает отдельный self-hosted backend **sish** для случаев, когда публичный SSH relay принадлежит вам, а lifecycle туннеля всё ещё должен управляться самим launcher-ом.

1. В `SETUP.bat` выберите **Self-hosted sish relay (SSH reverse tunnel)**.
2. Укажите `tunnel_host`, `tunnel_ssh_port`, публичный wildcard-домен `tunnel_domain` и reserved subdomain label.
3. Подключите приватный SSH-ключ, который принимает ваш relay.
4. Используйте итоговый публичный origin вида `https://<serveo_hostname>.<tunnel_domain>` как MCP endpoint base URL.

В этом режиме launcher сам поднимает SSH reverse tunnel, а стабильный внешний URL вычисляется из `serveo_hostname + tunnel_domain`. Полная инструкция вынесена в `docs/SISH_SETUP.md`.

## Собственный домен и reverse proxy

Начиная с perimeter milestone 1.6.0 launcher поддерживает режим **Custom public URL / reverse proxy**:

1. В `SETUP.bat` выберите **Custom public URL / reverse proxy (no built-in tunnel)**.
2. Укажите публичный базовый URL вида `https://mcp.example.com`.
3. Настройте nginx / Caddy / Traefik или другой proxy так, чтобы он проксировал этот host на локальный MCP порт `127.0.0.1`.
4. Используйте этот же origin для MCP endpoint (`/mcp`), OAuth issuer и discovery endpoints.

В этом режиме launcher **не** поднимает Serveo/Tunnellio и использует `public_url` как канонический внешний адрес. Подробности и инварианты вынесены в `REVERSE_PROXY.md`.

## Управление







- `START.bat` — создать локальное `.venv`, установить зависимости и запустить сервер с туннелем. Если `connections.cfg` содержит `MENU = on`, перед стартом появится меню сохранённых рабочих областей. При наличии рабочего `tunnellio.exe` launcher использует Tunnellio backend, иначе остаётся на Serveo-совместимости.



- `STOP.bat` — остановить только процессы этого MCP после проверки их идентичности.



- `SETUP.bat` — заново пройти мастер настройки; токен при повторном setup сохраняется, а выбранная рабочая область попадает в `connections.cfg`.



- `SHOW_CONNECTION.bat` — показать текущие URL, токен, workspace, auth mode и OAuth discovery / owner code (по умолчанию секреты замаскированы).

- `OAUTH_SETUP.bat` / `oauth_setup.sh` — переключить `legacy` / `oauth` / `dual`, сохранить или перевыпустить owner code и получить operator summary для OAuth-клиентов.

- `REGISTER_OAUTH_CLIENT.bat` / `register_oauth_client.sh` — заранее зарегистрировать OAuth client для Bring Your Own OAuth App flow и сохранить его в локальном `oauth_state.json`.







## connections.cfg







В корне сервера лежит файл `connections.cfg`. Он создаётся автоматически и содержит:







- `MENU = on/off` — показывать ли меню выбора рабочей области при старте;



- `PATH[1] ... PATH[9]` — стартовые слоты для сохранённых путей;



- дополнительные слоты `PATH[10]`, `PATH[11]` и дальше можно добавлять вручную или через меню, если базовые места заняты.







Когда меню включено, запуск показывает только занятые слоты и предлагает:







- выбрать сохранённую рабочую область по номеру;



- нажать `0`, чтобы задать новую папку и сохранить её в свободный слот;



- нажать `q`, чтобы отключить меню и оставить последнюю выбранную область в `config.json`.







Все подсказки во время запуска сообщают, где находятся `connections.cfg` и `%LOCALAPPDATA%\NotionMcpEasy\config.json`, чтобы их было легко отредактировать вручную.







## Режимы







### File-only mode — по умолчанию







Файловые инструменты разрешены только внутри выбранного workspace. Пути нормализуются, а выход через `..`, абсолютные пути и ссылки наружу отклоняется.







### Trusted developer mode — опционально







Добавляет запуск разрешённых программ без `cmd.exe` и PowerShell:







```text



python, py, pip, git, node, npm, npx, pytest, ruff, make, uv



```







Это **не песочница**. Python, Node, Git hooks, npm scripts и другие инструменты могут обращаться ко всей системе и сети с правами текущего пользователя Windows. Включайте режим только для личного доверенного агента. Git через MCP теперь проходит через отдельный setup-flow: без local repo context (`agent-repo-config.local.json`) обычные git-команды блокируются, а агент должен сначала либо привязать существующий репозиторий, либо инициализировать новый, либо явно отключить git для этой папки. Если сервер собирается принять значения по умолчанию или изменить уже сохранённую git-привязку, агент обязан запросить явное подтверждение пользователя.







## Архитектура







```text



Notion Agent



    -> HTTPS + Bearer token



Tunnellio managed tunnel (default) / Serveo fallback



    -> 127.0.0.1:8765



FastMCP server



    -> выбранный workspace



```







Сервер слушает только localhost. Все HTTP-маршруты, включая `/health`, требуют токен. FastMCP Host-проверка отключена намеренно: публичный домен определяется внешним tunnel backend и не должен ломать проксирование.







Если рядом с `launcher.py` лежит рабочий `tunnellio.exe`, launcher по умолчанию поднимает managed-runtime через Tunnellio и получает итоговый публичный URL из runtime snapshot `show-config --name ...`, не парся SSH-вывод.







Serveo остаётся режимом совместимости: launcher использует его, если в конфигурации уже сохранены `serveo_hostname` / `ssh_key` или если Tunnellio-клиент недоступен. Для stable Serveo mode по-прежнему можно зарезервировать hostname и подключить SSH-ключ.

Для self-hosted relay появился отдельный backend **sish**: launcher поднимает обычный SSH reverse tunnel на ваш relay и выводит стабильный внешний origin из пары `serveo_hostname + tunnel_domain`. Это удобно, когда tunnel endpoint принадлежит вам, но не хочется поддерживать отдельный reverse proxy path без встроенного SSH-туннеля. Пошаговая настройка вынесена в `docs/SISH_SETUP.md`.







## Инструменты







- `workspace_info` — workspace, активный режим, root repo и краткий обзор nested repo;



- `repo_context_status`, `inspect_git_repository` — диагностика git и следующего безопасного шага;



- `setup_git_context`, `configure_repo_context` — инициализация, привязка, перепривязка или отключение git для конкретной папки с обязательным выбором branch policy;



- `list_dir`, `file_info`, `read_file`;



- `write_file`, `append_file`, `edit_file`;



- `create_dir`, безопасное нерекурсивное `delete_file`;



- `copy_file`, `move_file` — только отдельные файлы;



- `glob_files`, ограниченный текстовый `grep_files`;



- `run_command` — только в trusted developer mode.



- `list_plugins`, `plugin_status` — диагностика profile-aware plugin loader;



- `attach_plugin`, `detach_plugin` — явное подключение discoverable plugins в `current` или `global` scope с последующей пересборкой registry после рестарта MCP.







### Встроенные plugin families







- DB family: `sqlite` и `postgres` с общим foundation layer, canonical toolset (`*_list_connections`, `*_list_tables`, `*_describe_table`, `*_query`, `*_execute`) и profile-aware attach behavior.



- AI/subagent family: `openai_compat` с tools `*_list_models`, `*_describe_provider`, `*_generate_text`, `*_run_subagent`, secret refs через env и trusted-only gating для subagent/full-access сценариев.



- Authoring kit: для генерации совместимых плагинов через внешнюю модель есть `docs/workflow-plugin-system/AI_PLUGIN_AUTHORING_PROMPT.md`, `PLUGIN_AUTHORING_CHECKLIST.md`, `PLUGIN_AUTHORING_PACKAGE_EXAMPLES.md` и `PLUGIN_PROMPT_TRACEABILITY_MATRIX.md`.







`read_file()` теперь читает длинные файлы частями: показывает диапазон строк, общее число строк и `next offset` для продолжения. Если `run_command()`, `grep_files()` или `list_dir()` возвращают слишком большой результат, MCP сохраняет полный вывод во временный файл и отдаёт первую безопасную часть с путём вида `@temp/...` для продолжения через `read_file()`.







Regex-поиск отключён, чтобы исключить зависание на патологических выражениях. Обычный регистронезависимый поиск остаётся доступен.







## Ограничения







- текстовый файл для чтения и итогового append/edit: до 5 МБ;



- один write/append: до 2 МБ;



- `read_file()` по умолчанию выдаёт до 400 строк, но в первую очередь ограничивается безопасным бюджетом около 9 500 символов, сохраняя целые строки;



- небольшие результаты команд отдаются напрямую, а большие автоматически сохраняются во временный файл и продолжаются через `read_file()`;



- `git` через MCP запрещён, пока не завершён local setup-flow: при отсутствии `.git` агент должен спросить пользователя, создаём новый репозиторий, подключаемся к существующему или временно отключаем git;



- при настройке repo context пользователь теперь должен явно выбрать branch policy: коммит в ветку по умолчанию (`default_branch`) или в явно заданную ветку (`commit_branch`);



- после настройки MCP сверяет `remote.origin.url` с сохранённой локальной привязкой и блокирует git при несовпадении, а commit/push/merge/rebase блокирует вне выбранной ветки;



- если настройка git уже сохранена, её нельзя молча менять: для default-значений и для перепривязки требуются отдельные явные подтверждения пользователя;



- обычные mutating git-команды вроде `reset`, `checkout -B`, `tag`, `config` и `remote set-url` теперь дополнительно фильтруются политикой MCP и не должны обходить setup-flow.



- временные MCP-файлы используют путь вида `@temp/...`, лежат в `temp/` рядом с `server.py`, удаляются после финального чтения и дополнительно очищаются при старте;



- timeout команды по-прежнему останавливает дерево процесса;



- рекурсивное удаление и перемещение каталогов через MCP отсутствуют;



- `node_modules`, `.venv`, `.git` и кэши пропускаются при рекурсивном просмотре.







## Требования







- Windows 10/11;



- Python 3.11+ с опцией `Add Python to PATH`;



- рабочий `tunnellio.exe` в корне проекта или другой совместимый Tunnellio client binary;



- встроенный OpenSSH Client (`ssh.exe`) для Serveo-совместимости и self-hosted `sish` relay;



- интернет при первой установке и для публичного туннеля (Tunnellio или Serveo).







## Проверка







```bat



.venv\Scripts\python -m unittest discover -s tests -v



.venv\Scripts\ruff check .



```







Тесты покрывают path traversal, allowlist, занятый порт, PID-проверку, правильный и неправильный токены, Serveo Host без HTTP 421, Tunnellio command/snapshot integration, chunked-выдачу, repo bootstrap / disable / mismatch guard и timeout процесса.







## Что улучшено относительно оригинала







- автоматический setup без ручного редактирования BAT-файлов;



- токен и runtime вне проекта;



- localhost-only bind и обязательная Bearer-авторизация;



- правильная проверка границ workspace;



- файловый режим безопаснее и включён по умолчанию;



- команды вынесены в явно доверенный режим;



- нет shell, фоновых команд, HTTP downloader и чтения env через MCP;



- автоматический перевод больших результатов в temp-файлы с продолжением через `read_file()` вместо попытки отправить всё модели одним ответом;



- обязательный setup-flow для git: bind existing / init new / attach existing remote / disable git with persisted local policy;



- локальная repo-привязка для Git с проверкой `origin` после перезапуска MCP;



- проверка занятого порта до создания туннеля;



- проверка идентичности PID перед остановкой;



- фиксированные зависимости, тесты, changelog и security model.







Подробная модель безопасности: `SECURITY.md`. История версий: `CHANGELOG.md`.







## Git setup-flow для агента







Если обычная git-команда вызывается впервые для этой папки, MCP больше не пытается угадывать репозиторий. Вместо этого агент должен сначала вызвать `repo_context_status()` и, при необходимости, предложить пользователю выбор:







1. `setup_git_context(mode="init_new_repo", repository_url="...", fork_status="fork|not_fork", branch_mode="default_branch|specified_branch", default_branch="main", commit_branch="stablefix")`



2. `setup_git_context(mode="attach_to_remote", repository_url="...", fork_status="fork|not_fork", branch_mode="default_branch|specified_branch", default_branch="main", commit_branch="stablefix")`



3. `setup_git_context(mode="bind_existing_repo", repository_url="...", fork_status="fork|not_fork", branch_mode="default_branch|specified_branch", default_branch="main", commit_branch="stablefix")`



4. `setup_git_context(mode="disable_git")`







Это состояние сохраняется в `agent-repo-config.local.json` в корне workspace и переживает перезапуск MCP. Вместе с repo URL там хранится branch policy: либо коммиты разрешены только в ветку по умолчанию, либо только в явно заданную ветку. Файл intentionally local-only: он исключён из Git и release-архивов.







## Сборка архива для отправки







Запустите `BUILD_RELEASE.bat`. Архив `notion-mcp-easy-<версия>.zip` появится в папке `release/` внутри проекта. Эта папка создаётся автоматически, исключена из Git и не попадает в сам release-архив. Сборщик автоматически исключает `.venv`, кэши, логи, ZIP-файлы, временную папку `temp/`, папку `release/`, локальные repo-файлы и файлы конфигурации/токенов.







## Serveo fallback guide







Подробная инструкция по временному и постоянному URL, созданию аккаунта, SSH-ключа, резервированию hostname, настройке Notion и устранению ошибок для Serveo-совместимости находится в `SERVEO_SETUP.md`.



