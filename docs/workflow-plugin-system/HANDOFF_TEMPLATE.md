# Handoff Template

Используй этот шаблон в конце каждого задания или перед любой паузой.

```text
Phase:
Task ID:
Task title:
Status: ACTIVE | BLOCKED | DONE

What was completed:
- ...
- ...

Files created/changed:
- ...
- ...

Decisions locked in:
- ...
- ...

Verification level reached:
- V0 | V1 | V2 | V3 | V4
- Evidence: tests / logs / diff review / manual walkthrough

Open risks / unknowns:
- ...
- ...

Next recommended task:
- TASK-XXX ...

Safe resume point:
- exact place to continue from
```

## Минимальный handoff, если задача не завершена

Даже при аварийной остановке нужно обязательно оставить:
- последний изменённый файл;
- последнее безопасное состояние;
- что нельзя делать следующим исполнителем, пока не закрыт текущий риск;
- первый следующий шаг для возобновления.
