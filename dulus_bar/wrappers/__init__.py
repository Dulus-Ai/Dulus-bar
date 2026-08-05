"""Agent wrappers for Dulus Bar.

These are invoked as standalone scripts (e.g. ``python .../wrappers/agent_wrapper.py``)
to pipe a CLI agent's stdio to the island and forward Allow/Deny. Each one adds
its own directory to ``sys.path`` at runtime, so they resolve their sibling
imports (``base_wrapper``, ``paths``, …) whether packaged here in a pip install
or run from a source checkout.

They live inside the package so the wheel actually ships them — previously they
sat at the repo root and were excluded from the build, so a pip-installed island
launched ``python <missing>/agent_wrapper.py`` and the terminal closed instantly.
"""
