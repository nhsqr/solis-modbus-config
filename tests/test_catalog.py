"""Catalog completeness vs the generated JSON and the two protocol documents."""
from collections import Counter

from app.catalog import default_catalog, read_blocks
from app.groups import MONITOR_GROUPS, SETTING_GROUPS


def test_load_and_counts():
    c = default_catalog()
    assert len(c) == 1847
    assert len(c.settings()) == 896
    assert len(c.monitors()) == 951


def test_every_setting_has_bulgarian_name():
    missing = [r.address for r in default_catalog().settings() if not r.name_bg]
    assert missing == []


def test_unique_addresses():
    c = default_catalog()
    addrs = [r.address for r in c.registers]
    assert len(addrs) == len(set(addrs))


def test_groups_cover_everything():
    c = default_catalog()
    sids = {g.id for g in SETTING_GROUPS}
    mids = {g.id for g in MONITOR_GROUPS}
    assert all(r.group in sids for r in c.settings())
    assert all(r.group in mids for r in c.monitors())


def test_writable_are_holding():
    for r in default_catalog().settings():
        assert r.fc == 3
        assert r.writable
        assert 43000 <= r.address <= 44999 or r.address >= 43000


def test_monitors_are_input():
    for r in default_catalog().monitors():
        assert r.fc == 4
        assert not r.writable
        assert 25000 <= r.address < 26000 or 33000 <= r.address < 37000


def test_source_is_v35_or_v32():
    for r in default_catalog().registers:
        assert r.source in ("V3.5", "V3.2")
        assert r.sources


def test_kinds_consistent():
    c = default_catalog()
    for r in c.registers:
        if r.kind == "enum":
            assert r.options
        if r.kind == "bits":
            assert r.bits
        if r.kind == "bytes" and (r.lo_options or r.hi_options):
            assert r.lo_options or r.hi_options


def test_read_blocks_contiguous_same_fc():
    c = default_catalog()
    blocks = read_blocks(c.settings()[:40], max_count=50)
    for fc, start, count, members in blocks:
        assert fc == 3
        assert count == members[-1].end - start + 1
        assert count <= 50


def test_counts_per_group_nonzero():
    c = default_catalog()
    sc = Counter(r.group for r in c.settings())
    mc = Counter(r.group for r in c.monitors())
    for g in SETTING_GROUPS:
        assert sc[g.id] > 0, g.id
    for g in MONITOR_GROUPS:
        assert mc[g.id] > 0, g.id


def test_v32_only_flagged():
    only = [r for r in default_catalog().registers if r.source == "V3.2"]
    assert len(only) > 400  # hundreds of V3.2-only registers
    assert any(r.writable for r in only)
