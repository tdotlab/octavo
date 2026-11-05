# Test rewrites

- Independent experiments live in their own language-named directories.
- The upstream C++ program is the authority. Do not modify its source, tests, or build scripts as part of a rewrite.
- Keep the source commit SHA and inspection date accurate in the parent and per-port READMEs.
- Keep each rewrite self-contained, with tests and its own README; avoid shared runtime dependencies between ports.
- Do not call a port finished until its build and relevant tests have been run. Document behavior differences plainly.
- Python entry point: `python/octavo.py`; Python tests: `python/test_octavo.py`.
