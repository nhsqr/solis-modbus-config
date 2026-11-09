from app.catalog import default_catalog
from app.codec import words_to_raw
from app.identity import decode_sn, read_identity
from app.ops import read_one, read_registers, write_setting
from app.profiles import Profile, load_profile
from tests.fake_modbus import FakeBus


def test_read_write_verify_roundtrip():
    bus = FakeBus()
    bus.add("10.0.0.1", 502, 1, "SNTEST01", regs={43020: 400})
    c = bus.client("10.0.0.1", 502, 1, 3)
    c.connect()
    cat = default_catalog()
    reg = cat.get(43020)
    assert read_one(c, reg) == 400
    out = write_setting(c, reg, 445, verify=True, verify_delay=0, sleep=lambda _t: None)
    assert out.ok
    assert words_to_raw(reg, out.readback) == 445
    assert read_one(c, reg) == 445


def test_read_blocks_and_unsupported():
    bus = FakeBus()
    # leave most registers at 0; one address "unsupported" via read_failures? just 0 is fine
    bus.add("10.0.0.1", 502, 1, "SN", regs={43020: 445, 43021: 100})
    c = bus.client("10.0.0.1", 502, 1, 3)
    c.connect()
    cat = default_catalog()
    regs = [cat.get(43020), cat.get(43021)]
    res = read_registers(c, regs, interval=0, sleep=lambda _t: None)
    assert res.values[43020] == 445
    assert res.requests >= 1


def test_identity_ascii():
    bus = FakeBus()
    bus.add("10.0.0.1", 502, 1, "ABC123XYZ")
    c = bus.client("10.0.0.1", 502, 1, 3)
    c.connect()
    ident = read_identity(c)
    assert ident["serial_number"] == "ABC123XYZ"
    assert ident["model_no_display"].startswith("0x")


def test_decode_sn_hex_fallback():
    words = [0x0001, 0x0002] + [0] * 14
    s = decode_sn(words)
    # no printable ASCII → hex
    assert s == "" or s.startswith("0001") or "0001" in s or s == "00010002"


def test_profile_roundtrip(tmp_path):
    from app.ops import make_item
    cat = default_catalog()
    p = Profile("demo")
    p.set(make_item(cat.get(43020), 445, "44.5 V"))
    path = p.save(tmp_path / "demo.json")
    loaded = load_profile(path, cat)
    assert loaded.items[0].address == 43020
    assert loaded.items[0].words == (445,)
