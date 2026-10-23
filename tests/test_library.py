from pipeline.library import get_part, load_catalog, normalize_category, search, slugify


def test_real_catalog_loads():
    parts = load_catalog()
    assert len(parts) >= 1
    assert all(p.part_id for p in parts)
    assert len({p.part_id for p in parts}) == len(parts), "duplicate part_ids"


def test_category_typos_fixed():
    assert normalize_category("Houshold") == "Household"
    assert normalize_category("plumbing") == "Plumbing"


def test_slug():
    assert slugify("Desk Organizer / Plant Pot Cover") == "desk-organizer-plant-pot-cover"


def test_part_kinds_and_status(test_library):
    hook = get_part(test_library, "test-hook")
    spacer = get_part(test_library, "test-spacer")
    locked = get_part(test_library, "locked-part")
    nofile = get_part(test_library, "no-file-yet")
    assert hook.kind == "mesh" and hook.printable and hook.base_size_mm == (30.0, 33.0, 60.0)
    assert spacer.kind == "scad" and [p.name for p in spacer.params] == ["outer_diameter", "inner_diameter", "height"]
    assert not locked.printable and "license" in locked.status
    assert nofile.kind == "missing" and not nofile.printable


def test_search(test_library):
    assert [p.part_id for p in search(test_library, "spacer")] == ["test-spacer"]
    assert all(p.category == "Plumbing" for p in search(test_library, category="Plumbing"))
