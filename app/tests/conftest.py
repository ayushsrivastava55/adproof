"""Test configuration.

The database URL is set before `adproof` is imported anywhere, so no test needs
to reload modules. Reloading would rebind exception classes and make `except`
clauses silently stop matching, which is exactly the kind of false pass this
suite exists to prevent.
"""

import os

os.environ.setdefault(
    "ADPROOF_DATABASE_URL",
    os.getenv("ADPROOF_TEST_DATABASE_URL", "postgresql+psycopg:///adproof_test"),
)
