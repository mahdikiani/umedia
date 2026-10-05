"""In-memory stand-in for the slice of `huggingface_hub.HfApi` the
Hugging Face plugin uses, plus an httpx transport serving its `resolve`
URLs -- so the plugin's real code runs end-to-end without a network or a
Hub token. Mirrors observed server behavior where it matters: prefix
matching is lexical (`a` also matches `ab/...`), and non-recursive
listings return implicit directories.
"""

import hashlib
from typing import Any
from urllib.parse import unquote

import httpx
from huggingface_hub import BucketFile, BucketFolder
from huggingface_hub.errors import BucketNotFoundError

ENDPOINT = "https://hf.test"


class FakeBucketApi:
    def __init__(self, bucket_id: str = "me/media", **_: Any) -> None:  # noqa: ANN401
        self.endpoint = ENDPOINT
        self.bucket_id = bucket_id
        self.objects: dict[str, bytes] = {}

    def _check(self, bucket_id: str) -> None:
        if bucket_id != self.bucket_id:
            request = httpx.Request("GET", f"{ENDPOINT}/api/buckets/{bucket_id}")
            raise BucketNotFoundError(
                "bucket not found",
                response=httpx.Response(404, request=request),
            )

    def _file(self, path: str) -> BucketFile:
        data = self.objects[path]
        return BucketFile(
            type="file",
            path=path,
            size=len(data),
            xetHash=hashlib.sha256(data).hexdigest(),
        )

    def bucket_info(self, bucket_id: str, **_: Any) -> dict[str, Any]:  # noqa: ANN401
        self._check(bucket_id)
        return {"id": bucket_id}

    def list_bucket_tree(
        self,
        bucket_id: str,
        prefix: str | None = None,
        *,
        recursive: bool | None = None,
        **_: Any,  # noqa: ANN401
    ) -> list[BucketFile | BucketFolder]:
        self._check(bucket_id)
        base = prefix or ""
        out: list[BucketFile | BucketFolder] = []
        seen: set[str] = set()
        for path in sorted(self.objects):
            if not path.startswith(base):  # lexical, like the real server
                continue
            if recursive:
                out.append(self._file(path))
                continue
            start = len(base) + 1 if base and path.startswith(f"{base}/") else len(base)
            head, sep, _ = path[start:].partition("/")
            if sep:
                folder = path[:start] + head
                if folder not in seen:
                    seen.add(folder)
                    out.append(BucketFolder(type="directory", path=folder))
            else:
                out.append(self._file(path))
        return out

    def get_bucket_paths_info(
        self,
        bucket_id: str,
        paths: list[str],
        **_: Any,  # noqa: ANN401
    ) -> list[BucketFile]:
        self._check(bucket_id)
        return [self._file(path) for path in paths if path in self.objects]

    def batch_bucket_files(
        self,
        bucket_id: str,
        *,
        add: list | None = None,
        copy: list | None = None,
        delete: list[str] | None = None,
        **_: Any,  # noqa: ANN401
    ) -> None:
        self._check(bucket_id)
        by_hash = {self._file(p).xet_hash: data for p, data in self.objects.items()}
        for source, destination in add or []:
            data = source if isinstance(source, bytes) else open(source, "rb").read()  # noqa: SIM115
            self.objects[destination] = data
        for _type, _repo, xet_hash, destination in copy or []:
            self.objects[destination] = by_hash[xet_hash]
        for path in delete or []:
            self.objects.pop(path, None)

    def transport(self) -> httpx.MockTransport:
        """Serves `GET /buckets/<id>/resolve/<path>` with Range support."""

        def handler(request: httpx.Request) -> httpx.Response:
            prefix = f"/buckets/{self.bucket_id}/resolve/"
            path = unquote(request.url.path)
            if not path.startswith(prefix) or path[len(prefix) :] not in self.objects:
                return httpx.Response(404)
            data = self.objects[path[len(prefix) :]]
            range_header = request.headers.get("range")
            if range_header:
                start_str, _, end_str = range_header.removeprefix("bytes=").partition(
                    "-"
                )
                end = int(end_str) if end_str else len(data) - 1
                return httpx.Response(206, content=data[int(start_str) : end + 1])
            return httpx.Response(200, content=data)

        return httpx.MockTransport(handler)
