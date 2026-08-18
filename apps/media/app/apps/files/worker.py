from datetime import datetime, timedelta

from .models import FileMetaData


async def remove_deleted_files() -> None:
    files = await FileMetaData.find(
        FileMetaData.is_deleted,
        FileMetaData.deleted_at < datetime.now() - timedelta(days=30),
        not FileMetaData.is_directory,
    ).to_list()
    for file in files:
        await file.delete(file.user_id)


async def remove_deleted_directories() -> None:
    files = await FileMetaData.find(
        FileMetaData.is_deleted,
        FileMetaData.deleted_at < datetime.now() - timedelta(days=30),
        FileMetaData.is_directory,
    ).to_list()
    for file in files:
        await file.delete(file.user_id)


async def remove_old_access_files() -> None:
    files = await FileMetaData.find(
        not FileMetaData.is_deleted,
        FileMetaData.access_at < datetime.now() - timedelta(days=30),
        not FileMetaData.is_directory,
    ).to_list()
    for file in files:
        await file.delete(file.user_id)
