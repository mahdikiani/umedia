from collections.abc import AsyncGenerator
from datetime import datetime
from io import BytesIO

import pytest

from apps.files.storage_backend.base_storage import StorageBackend


@pytest.fixture
def test_key() -> str:
    """Generate a unique test file key."""
    now = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"testdir/{now}/testfile.txt"


@pytest.fixture
async def test_file(
    storage_backend: StorageBackend,
    sample_file: BytesIO,
    test_key: str,
) -> AsyncGenerator[dict]:
    """Create a sample file for testing."""
    result = await storage_backend.upload_file(
        sample_file, test_key, content_type="text/plain"
    )

    yield result
    await storage_backend.delete_file(test_key)
