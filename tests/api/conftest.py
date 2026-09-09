"""Fixtures for the M5 API tests.

The API service is exercised against the *real* deterministic M4 runtime over
the frozen synthetic universe. All durable state (store, per-run workspace, and
the generated universe) is redirected into a pytest ``tmp_path`` so no
``.aircheck`` state is written into the repository, and the two canonical
demonstration runs are produced exactly once for the whole module.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture(scope="module")
def api_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[object]:
    """A ``TestClient`` bound to an isolated, freshly seeded runtime store.

    Environment overrides are set *before* importing the application module so
    the module-level service constructs against the temporary roots. The client
    context manager triggers the lifespan hook, which seeds the canonical
    Broadcast (paused at the Tier-2 boundary) and Preview (earned DELIVERY_READY)
    runs through the real orchestrator.
    """
    root = tmp_path_factory.mktemp("aircheck_api_state")
    os.environ["AIRCHECK_STORE_ROOT"] = str(root / "store")
    os.environ["AIRCHECK_WORKSPACE_ROOT"] = str(root / "workspace")
    os.environ["AIRCHECK_UNIVERSE_ROOT"] = str(root / "universe")

    from fastapi.testclient import TestClient

    from apps.api.main import app

    with TestClient(app) as client:
        yield client


BROADCAST_RUN = "run_llk_broadcast_042"
PREVIEW_RUN = "run_llk_preview_018"
