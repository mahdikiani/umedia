"""The user's library as a WebDAV namespace.

Sibling names are not unique in the library, WebDAV paths must be: the
oldest sibling keeps its name and later ones get ` (2)` etc. -- the same
rule the S3 projection uses (`apps.s3.paths`), so a file has one
predictable name in both gateways.
"""

from dataclasses import dataclass

from apps.media_files.schemas import MediaFileRecord
from apps.media_files.services import MediaFileService
from apps.s3.paths import copy_suffix

PAGE = 1000


@dataclass(frozen=True)
class Node:
    segments: tuple[str, ...]
    record: MediaFileRecord | None  # None is the library root

    @property
    def is_collection(self) -> bool:
        return self.record is None or self.record.type == "folder"

    @property
    def parent_id(self) -> str | None:
        return None if self.record is None else self.record.uid

    @property
    def path(self) -> str:
        return "/" + "/".join(self.segments) if self.segments else ""


ROOT = Node((), None)


class DavTree:
    def __init__(self, media: MediaFileService, user_id: str) -> None:
        self._media = media
        self._user_id = user_id

    async def children(self, parent: Node) -> list[Node]:
        records: list[MediaFileRecord] = []
        offset = 0
        while True:
            page = await self._media.list_children(
                parent.parent_id,
                actor_user_id=self._user_id,
                scope="owned",
                limit=PAGE,
                offset=offset,
            )
            records.extend(page.items)
            offset += PAGE
            if offset >= page.total or not page.items:
                break
        claimed: set[str] = set()
        nodes: list[Node] = []
        visible = (r for r in records if r.status != "failed")
        for record in sorted(visible, key=lambda r: (r.created_at, r.uid)):
            index = 1
            while (name := copy_suffix(record.name, index)) in claimed:
                index += 1
            claimed.add(name)
            nodes.append(Node((*parent.segments, name), record))
        return sorted(nodes, key=lambda node: node.segments[-1])

    async def child(self, parent: Node, name: str) -> Node | None:
        for node in await self.children(parent):
            if node.segments[-1] == name:
                return node
        return None

    async def resolve(self, segments: list[str]) -> Node | None:
        node = ROOT
        for segment in segments:
            if not node.is_collection:
                return None
            found = await self.child(node, segment)
            if found is None:
                return None
            node = found
        return node
