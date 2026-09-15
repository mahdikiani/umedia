from apps.media_files.errors import MediaFileValidationError
from apps.media_files.placement import PlacementSettings, pick_connection


def test_bound_folder_wins_over_policy() -> None:
    settings = PlacementSettings(policy="default", default_connection_id="a")
    assert (
        pick_connection(
            settings=settings,
            enabled_ids=["a", "b"],
            parent_connection_id="b",
        )
        == "b"
    )


def test_default_policy_uses_configured_then_first_enabled() -> None:
    settings = PlacementSettings(policy="default", default_connection_id="b")
    assert pick_connection(settings=settings, enabled_ids=["a", "b"]) == "b"
    unset = PlacementSettings(policy="default")
    assert pick_connection(settings=unset, enabled_ids=["a", "b"]) == "a"


def test_fill_order_skips_disabled_and_falls_back() -> None:
    settings = PlacementSettings(
        policy="fill_order",
        fill_order=["gone", "b", "a"],
        default_connection_id="a",
    )
    assert pick_connection(settings=settings, enabled_ids=["a", "b"]) == "b"
    empty_order = PlacementSettings(policy="fill_order", default_connection_id="a")
    assert pick_connection(settings=empty_order, enabled_ids=["a", "b"]) == "a"


def test_most_free_currently_follows_fill_order() -> None:
    settings = PlacementSettings(
        policy="most_free",
        fill_order=["b"],
        default_connection_id="a",
    )
    assert pick_connection(settings=settings, enabled_ids=["a", "b"]) == "b"


def test_preferred_connection_wins_at_root() -> None:
    settings = PlacementSettings(policy="default", default_connection_id="a")
    assert (
        pick_connection(
            settings=settings,
            enabled_ids=["a", "b"],
            preferred_connection_id="b",
        )
        == "b"
    )


def test_bound_folder_wins_over_preferred() -> None:
    settings = PlacementSettings(policy="default", default_connection_id="a")
    assert (
        pick_connection(
            settings=settings,
            enabled_ids=["a", "b"],
            parent_connection_id="a",
            preferred_connection_id="b",
        )
        == "a"
    )


def test_no_enabled_storage_is_an_error() -> None:
    try:
        pick_connection(settings=PlacementSettings(), enabled_ids=[])
    except MediaFileValidationError as error:
        assert "No storage" in error.detail
    else:
        raise AssertionError("expected MediaFileValidationError")
