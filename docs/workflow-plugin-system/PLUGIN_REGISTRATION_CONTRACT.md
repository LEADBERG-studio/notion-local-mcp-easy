# Universal Plugin Registration Contract

## Назначение

Контракт задаёт единую систему регистрации plugin tools для:
- баз данных;
- внешних API;
- внешних нейросетей;
- субагентов;
- импорт/экспорт и других доменных расширений.

## Главный принцип

Ни один плагин не должен регистрировать инструменты напрямую в обход общего registry layer. Ядро сервера должно всегда знать:
- какой workflow profile активен;
- какой `PATH[N]` лежит в основе активной области;
- какой access mode выбран для этой области;
- `DEFAULT` или `CUSTOM` окружение применено;
- какой global safety mode действует;
- какой effective mode допустим для конкретного плагина: `read_only` или `full_access`.

## Базовая модель области

`PATH[N]` остаётся базовым механизмом сохранённых областей, но каждая такая запись должна разворачиваться в profile-aware конфигурацию.

Выбор пути означает одновременный выбор:
1. рабочей области;
2. режима доступа;
3. конфигурации окружения.

Для конкретной области окружение считается `DEFAULT`, пока:
- к ней не был подключён хотя бы один плагин;
- и для этого плагина не были сохранены area-specific настройки.

Если плагин подключён только глобально и не имеет отдельного сохранённого состояния для области, область может оставаться в `DEFAULT`.

## Подключение плагинов

Подключение плагина должно быть стандартизовано:

1. Пользователь инициирует подключение плагина.
2. Сервер запускает **исполняемый файл / entrypoint** плагина.
3. Во время подключения пользователь выбирает область применения:
   - **текущая конфигурация области**;
   - **глобальная конфигурация**.
4. Система сохраняет результат подключения в соответствующем scope.
5. Если для области появляется plugin-specific saved state, область переходит в `CUSTOM`.

## Обязательные сущности

### Plugin manifest
Минимальные поля:
- `id`
- `display_name`
- `version`
- `entrypoint`
- `supported_modes`
- `capabilities`
- `config_schema_version`
- `required_env`
- `dependencies`
- `required`
- `install_scope_support` (`current`, `global`, `both`)

### Tool descriptor
Каждый инструмент должен описываться декларативно:
- `name`
- `title`
- `description`
- `capability`
- `mode_required`
- `input_schema`
- `output_schema`
- `handler_ref`
- `safety_tags`
- `diagnostic_visibility`

### Runtime contract
Плагин обязан иметь слой, эквивалентный следующим обязанностям:
- validate manifest
- validate config
- load runtime
- register tools through registry
- describe status
- healthcheck
- expose install / attach entrypoint behavior

## Namespace policy

Имена должны быть стабильными и namespaced:
- `sqlite_query`
- `postgres_execute`
- `mysql_describe_table`
- `subagent_run_prompt`
- `provider_list_models`

Правило: один плагин — один namespace root.

## Effective mode policy

Порядок вычисления прав:
1. server safety mode
2. active path/profile selection
3. area access mode
4. global plugin defaults
5. area-specific plugin overrides
6. supported plugin modes
7. final effective mode

Следствия:
- `full_access` невозможен вне trusted mode;
- `CUSTOM` не может обойти global safety gate;
- смена профиля обязана пересобирать plugin registry state;
- подключение плагина в global scope не должно автоматически создавать area-specific `CUSTOM` state без сохранённых настроек области.

## Требования к DB plugins

Минимальные семейства инструментов:
- `*_list_connections`
- `*_list_tables`
- `*_describe_table`
- `*_query`
- `*_execute` только при `full_access`

Контракт должен допускать несколько СУБД на одном реестре: SQLite, PostgreSQL, MySQL/MariaDB и далее.
Также должен поддерживаться выбор, сохранять подключение БД глобально или только для текущей области.

## Требования к AI/subagent plugins

Минимальные семейства инструментов:
- `*_list_models`
- `*_generate_text`
- `*_run_subagent`
- `*_describe_provider`

Дополнительно обязательно:
- profile-aware model routing;
- безопасное хранение secret refs;
- диагностика без утечки секретов;
- ограничение subagent-вызовов profile rules и effective mode;
- выбор scope подключения: глобально или для текущей области.

## Диагностика

Каждый плагин обязан уметь отдавать:
- effective mode;
- enabled/disabled state;
- scope подключения (`global` / `current area`);
- config validation status;
- dependency health;
- last startup error;
- source of current config.
