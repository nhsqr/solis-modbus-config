from app.catalog import default_catalog
from app.explain import explanation, tooltip


def test_explanation_for_every_register():
    c = default_catalog()
    for r in c.registers:
        text = explanation(r, raw=1 if r.kind != "ascii" else "AB")
        assert str(r.address) in text
        assert r.name in text
        tip = tooltip(r)
        assert str(r.address) in tip


def test_key_examples_are_bulgarian():
    c = default_catalog()
    t = explanation(c.get(43110), raw=0x21)
    assert "Превключвател" in t or c.get(43110).name_bg in t
    assert "Битове:" in t
    t = explanation(c.get(43068))
    assert "падащо меню" in t or "избор" in t
