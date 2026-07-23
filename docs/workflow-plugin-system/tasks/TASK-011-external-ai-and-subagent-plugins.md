# TASK-011 — External AI and subagent plugins

- Default status: `QUEUED`
- Required verification: `V3`

## Goal
Спроектировать и реализовать семейство плагинов для внешних ИИ-провайдеров и использования их как субагентов внутри profile-aware правил с поддержкой global и area-specific attach.

## Dependencies
- `TASK-007`
- `TASK-008`

## Required code/docs outputs
- AI provider plugin contract;
- subagent plugin contract;
- минимум один реализованный AI/plugin path и один subagent-compatible path или эквивалентное реальное доказательство совместимости;
- documented secret-handling model;
- tests/walkthroughs для attach scope и safety behavior.

## Steps
1. Развести обычный AI provider plugin и subagent plugin как разные, но совместимые роли.
2. Зафиксировать минимальные tools: list models, generate text, run subagent, describe provider.
3. Описать profile-specific routing по моделям и провайдерам.
4. Добавить правила secret handling и безопасной диагностики.
5. Зафиксировать, как провайдер или субагент подключается глобально или только к текущей области.
6. Проверить, что subagent use case не ломает safety model и effective mode.
7. Реализовать минимальный рабочий код/каркас и проверить его не ниже V3.

## Required verification evidence
- tests или явные walkthroughs для provider/subagent attach scope;
- подтверждение, что secrets не текут в diagnostics;
- подтверждение, что subagent invocation не обходит effective mode и profile rules.

## Expected outputs
- AI provider contract;
- subagent contract;
- safety notes.

## Done when
- есть ясная архитектурная и кодовая основа для внешних AI plugins;
- subagent-вызовы не обходят profile rules;
- профиль может задавать provider/model routing без нарушения безопасности;
- attach scope для AI plugins формализован явно;
- есть реальное доказательство совместимости, а не только текстовый черновик.

## Cannot be marked DONE unless
- есть минимальный рабочий AI/subagent path или равноценное доказуемое implementation-level основание;
- attach scope и secret handling подтверждены тестами/проверками;
- задача не остаётся на уровне концепта без кода;
- задача не выдается за завершённую, если это только draft для будущей работы.

## Handoff must include
- различия provider vs subagent plugins;
- tool families;
- global vs area attach behavior;
- risks around secrets and observability;
- пройденные проверки;
- next task: `TASK-012`.
