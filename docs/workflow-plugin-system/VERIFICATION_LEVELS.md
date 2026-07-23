# Verification Levels

## V0 — framing

Только чтение, анализ и постановка следующего шага.

## V1 — spec integrity

Для архитектурных документов и task-файлов.

Нужно проверить:
- нет ли противоречий README и текущим инвариантам;
- все ссылки и зависимости внутри docs согласованы;
- у задачи есть критерий завершения и safe pause point.

## V2 — local implementation confidence

Для launcher/config/diagnostics/plugin-registry кода.

Нужно проверить:
- локальные unit/static проверки;
- хотя бы один основной сценарий вручную;
- соответствие документации и реального поведения.

## V3 — migration and integration confidence

Для migration, profile switching, plugin loading, multi-step сценариев.

Нужно проверить:
- старый и новый форматы конфигов;
- переходы DEFAULT/CUSTOM;
- поведение хотя бы одного failure path;
- восстановление после паузы и handoff.

## V4 — release candidate

Для финального этапа.

Нужно проверить:
- release checklist;
- актуальность корневой документации;
- совместимость superprompt с реальным plugin contract;
- список known limitations и release notes.

## Рекомендация по задачам

- TASK-001 ... TASK-004 -> минимум V1
- TASK-005 ... TASK-009 -> минимум V2
- TASK-010 ... TASK-013 -> минимум V3
- TASK-014 -> V4
