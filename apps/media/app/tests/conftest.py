import logging
import os
import shutil
import tempfile
from collections.abc import AsyncGenerator
from io import BytesIO
from pathlib import Path

# Set *before* any project import triggers the Settings singleton -- Settings
# reads these from the environment at construction time, and server/server.py
# constructs one at import time.
_tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)  # noqa: SIM115
_tmp_db.close()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_tmp_db.name}")
os.environ.setdefault("DOMAIN", "test.uln.me")
os.environ.setdefault("UMEDIA_DATA_DIR", tempfile.mkdtemp(prefix="umedia-data-"))
# Deliberately *not* nested under UMEDIA_DATA_DIR: Unix domain socket paths
# have a hard ~108-byte OS limit, and a long enough tmp/data path silently
# exceeds it (see server/config.py's plugin_socket_dir default).
os.environ.setdefault(
    "UMEDIA_PLUGIN_SOCKET_DIR", tempfile.mkdtemp(prefix="umedia-sock-"),
)

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi_mongo_base.sql.models import BaseEntity

from apps.provider_connections.models import ProviderConnection  # noqa: F401
from server.config import Settings
from server.server import app as fastapi_app

# Two generations of superseded test files are skipped explicitly rather
# than left to fail with confusing errors:
# - `apps/files`-era tests (old Beanie/MongoDB models, never wired into
#   this app): test_api / test_config / test_file_manager /
#   test_integration / test_models / test_permission.
# - `apps/resources` *route-level* tests: the dual-layer library
#   (docs/11-dual-layer-library.md) replaced `/resources/*` with
#   `/files/*` (see tests/test_media_file_routes.py), and the tus
#   finalize hook now creates MediaFiles -- so tests driving the
#   unmounted routes can't pass. The `apps/resources` *unit* tests
#   (service/repository/gateway/ACL) still run: that code remains on
#   disk until the cleanup migration drops it.
collect_ignore = [
    "test_api.py",
    "test_config.py",
    "test_file_manager.py",
    "test_integration.py",
    "test_models.py",
    "test_permission.py",
    "test_resource_routes.py",
    "test_resource_uploads.py",
]


@pytest.fixture(scope="session", autouse=True)
def setup_debugpy() -> None:
    if os.getenv("DEBUGPY", "False").lower() in ("true", "1", "yes"):
        import debugpy  # noqa: T100

        debugpy.listen(("127.0.0.1", 3020))  # noqa: T100
        logging.info("Waiting for debugpy client")
        debugpy.wait_for_client()  # noqa: T100


@pytest_asyncio.fixture(scope="module")
async def client() -> AsyncGenerator[httpx.AsyncClient]:
    """ASGI client with the app's real lifespan (DB engine, auth service)."""
    for suffix in ("", "-wal", "-shm"):
        Path(f"{_tmp_db.name}{suffix}").unlink(missing_ok=True)
    async with LifespanManager(fastapi_app):
        # Production creates this app's own tables via `alembic upgrade
        # head` (docker-entrypoint.sh) before the process starts -- tests
        # take the equivalent-but-faster `create_all` shortcut against the
        # engine the lifespan just set up. usso.lite creates its own
        # tables itself (`AuthService.ensure_initialized`, already run by
        # the lifespan above).
        async with fastapi_app.state.engine.begin() as connection:
            await connection.run_sync(BaseEntity.metadata.create_all)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url=f"https://{Settings.root_url}{Settings.base_path}",
        ) as ac:
            yield ac


@pytest.fixture
def sample_file() -> BytesIO:
    """Create a sample file for testing."""
    content = b"Hello, World! This is a test file content."
    file = BytesIO(content)
    file.name = "test.txt"
    return file


def pytest_sessionfinish(exitstatus: int) -> None:
    Path(_tmp_db.name).unlink(missing_ok=True)


@pytest.fixture
def host_key(tmp_path: Path) -> tuple[Path, str]:
    """A fresh ed25519 server key: (private key path, "algo base64" pin)."""
    import subprocess  # noqa: S404 -- fixed argv, local test binaries

    key = tmp_path / "host_key"
    keygen = shutil.which("ssh-keygen")
    if keygen is None:
        pytest.skip("ssh-keygen not installed")
    subprocess.run(  # noqa: S603
        [keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
    )
    algo, blob, *_ = (key.with_suffix(".pub")).read_text().split()
    return key, f"{algo} {blob}"
