Running unit tests:

- Use the uv '.venv' already existing in the dir.
- Run using `python manage.py test --settings=config.test_settings --parallel auto`
- Typically, run only the test(s) you need, then at the end run the full suite.

Writing unit tests:
- Ensure the test is tagged with a batch tag, e.g. `@tag('my_feature_batch')`
- Ensure the tag is registered in ci.yml

Python:
- Do NOT do annotations imports like `from __future__ import annotations`

For now, only run specific tests, not the full suite, as that will crash.
