import pytest
from backend.testing.test_profiles import included, load_inventory, validate_inventory


def test_inventory_covers_all_python_tests_without_empty_placeholders():
    validate_inventory(load_inventory())


def test_local_profile_excludes_paid_external_and_archived_compatibility():
    assert included("core", "local")
    assert included("postgres", "local")
    assert not included("external", "local")
    assert not included("compatibility", "local")
    assert not included("postgres", "core")


def test_new_or_unclassified_test_fails_inventory_check(tmp_path):
    folder = tmp_path / "backend/tests"
    folder.mkdir(parents=True)
    (folder / "test_new.py").write_text("def test_new(): pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unclassified"):
        validate_inventory({}, tmp_path)
