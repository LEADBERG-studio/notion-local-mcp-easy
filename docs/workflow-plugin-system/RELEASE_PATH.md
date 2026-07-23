# Release Path

## Цель релиза

Релиз должен дать:
- развитие существующей схемы `PATH[N]` в profile-aware модель, а не отказ от неё;
- выбор пути как выбор рабочей области, режима доступа и конфигурации окружения;
- явный `DEFAULT/CUSTOM` environment state на область;
- profile-aware launcher switching;
- универсальный plugin registry;
- подключение плагинов через их исполняемый файл/entrypoint;
- возможность подключать плагины либо в текущую конфигурацию области, либо глобально;
- основу для нескольких DB plugins;
- основу для AI/subagent plugins;
- пользовательский authoring kit и superprompt.

## Общий release gate

Работа не может считаться завершённой, если выполнен только checkpoint/baseline/foundation.

Release-ready состояние существует только если:
- `TASK-001` ... `TASK-014` закрыты статусом `DONE`;
- выполнен `MASTER_COMPLETION_CHECKLIST.md`;
- есть release-candidate набор кода, тестов и docs;
- нет blocker-ов по миграции, diagnostics и обратной совместимости.

## Milestone 1 — Profile foundation

Связанные задачи: TASK-001 ... TASK-004.

Готово, когда:
- `PATH[N]` описан как база для workflow profile;
- есть state machine DEFAULT/CUSTOM;
- выбран storage;
- описано, как область остаётся DEFAULT до первого area-specific plugin state;
- описана миграция старого формата.

## Milestone 2 — Launcher integration

Связанные задачи: TASK-005, TASK-006.

Готово, когда:
- новая область создаётся как профиль на основе выбранного пути;
- выбор пути активирует путь, режим доступа и конфигурацию окружения;
- меню показывает состояние профиля;
- launcher понимает разницу между global plugin attach и area-specific attach.

## Milestone 3 — Plugin framework

Связанные задачи: TASK-007 ... TASK-009.

Готово, когда:
- есть manifest/registry contract;
- есть attach/install flow через entrypoint плагина;
- есть loader/effective mode;
- diagnostics показывают active area, plugin scope и plugin state.

Важно: Milestone 3 сам по себе **не завершает инициативу**.

## Milestone 4 — Plugin families

Связанные задачи: TASK-010, TASK-011.

Готово, когда:
- есть foundation для нескольких DB plugins;
- есть foundation для external AI/subagent plugins;
- plugin families поддерживают global и area-specific scopes.

Важно: один SQLite baseline **не считается** закрытием Milestone 4, если не доказана общая family-модель и не закрыты требования `TASK-010` и `TASK-011`.

## Milestone 5 — Self-service plugin creation

Связанная задача: TASK-012.

Готово, когда:
- authoring prompt согласован с реальным contract;
- пользователь может получить совместимый плагин через внешнюю нейросеть;
- generated plugin сразу понимает attach scope и registry rules.

## Milestone 6 — Hardening and release candidate

Связанные задачи: TASK-013, TASK-014.

Готово, когда:
- миграции и интеграции проверены;
- pause/resume discipline работает;
- root docs обновлены;
- сформирован release candidate.

## Обязательные доказательства перед финалом

Перед объявлением `готово` исполнитель обязан показать:
- матрицу статусов всех задач;
- список изменённых файлов кода;
- список новых файлов;
- список обязательных тестов и их результаты;
- отдельную сводку по backward compatibility;
- отдельную сводку по migration safety;
- явное указание, что осталось only for user approval (например commit/push/release), если что-то осталось.
