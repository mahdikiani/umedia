import pytest
import pytest_asyncio

from apps.files.models import FileMetaData
from apps.files.schemas import Permission, PermissionEnum, PermissionSchema


@pytest_asyncio.fixture(scope="module", autouse=True)
async def file() -> FileMetaData:
    return await FileMetaData(
        uid="f1",
        user_id="u1",
        public_permission=PermissionSchema(permission=PermissionEnum.READ),
        filename="f1",
        content_type="image/jpeg",
    ).save()


@pytest_asyncio.fixture(scope="module", autouse=True)
async def file_shared() -> FileMetaData:
    return await FileMetaData(
        uid="f2",
        user_id="u1",
        permissions=[Permission(user_id="u2", permission=PermissionEnum.READ)],
        filename="f1",
        content_type="image/jpeg",
    ).save()


@pytest.mark.parametrize(
    "uid, user_id, permission, root_permission, expected",
    [
        ("f1", "u1", PermissionEnum.READ, False, True),
        ("f1", "u2", PermissionEnum.READ, False, True),
        ("f1", "u3", PermissionEnum.READ, False, True),
        ("f1", None, PermissionEnum.READ, False, True),
        ("f2", "u1", PermissionEnum.READ, False, True),
        ("f2", "u2", PermissionEnum.READ, False, True),
        ("f2", "u3", PermissionEnum.READ, False, False),
        ("f2", None, PermissionEnum.READ, False, False),
        ("f1", "u1", PermissionEnum.READ, True, True),
        ("f1", "u2", PermissionEnum.READ, True, True),
        ("f1", "u3", PermissionEnum.READ, True, True),
        ("f1", None, PermissionEnum.READ, True, True),
        ("f2", "u1", PermissionEnum.READ, True, True),
        ("f2", "u2", PermissionEnum.READ, True, True),
        ("f2", "u3", PermissionEnum.READ, True, True),
        ("f2", None, PermissionEnum.READ, True, True),
    ],
)
@pytest.mark.asyncio
async def test_get_file(
    uid: str,
    user_id: str | None,
    permission: PermissionEnum,
    root_permission: bool,
    expected: bool,
) -> None:
    f = await FileMetaData.get_item(
        uid=uid,
        user_id=user_id,
        permission=permission,
        root_permission=root_permission,
    )
    assert bool(f) == expected
