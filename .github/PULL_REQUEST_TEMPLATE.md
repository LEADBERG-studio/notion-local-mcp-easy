## Summary

What does this PR change and why?

## Testing

- [ ] `python -m py_compile server.py launcher.py core.py profiles.py plugin_runtime.py`
- [ ] `python -m unittest discover -s tests -v`
- [ ] Manual smoke test, if applicable:

## Security / compatibility checklist

- [ ] No secrets, tokens, local paths, or generated runtime files are committed.
- [ ] File operations remain constrained to the configured workspace.
- [ ] Command/git behavior remains covered by tests where changed.
- [ ] User-facing docs are updated where behavior changed.
