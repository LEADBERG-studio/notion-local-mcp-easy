# TASK-001 — Current state audit

- Default status: `QUEUED`
- Required verification: `V1`

## Goal
Провести аудит текущих launcher/config/runtime механизмов и зафиксировать, как существующая логика `PATH[N]` должна быть развита в workflow-profile модель без разрыва с текущей схемой.

## Inputs
- `README.md`
- `launcher.py`
- `server.py`
- `connections.cfg`
- локальные repo notes

## Steps
1. Зафиксировать текущий жизненный цикл: setup -> save workspace -> startup menu -> switch -> runtime.
2. Найти все места, где сохранённая область понимается только как путь или slot.
3. Найти, где должны появиться access mode и environment config как часть выбора пути.
4. Найти, где формируется diagnostics/status surface.
5. Составить список пробелов относительно новой цели: profile-aware модель на базе `PATH[N]`, DEFAULT/CUSTOM semantics, plugin attach scopes, plugin layer.
6. Оставить audit summary и список затрагиваемых файлов.

## Expected outputs
- audit summary;
- карта точек интеграции;
- список архитектурных пробелов.

## Done when
- понятно, какие файлы отвечают за setup, menu, persistence и diagnostics;
- есть список мест, которые придётся перепроектировать;
- следующий исполнитель может переходить к domain model без повторного аудита.

## Handoff must include
- найденные точки интеграции;
- затрагиваемые файлы;
- главный architectural gap;
- next task: `TASK-002`.
