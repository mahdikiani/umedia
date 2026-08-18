"""Shared provider-plugin contract test suite.

Run this same function against every plugin's real socket -- the reference
plugin here (`tests/test_plugin_contract_reference.py`) and, from Phase 3
on, `local`/`s3`/`rclone`/`telegram`. This is what "keep interfaces
stable" (docs/07-agent-instructions.md) means in practice: one battery of
assertions, unchanged, proves every plugin honors the same contract.

Deliberately generic: it only asserts behavior every compliant plugin must
have regardless of its specific config shape (a plugin-specific "wrong
config is rejected" case belongs in that plugin's own test file, not here).
"""

from plugins.client import PluginClient, PluginRPCError
from plugins.contracts import CreateResourceIn, UpdateResourceIn


async def run_contract_suite(
    client: PluginClient,
    *,
    config: dict[str, object],
) -> None:
    """Exercise the full resource lifecycle against a running plugin."""
    # connect() with valid config does not raise.
    await client.connect(config)

    # A freshly created resource round-trips through create/get/list/content.
    created = await client.create_resource(
        config,
        CreateResourceIn(name="hello.txt", type="file"),
        content=b"hello, umedia",
    )
    assert created.id
    assert created.name == "hello.txt"

    fetched = await client.get_resource(config, created.id)
    assert fetched.id == created.id
    assert fetched.name == "hello.txt"

    listing = await client.list_resources(config, parent_id=created.parent_id)
    assert created.id in {item.id for item in listing}

    content = b"".join(
        [chunk async for chunk in client.read_content(config, created.id)],
    )
    assert content == b"hello, umedia"

    # A resource *inside* a folder round-trips through create/get/content/
    # delete too -- for a path-addressed provider (`local`, `rclone`) this
    # is the case that actually produces a `/`-bearing id
    # (`a-folder/nested.txt`), which needs `encode_resource_id`/
    # `decode_resource_id` (client.py/sdk.py) to even route correctly --
    # a plain `{resource_id}` path segment can't match one otherwise. An
    # opaque-id provider's nested id won't contain a `/`; the same
    # assertions hold for it trivially.
    folder = await client.create_resource(
        config, CreateResourceIn(name="a-folder", type="folder"), content=b"",
    )
    nested = await client.create_resource(
        config,
        CreateResourceIn(name="nested.txt", type="file", parent_id=folder.id),
        content=b"nested content",
    )
    assert (await client.get_resource(config, nested.id)).id == nested.id
    nested_content = b"".join(
        [chunk async for chunk in client.read_content(config, nested.id)],
    )
    assert nested_content == b"nested content"
    await client.delete_resource(config, nested.id)
    await client.delete_resource(config, folder.id)

    # update() renames it. Its `id` may or may not change as a result --
    # a path-addressed provider like `local` has no other notion of
    # identity, while an opaque-id provider (Drive, S3) keeps the same id
    # across a rename. Either is a compliant provider: the contract only
    # requires that the response's own id+name are self-consistent, and
    # that a fresh get() *by that returned id* sees the rename. Callers
    # (the core's `Resource.content_reference`, docs/04-data-model.md)
    # are expected to track the latest id from each write response rather
    # than assume it never changes -- this suite deliberately doesn't test
    # the old id still resolving, because for `local` it must not.
    renamed = await client.update_resource(
        config,
        created.id,
        UpdateResourceIn(name="renamed.txt"),
    )
    assert renamed.name == "renamed.txt"
    assert (await client.get_resource(config, renamed.id)).name == "renamed.txt"

    # delete() removes it; a subsequent get() reports 404, not a crash.
    await client.delete_resource(config, renamed.id)
    try:
        await client.get_resource(config, renamed.id)
    except PluginRPCError as error:
        assert error.status_code == 404
    else:
        raise AssertionError("expected get_resource to 404 after delete")
