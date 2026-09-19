"""Canonical names for persisted citation checks.

The ``findings.check`` database column is intentionally extensible, but P2
adds a third durable value. Keeping these names in one module prevents worker
tasks from writing typo-only finding types that the API/UI cannot recognise.
"""

CHECK_EXISTENCE = "existence"
CHECK_QUOTE = "quote"
CHECK_PROPOSITION = "proposition"
