# Notion Local MCP Easy 2.1.0

## Главное

IDE Gateway sandbox mode теперь поднимается одной командой «подними мост». Модель запускает `sandbox_bootstrap.py`, который сохраняет внутренний endpoint/key текущей песочницы, стартует resident `sandbox_server.py` и пробрасывает наружу keyless Tunnellio TCP bridge.

## Что изменилось

- Убран SSH-туннель из sandbox path: вместо него `tunnellio bridge --run --watch`.
- Больше не нужны SSH-ключи и регистрация ключей в облаке.
- Public URL сохраняется в endpoint state, hostname переиспользуется, пока живёт домен.
- Custom hostname из setup остаётся постоянным.
- Локальный IDE API key `ideg_...` сохраняется и не должен меняться при обычном обновлении.
- Модельные endpoint/key берутся из env именно той песочницы, где запускается bridge.
- Документация обновлена под новый поток.

## Проверка

- `python -m py_compile ...` OK
- `python tests/test_ide_gateway_bridge.py` OK, 12 tests
