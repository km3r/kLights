"""The engine's test suites.

Every suite here is a standalone script: it defines its own `check(label, ok,
detail)`, prints PASS/FAIL lines, and exits non-zero if anything failed. That
predates this package and is deliberate -- a suite you can run with nothing but
`python engine/tests/test_clock.py` needs no runner installed at a venue, which
is the same reason the engine itself is stdlib-only.

This package exists only so `python -m engine.tests` can find and aggregate
them. See __main__.py.
"""
