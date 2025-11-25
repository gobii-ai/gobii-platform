Running unit tests:

- Use the uv '.venv' already existing in the dir.
- Prefer to run using `uv run python manage.py test --settings=config.test_settings --parallel auto`,
  Or fall back to `python manage.py test --settings=config.test_settings`.
- Typically, run only the test(s) you need, then at the end run the full suite.
- Use `uv run` for almost everything python related

Writing unit tests:
- Ensure the test is tagged with a batch tag, e.g. `@tag('my_feature_batch')`
- Ensure the tag is registered in ci.yml

Python:
- Do NOT do annotations imports like `from __future__ import annotations`

For now, only run specific tests, not the full suite, as that will crash.

Design/UX:

- Use existing components where possible
  - Extract out common/new components as needed
- Reference preline components in vendor/
- Do not use light gray backgrounds for anything
- Do not use horizontal rules
- Keep components "integrated" --avoid container-in-container looks
- Avoid stacked shadows

Code quality:
- Leave the codebase in a better state than when you started. If you can do light refactoring, cleanup, have a net negative lines of code, that is encouraged.
- Pay careful attention to code organization and structure, where files go, etc. Fit the existing structure.
- Do things the right way.
