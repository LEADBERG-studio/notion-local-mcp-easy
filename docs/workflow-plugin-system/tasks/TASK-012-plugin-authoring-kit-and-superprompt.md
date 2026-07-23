# TASK-012 — Plugin authoring kit and superprompt

- Default status: `QUEUED`
- Required verification: `V3`

## Goal
Подготовить подробный authoring kit и суперпромт, по которому пользователь сможет получить совместимый плагин через внешнюю нейросеть.

## Dependencies
- `TASK-007`
- `TASK-010`
- `TASK-011`
- `AI_PLUGIN_AUTHORING_PROMPT.md`

## Required code/docs outputs
- обновлённый суперпромт;
- authoring checklist;
- пример generated plugin package shape;
- пример global config и area-specific config;
- проверка against real code/contracts.

## Steps
1. Сверить суперпромт с реальным registry/manifest contract.
2. Добавить checklist обязательных файлов и тестов для generated plugin.
3. Добавить примеры для DB plugin и AI/subagent plugin.
4. Добавить инструкции по profile config, diagnostics и mode gating.
5. Проверить, что по промту можно получить полностью совместимый пакет без скрытых ручных шагов.
6. Обновить документацию так, чтобы она отражала уже реализованный код, а не только желаемую архитектуру.

## Required verification evidence
- таблица соответствия: prompt item -> реальный contract/code path;
- примеры global и area-specific attach;
- список того, что generated plugin обязан пройти по тестам;
- явная проверка, что в промте нет устаревших допущений.

## Expected outputs
- authoring checklist;
- verified superprompt;
- example generated package shape.

## Done when
- пользователь может описать плагин и получить совместимую структуру;
- промт покрывает manifest, config, registry, tests и docs;
- authoring kit соответствует реальному серверному contract;
- prompt синхронизирован с кодом, уже существующим в репозитории.

## Cannot be marked DONE unless
- промт сверён с реальным кодом, а не только с архитектурным документом;
- есть примеры global/area attach и mode gating;
- есть checklist обязательных файлов и тестов;
- задача не оставлена как «черновик полезного промта на будущее».

## Handoff must include
- verified prompt version;
- compatibility checklist;
- missing pieces to close before public use;
- traceability prompt -> code/contracts;
- next task: `TASK-013`.
