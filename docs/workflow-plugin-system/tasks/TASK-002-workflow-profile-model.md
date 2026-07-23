# TASK-002 — Workflow profile model

- Default status: `QUEUED`
- Required verification: `V1`

## Goal
Определить каноническую модель workflow profile как profile-aware надстройку **на основе** существующего `PATH[N]`.

## Dependencies
- `TASK-001`

## Steps
1. Зафиксировать, что `PATH[N]` остаётся базовым указателем сохранённой области.
2. Определить, как один `PATH[N]` разворачивается в workflow profile.
3. Зафиксировать обязательные поля: `pathSlot`, `workspacePath`, `accessMode`, `environmentMode`, config refs/inline config, metadata.
4. Разделить persistent и runtime-only данные.
5. Определить, какие поля обязательны для `DEFAULT`, а какие для `CUSTOM`.
6. Добавить пример JSON-представления профиля.

## Expected outputs
- spec модели профиля;
- список обязательных полей;
- пример JSON.

## Done when
- переключение workspace однозначно трактуется как переключение profile, построенного на `PATH[N]`;
- новая модель пригодна и для launcher, и для plugin layer;
- нет неявности в составе профиля.

## Handoff must include
- финальный список полей;
- что считается persistent state;
- что считается runtime state;
- как profile связан с `PATH[N]`;
- next task: `TASK-003`.
