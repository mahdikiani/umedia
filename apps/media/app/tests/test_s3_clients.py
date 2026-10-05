"""Live S3 client coverage: boto3, rclone, AWS CLI, MinIO `mc`, Cyberduck.

Cyberduck has no Linux CLI in this environment; that case replays the
virtual-host Put/Head/Get/Delete sequence the Mac client actually sends.
rclone / aws / mc / boto3 talk to a real HTTP listener on the same FastAPI app.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import boto3
import httpx
import pytest
import pytest_asyncio
import uvicorn
from botocore.client import BaseClient, Config
from botocore.exceptions import ClientError

from apps.user_access_keys.factory import build_user_access_key_service_from_state
from server.config import Settings
from server.server import app as fastapi_app
from tests.test_s3_api import _authenticated, _S3File, _signed_headers

RCLONE = shutil.which("rclone")
AWS = shutil.which("aws")
MCLI = shutil.which("mcli") or shutil.which("mc")


@pytest_asyncio.fixture(scope="module")
async def s3_cli_file(client: httpx.AsyncClient) -> _S3File:
    user_id = await _authenticated(client)
    storage_root = Path(Settings().data_dir) / "storage" / "s3-cli-library"
    connection = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "s3-cli-test-library",
            "config": {"root_path": str(storage_root)},
        },
    )
    assert connection.status_code == 201, connection.text
    content = b"cli library seed"
    uploaded = await client.post(
        "/files",
        data={
            "provider_connection_id": connection.json()["uid"],
            "name": "cli-seed.txt",
        },
        files={"file": ("cli-seed.txt", content, "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    access_keys = build_user_access_key_service_from_state(fastapi_app.state)
    key = await access_keys.ensure_default_key(user_id)
    return _S3File(
        uid=uploaded.json()["uid"],
        name="cli-seed.txt",
        content=content,
        connection_uid=connection.json()["uid"],
        access_key=key.access_key_id,
        secret_key=access_keys.decrypt_secret_str(key),
        key_uid=key.uid,
    )


@pytest_asyncio.fixture(scope="module")
async def s3_http_endpoint(
    client: httpx.AsyncClient, s3_cli_file: _S3File,
) -> str:
    """Path-style S3 root on a real TCP port (`/api/v1/s3`)."""
    del client, s3_cli_file
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    config = uvicorn.Config(
        fastapi_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="off",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)
    else:
        raise RuntimeError("S3 test server did not start")
    try:
        yield f"http://127.0.0.1:{port}{Settings.base_path}/s3"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _cli_env(access_key: str, secret_key: str) -> dict[str, str]:
    env = os.environ.copy()
    env["AWS_ACCESS_KEY_ID"] = access_key
    env["AWS_SECRET_ACCESS_KEY"] = secret_key
    env["AWS_DEFAULT_REGION"] = Settings.S3_COMPAT_REGION
    env["AWS_EC2_METADATA_DISABLED"] = "true"
    env["AWS_REQUEST_CHECKSUM_CALCULATION"] = "when_required"
    env["AWS_RESPONSE_CHECKSUM_VALIDATION"] = "when_required"
    return env


def _run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert completed.returncode == 0, (
        f"{command}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return completed.stdout


def _boto3_client(endpoint: str, s3_cli_file: _S3File) -> BaseClient:
    """Path-style SigV4 client. Checksums off: they break S3-compat gateways."""
    os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
    os.environ.setdefault("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=s3_cli_file.access_key,
        aws_secret_access_key=s3_cli_file.secret_key,
        region_name=Settings.S3_COMPAT_REGION,
        config=Config(
            s3={"addressing_style": "path"},
            signature_version="s3v4",
        ),
    )


def _rclone_env(endpoint: str, s3_cli_file: _S3File) -> dict[str, str]:
    env = _cli_env(s3_cli_file.access_key, s3_cli_file.secret_key)
    env.update({
        "RCLONE_CONFIG_UMEDIA_TYPE": "s3",
        "RCLONE_CONFIG_UMEDIA_PROVIDER": "Minio",
        "RCLONE_CONFIG_UMEDIA_ACCESS_KEY_ID": s3_cli_file.access_key,
        "RCLONE_CONFIG_UMEDIA_SECRET_ACCESS_KEY": s3_cli_file.secret_key,
        "RCLONE_CONFIG_UMEDIA_ENDPOINT": endpoint,
        "RCLONE_CONFIG_UMEDIA_FORCE_PATH_STYLE": "true",
        "RCLONE_CONFIG_UMEDIA_ACL": "private",
        "RCLONE_CONFIG_UMEDIA_NO_CHECK_BUCKET": "true",
        "RCLONE_CONFIG_UMEDIA_REGION": Settings.S3_COMPAT_REGION,
    })
    return env


@pytest.mark.asyncio
async def test_boto3_covers_the_s3_process(
    s3_http_endpoint: str,
    s3_cli_file: _S3File,
) -> None:
    """ListBuckets → HeadBucket → Put → Head/Get → List → presign → Delete.

    This is the process rclone/Cyberduck/aws cli actually walk, through boto3.
    """
    s3 = _boto3_client(s3_http_endpoint, s3_cli_file)
    bucket = Settings.S3_COMPAT_BUCKET
    key = "clients/boto3-process.txt"
    nested = "clients/nested/boto3.txt"
    body = b"from boto3\n"

    names = [item["Name"] for item in s3.list_buckets()["Buckets"]]
    assert bucket in names
    s3.head_bucket(Bucket=bucket)
    s3.create_bucket(Bucket=bucket)

    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="text/plain")
    head = s3.head_object(Bucket=bucket, Key=key)
    assert int(head["ContentLength"]) == len(body)
    assert s3.get_object(Bucket=bucket, Key=key)["Body"].read() == body

    listed = s3.list_objects_v2(Bucket=bucket, Prefix="clients/")
    keys = [item["Key"] for item in listed.get("Contents") or []]
    assert key in keys

    s3.put_object(Bucket=bucket, Key=nested, Body=b"nested-boto3")
    delimited = s3.list_objects_v2(
        Bucket=bucket, Prefix="clients/", Delimiter="/",
    )
    prefixes = [
        item["Prefix"] for item in delimited.get("CommonPrefixes") or []
    ]
    assert "clients/nested/" in prefixes

    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=60,
    )
    async with httpx.AsyncClient() as http:
        downloaded = await http.get(url)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == body

    s3.delete_object(Bucket=bucket, Key=key)
    with pytest.raises(ClientError) as missing:
        s3.head_object(Bucket=bucket, Key=key)
    assert missing.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404

    deleted = s3.delete_objects(
        Bucket=bucket,
        Delete={"Objects": [{"Key": nested}, {"Key": "clients/missing.txt"}]},
    )
    assert deleted["ResponseMetadata"]["HTTPStatusCode"] in {200, 204}


@pytest.mark.asyncio
@pytest.mark.skipif(RCLONE is None, reason="rclone is not installed")
async def test_rclone_list_put_get_delete(
    s3_http_endpoint: str,
    s3_cli_file: _S3File,
    tmp_path: Path,
) -> None:
    source = tmp_path / "rclone-upload.txt"
    source.write_bytes(b"from rclone\n")
    dest = tmp_path / "rclone-download.txt"
    env = _rclone_env(s3_http_endpoint, s3_cli_file)
    bucket = Settings.S3_COMPAT_BUCKET
    key = "cli-rclone.txt"
    remote = f"umedia:{bucket}/{key}"

    buckets = _run([RCLONE, "lsd", "umedia:"], env=env)
    assert bucket in buckets
    _run([RCLONE, "copyto", str(source), remote], env=env)
    listed = _run([RCLONE, "ls", f"umedia:{bucket}"], env=env)
    assert key in listed
    _run([RCLONE, "copyto", remote, str(dest)], env=env)
    assert dest.read_bytes() == b"from rclone\n"
    _run([RCLONE, "deletefile", remote], env=env)
    listed_after = _run([RCLONE, "ls", f"umedia:{bucket}"], env=env)
    assert key not in listed_after


@pytest.mark.asyncio
@pytest.mark.skipif(AWS is None, reason="aws cli is not installed")
async def test_aws_cli_list_put_head_get_delete(
    s3_http_endpoint: str,
    s3_cli_file: _S3File,
    tmp_path: Path,
) -> None:
    source = tmp_path / "aws-upload.txt"
    source.write_bytes(b"from aws cli\n")
    dest = tmp_path / "aws-download.txt"
    env = _cli_env(s3_cli_file.access_key, s3_cli_file.secret_key)
    bucket = Settings.S3_COMPAT_BUCKET
    key = "cli-aws.txt"
    uri = f"s3://{bucket}/{key}"
    aws = [AWS, "--endpoint-url", s3_http_endpoint]

    buckets = _run([*aws, "s3", "ls"], env=env)
    assert bucket in buckets
    _run([*aws, "s3", "cp", str(source), uri], env=env)
    listed = _run([*aws, "s3", "ls", f"s3://{bucket}/"], env=env)
    assert key in listed
    _run(
        [*aws, "s3api", "head-object", "--bucket", bucket, "--key", key],
        env=env,
    )
    _run([*aws, "s3", "cp", uri, str(dest)], env=env)
    assert dest.read_bytes() == b"from aws cli\n"
    _run(
        [*aws, "s3api", "delete-object", "--bucket", bucket, "--key", key],
        env=env,
    )


@pytest.mark.asyncio
@pytest.mark.skipif(MCLI is None, reason="minio client (mcli/mc) is not installed")
async def test_minio_cli_list_put_stat_get_delete(
    s3_http_endpoint: str,
    s3_cli_file: _S3File,
    tmp_path: Path,
) -> None:
    source = tmp_path / "mc-upload.txt"
    source.write_bytes(b"from minio cli\n")
    dest = tmp_path / "mc-download.txt"
    config_dir = tmp_path / "mc-config"
    config_dir.mkdir()
    env = _cli_env(s3_cli_file.access_key, s3_cli_file.secret_key)
    env["MC_CONFIG_DIR"] = str(config_dir)
    alias = "umedia-test"
    bucket = Settings.S3_COMPAT_BUCKET
    # MinIO Client rejects a path in the alias URL; it talks to the host
    # root, which our app also serves as S3 (`s3_root_router`).
    host_only, _, _ = s3_http_endpoint.partition("/api/")
    _run(
        [
            MCLI, "alias", "set", alias, host_only.rstrip("/"),
            s3_cli_file.access_key, s3_cli_file.secret_key,
        ],
        env=env,
    )
    remote = f"{alias}/{bucket}/cli-mc.txt"
    listed_buckets = _run([MCLI, "ls", alias], env=env)
    assert bucket in listed_buckets
    _run([MCLI, "cp", str(source), remote], env=env)
    listed = _run([MCLI, "ls", f"{alias}/{bucket}"], env=env)
    assert "cli-mc.txt" in listed
    _run([MCLI, "stat", remote], env=env)
    _run([MCLI, "cp", remote, str(dest)], env=env)
    assert dest.read_bytes() == b"from minio cli\n"
    _run([MCLI, "rm", remote], env=env)


@pytest.mark.asyncio
async def test_cyberduck_virtual_host_put_head_get(
    client: httpx.AsyncClient,
    s3_cli_file: _S3File,
) -> None:
    await _authenticated(client)
    vhost = f"{Settings.S3_COMPAT_BUCKET}.{Settings.root_url}"
    filename = "Screenshot 1405-05-26 at 2.04.22\u202fPM.png"
    object_path = f"/{filename}"
    content = b"cyberduck-cli-suite"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url=f"https://{vhost}",
        follow_redirects=False,
    ) as root:
        put_response = await root.put(
            object_path,
            content=content,
            headers=_signed_headers(
                method="PUT",
                path=object_path,
                access_key=s3_cli_file.access_key,
                secret_key=s3_cli_file.secret_key,
                body=content,
                host=vhost,
            ),
        )
        head_response = await root.head(
            object_path,
            headers=_signed_headers(
                method="HEAD",
                path=object_path,
                access_key=s3_cli_file.access_key,
                secret_key=s3_cli_file.secret_key,
                host=vhost,
            ),
        )
        get_response = await root.get(
            object_path,
            headers=_signed_headers(
                method="GET",
                path=object_path,
                access_key=s3_cli_file.access_key,
                secret_key=s3_cli_file.secret_key,
                host=vhost,
            ),
        )
        delete_response = await root.delete(
            object_path,
            headers=_signed_headers(
                method="DELETE",
                path=object_path,
                access_key=s3_cli_file.access_key,
                secret_key=s3_cli_file.secret_key,
                host=vhost,
            ),
        )
        head_after_delete = await root.head(
            object_path,
            headers=_signed_headers(
                method="HEAD",
                path=object_path,
                access_key=s3_cli_file.access_key,
                secret_key=s3_cli_file.secret_key,
                host=vhost,
            ),
        )
    assert put_response.status_code == 200, put_response.text
    assert head_response.status_code == 200, head_response.text
    assert head_response.headers["content-length"] == str(len(content))
    assert get_response.content == content
    assert delete_response.status_code == 204, delete_response.text
    assert head_after_delete.status_code == 404


@pytest.mark.asyncio
async def test_cyberduck_bucket_subresources_are_not_list_objects(
    s3_cli_file: _S3File,
) -> None:
    vhost = f"{Settings.S3_COMPAT_BUCKET}.{Settings.root_url}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url=f"https://{vhost}",
        follow_redirects=False,
    ) as root:
        uploads = await root.get(
            "/?delimiter=%2F&uploads=",
            headers=_signed_headers(
                method="GET",
                path="/?delimiter=%2F&uploads=",
                access_key=s3_cli_file.access_key,
                secret_key=s3_cli_file.secret_key,
                host=vhost,
            ),
        )
        ownership = await root.get(
            "/?ownershipControls=",
            headers=_signed_headers(
                method="GET",
                path="/?ownershipControls=",
                access_key=s3_cli_file.access_key,
                secret_key=s3_cli_file.secret_key,
                host=vhost,
            ),
        )
    assert uploads.status_code == 200, uploads.text
    assert "<ListMultipartUploadsResult" in uploads.text
    assert "<ListBucketResult" not in uploads.text
    assert ownership.status_code == 200, ownership.text
    assert "<OwnershipControls" in ownership.text
    assert "<ListBucketResult" not in ownership.text
