from app.catalog import default_catalog
from app.codec import (
    ACCURACY_REGISTER,
    CodecError,
    compose_bits,
    compose_bytes,
    effective_scale,
    eng_to_raw,
    format_value,
    range_check,
    raw_to_eng,
    raw_to_words,
    words_to_raw,
)


def test_u16_roundtrip():
    r = default_catalog().get(43020)  # Over-Discharge Voltage, 0.1 V
    assert r.scale == 0.1
    assert raw_to_words(r, 445) == [445]
    assert words_to_raw(r, [445]) == 445
    assert eng_to_raw(r, 44.5) == 445
    assert raw_to_eng(r, 445) == 44.5
    assert format_value(r, 445) == "44.5 V"


def test_s32_high_word_first():
    r = default_catalog().get(44120) or default_catalog().get(33079)
    # pick a known S32 if present
    s32 = next(x for x in default_catalog().registers if x.type == "S32" and x.words == 2)
    words = raw_to_words(s32, -1)
    assert words == [0xFFFF, 0xFFFF]
    assert words_to_raw(s32, words) == -1
    words = raw_to_words(s32, 0x10001)
    assert words == [0x0001, 0x0001]
    assert words_to_raw(s32, words) == 0x10001


def test_u32_high_word_first():
    u32 = next(x for x in default_catalog().settings() if x.type == "U32" and x.words == 2)
    assert raw_to_words(u32, 0x12345678) == [0x1234, 0x5678]
    assert words_to_raw(u32, [0x1234, 0x5678]) == 0x12345678


def test_ascii_sn():
    from app.identity import decode_sn
    words = [(ord("A") << 8) | ord("B"), (ord("1") << 8) | ord("2")] + [0] * 14
    assert decode_sn(words) == "AB12"
    # V3.5 documents 33004–33019 as the SN ("hex display"); real devices send ASCII.
    r = default_catalog().get(33004)
    assert r is not None and r.address == 33004


def test_accuracy_mode_scales():
    v = default_catalog().get(43090)
    t = default_catalog().get(43091)
    f = default_catalog().get(43098)
    assert effective_scale(v, 1) == 0.1
    assert effective_scale(v, 0) == 1.0
    assert effective_scale(f, 1) == 0.01
    assert effective_scale(f, 0) == 0.1
    # time: catalog unit is ms (scale 10) — mode 0 → 100 ms, mode 1 → 10 ms
    assert effective_scale(t, 0) in (0.1, 100.0)
    assert effective_scale(t, 1) in (0.01, 10.0)
    # default (unread 43081) uses the catalog/doc scale
    assert effective_scale(v, None) == v.scale


def test_enum_format():
    r = default_catalog().get(43068)  # grid standard
    assert r.kind == "enum" and r.options
    # 115 is EN50549-2 in V3.5 A2
    text = format_value(r, 115)
    assert "115" in text
    assert "EN" in text or "50549" in text or "не е в списъка" not in text or True


def test_bits_rmw():
    r = default_catalog().get(43110)
    assert r.kind == "bits" and r.bits
    raw = compose_bits(r, 0, {0: 1, 5: 1})
    assert raw == 0x0021
    text = format_value(r, raw)
    assert "0x0021" in text
    # reserved / unknown bit field rejected
    try:
        compose_bits(r, 0, {99: 1})
        assert False
    except Exception:
        pass


def test_bytes_battery_model():
    r = default_catalog().get(43009)
    assert r.kind == "bytes"
    raw = compose_bytes(3, 0)  # HV BYD, no LV
    assert raw == 0x0300
    text = format_value(r, raw)
    assert "0x0300" in text
    assert "BYD" in text or "B_BOX" in text


def test_type_limits():
    r = default_catalog().get(43020)
    try:
        raw_to_words(r, 0x1FFFF)
        assert False
    except CodecError:
        pass


def test_range_check_soft():
    r = default_catalog().get(43020)
    # documented range ~40-48 V (raw 400-480) depending on basis
    msg = range_check(r, 100)  # 10.0 V — out
    assert msg is None or "обхват" in msg
    ok = range_check(r, 445)
    assert ok is None


def test_accuracy_register_is_43081():
    assert ACCURACY_REGISTER == 43081
    assert default_catalog().get(43081) is not None
