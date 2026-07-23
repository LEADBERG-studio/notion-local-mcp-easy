# TASK-013 — Test matrix and resume safety

- Default status: `QUEUED`
- Required verification: `V3`

## Goal
Собрать тестовую матрицу, миграционные проверки и pause/resume discipline до уровня production hardening.

## Dependencies
- `TASK-005`
- `TASK-006`
- `TASK-008`
- `TASK-010`
- `TASK-011`
- `TASK-012`

## Required code/docs outputs
- полная test matrix;
- migration checks;
- regression checklist;
- resume-safety checklist;
- явное перечисление обязательных тестовых команд и ручных walkthroughs.

## Steps
1. Определить unit/integration/migration/regression сценарии для workflow profiles на основе `PATH[N]`.
2. Проверить create/save/switch для DEFAULT/CUSTOM.
3. Проверить global plugin attach и area-specific attach.
4. Проверить plugin registry rebuild после profile switch.
5. Проверить миграцию старых `PATH[N]` и rollback strategy.
6. Проверить, что handoff-пакет и diagnostics позволяют возобновить работу после паузы.
7. Зафиксировать обязательный regression набор, который должен быть зелёным перед `TASK-014`.

## Required verification evidence
- выполненные тесты и их результаты;
- матрица покрытых/непокрытых сценариев;
- явный список remaining gaps;
- подтверждение, что resume safety доказана, а не предполагается.

## Expected outputs
- test matrix;
- migration checks;
- resume-safety checklist.

## Done when
- ключевые сценарии покрыты тестами и walkthroughs;
- migration path безопасен;
- различие global attach и area-specific attach проверено;
- работу можно прерывать без потери контекста;
- есть обязательный regression набор перед release candidate.

## Cannot be marked DONE unless
- перечислены и выполнены обязательные тестовые команды;
- есть coverage matrix по критическим потокам;
- есть явное указание, какие сценарии ещё не покрыты, если такие остались;
- задача не заменена частичным локальным прогоном, выдаваемым за production hardening.

## Handoff must include
- список покрытых сценариев;
- остающиеся непокрытые риски;
- доказательство resume safety;
- regression gate перед TASK-014;
- next task: `TASK-014`.
