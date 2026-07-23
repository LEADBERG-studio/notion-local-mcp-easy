# TASK-007 — Plugin manifest and tool registry

- Default status: `QUEUED`
- Required verification: `V1`

## Goal
Определить manifest, tool descriptor и универсальный registry contract для plugin tools с учётом attach scope: в текущую область или глобально.

## Dependencies
- `TASK-002`
- `TASK-003`
- `PLUGIN_REGISTRATION_CONTRACT.md`

## Required code/docs outputs
- обновлённый `PLUGIN_REGISTRATION_CONTRACT.md`;
- явный manifest schema;
- декларативный tool descriptor schema;
- описание attach/install flow через entrypoint;
- зафиксированная capability taxonomy.

## Steps
1. Уточнить обязательные поля plugin manifest, включая scope support и entrypoint.
2. Описать attach/install flow через исполняемый файл плагина.
3. Описать декларативный tool descriptor.
4. Зафиксировать namespace policy и capability taxonomy.
5. Разделить registration contract на `read_only` и `full_access`.
6. Проверить, что контракт одинаково подходит и DB plugins, и AI/subagent plugins.

## Required verification evidence
- документная сверка с `PLUGIN_REGISTRATION_CONTRACT.md`;
- проверка, что scope attach, mode gating и namespace rules описаны без двусмысленностей;
- handoff с перечислением locked decisions.

## Expected outputs
- manifest schema;
- tool descriptor schema;
- registry contract notes.

## Done when
- есть единый контракт регистрации инструментов;
- плагин можно подключить либо к текущей области, либо глобально;
- инструменты namespaced и mode-aware;
- архитектура готова для loader/registry слоя;
- нет открытой двусмысленности по attach scope и capability groups.

## Cannot be marked DONE unless
- contract обновлён в реальной документации, а не только в handoff;
- attach/install flow описан явно;
- есть явная связь с DB и AI/subagent use cases;
- задача не оставлена как `checkpoint` или `baseline`.

## Handoff must include
- обязательные manifest fields;
- capability groups;
- attach scope rules;
- registry invariants;
- next task: `TASK-008`.
