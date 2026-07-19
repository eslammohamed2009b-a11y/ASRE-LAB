"""Root pytest conftest.

Intentionally minimal. Category-specific fixtures (and the CadQuery stub,
which is restricted to `tests/unit/`) live in each category's own
`conftest.py`:

- tests/unit/conftest.py         -> stubbed_cadquery_engine fixture (mocked CAD)
- tests/integration/conftest.py  -> client/authorized_client fixtures, real
                                     CadQuery/OCP is required (no stub, no skip)
- tests/e2e/                     -> spins up the real app as a live HTTP server
"""

