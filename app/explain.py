"""Bulgarian explanation text for a register (tooltip + description panel)."""
from __future__ import annotations

import re
from typing import List, Optional

from app.catalog import Register
from app.codec import effective_scale, fmt_num, format_value
from app.groups import group_by_id

KIND_BG = {
    "value": "числова стойност",
    "enum": "избор от списък (падащо меню)",
    "bits": "битово поле (отметки за всеки бит)",
    "bytes": "два байта (старши/младши) в един регистър",
    "hex": "шестнадесетична стойност",
    "ascii": "текст (ASCII)",
}

# Frequent remark phrases -> Bulgarian notes (matched case-insensitively)
PHRASES = [
    (r"national standard parameter\s*table", "Обхватът и стойността по подразбиране се определят от избрания "
                                              "мрежов стандарт (таблицата с параметри на стандарта)."),
    (r"only (be )?supported for lead[- ‑]?acid|only supported for lead|lead[- ‑]?acid batter(y|ies) only",
     "Само за оловно-киселинни батерии."),
    (r"not (be )?retained|power off not saving|not saved after power|lost after power",
     "Не се запазва при изключване на захранването (временна команда)."),
    (r"cannot be enabled simultaneously|can ?not be (turned on|enabled) at the same time|mutually exclusive",
     "Някои функции не могат да са включени едновременно – вижте описанието от документа."),
    (r"first read the current value|read the current value of this register|reads the current value",
     "Битовете се записват чрез четене-промяна-запис: програмата първо чете регистъра и променя само "
     "избраните битове."),
    (r"effective only when|only effective when|takes effect only when|valid only when|only valid when",
     "Действа само при определено условие (вижте описанието от документа)."),
    (r"\bS5\b.*\bS6\b|\bS6\b.*\bS5\b|\b5G\b", "Обхватът/поведението се различава по серии (S5/S6/5G) – "
                                                      "вижте описанието от документа."),
    (r"only for (the )?(US|U\.S\.)|U\.S\. models|US models|UL1741|IEEE1547", "Касае американски модели/стандарти."),
    (r"CEI[ -]?0-?21|Italian|Italy", "Свързано с италианския стандарт CEI 0-21."),
    (r"Australia|AS4777|FCAS", "Свързано с австралийските изисквания (AS4777 / FCAS)."),
    (r"G100|G98|G99", "Свързано с британските изисквания (G98/G99/G100)."),
    (r"timeout|within 5 minutes|continuous \"?enable\"? command", "Командата изтича, ако не бъде "
                                                                   "повтаряна периодично (timeout)."),
    (r"parallel", "Има значение при паралелна работа на няколко инвертора."),
    (r"Appendix", "Стойностите са взети от приложение (Appendix) на протокола."),
]


def scale_text(reg: Register, accuracy_mode: Optional[int] = None) -> str:
    sc = effective_scale(reg, accuracy_mode)
    if sc == 1 and not reg.unit:
        return "1 raw = 1 (без мащаб)"
    return f"1 raw = {fmt_num(sc, sc)} {reg.unit}".strip()


def range_text(reg: Register) -> str:
    rg = reg.range
    if not rg:
        return ""
    if rg.get("min") is None:
        return f"по документа: {rg.get('text', '')}"
    lo, hi = rg["min"], rg["max"]
    basis = rg.get("basis")
    if basis == "raw":
        return (f"{lo:g} … {hi:g} raw  (= {fmt_num(lo * reg.scale, reg.scale)} … "
                f"{fmt_num(hi * reg.scale, reg.scale)} {reg.unit})  — „{rg.get('text', '')}“")
    if basis == "eng":
        raw_lo = round(lo / reg.scale) if reg.scale else lo
        raw_hi = round(hi / reg.scale) if reg.scale else hi
        return f"{lo:g} … {hi:g} {reg.unit} (raw {raw_lo} … {raw_hi})  — „{rg.get('text', '')}“"
    return f"„{rg.get('text', '')}“ (не е ясно дали е raw или в единици – проверете)"


def notes_bg(reg: Register) -> List[str]:
    text = f"{reg.name} {reg.description} {reg.description_v32}"
    out = []
    for rx, bg in PHRASES:
        if re.search(rx, text, re.I | re.S) and bg not in out:
            out.append(bg)
    if reg.volatile and PHRASES[2][1] not in out:
        out.append(PHRASES[2][1])
    return out


def source_text(reg: Register) -> str:
    parts = []
    for s in reg.sources:
        parts.append(f"{s['doc']} стр. {s['page']}")
    txt = "; ".join(parts)
    if reg.source == "V3.2":
        txt += " — само във Ver3.2 (неофициално копие, непотвърдено за S6/V3.5)"
    return txt


def explanation(reg: Register, *, raw=None, accuracy_mode: Optional[int] = None, full: bool = True) -> str:
    g = group_by_id(reg.group, monitor=not reg.writable)
    fc = "FC03 четене / FC06 запис" if reg.writable and reg.words == 1 else \
        "FC03 четене / FC16 запис" if reg.writable else "FC04 (само четене)"
    lines = [f"{reg.addr_text} · {reg.name}"]
    if reg.name_bg:
        lines.append(reg.name_bg)
    lines.append("")
    if g:
        lines.append(f"Група: {g.title_bg}")
        if full and g.intro_bg:
            lines.append(f"  {g.intro_bg}")
    lines.append(f"Тип: {reg.type}, {reg.words} регистър(а), {fc}")
    lines.append(f"Вид: {KIND_BG.get(reg.kind, reg.kind)}")
    if reg.kind in ("value", "hex"):
        lines.append(f"Мащаб: {scale_text(reg, accuracy_mode)}")
        if reg.unit:
            lines.append(f"Единица: {reg.unit}")
    rt = range_text(reg)
    if rt:
        lines.append(f"Обхват: {rt}")
    if reg.default:
        lines.append(f"По подразбиране (по документа): {reg.default.get('text', '')}")
    if reg.address in range(43090, 43106):
        lines.append("Мащабът зависи от 43081 (точност): 1 → 0.1 V / 10 ms / 0.01 Hz (както във V3.5), "
                     "0 → 1 V / 100 ms / 0.1 Hz. Прочетете 43081 първо, ако инверторът е с Ver3.2 софтуер.")
    if rt and reg.range and reg.range.get("min") is not None:
        lines.append("  (Обхватът се проверява само като предупреждение – твърдата граница е типът на регистъра.)")
    if raw is not None:
        lines.append(f"Текуща стойност: {format_value(reg, raw, accuracy_mode)}  (raw {raw}"
                     + (f" = 0x{int(raw) & 0xFFFFFFFF:X}" if not isinstance(raw, str) else "") + ")")
    if reg.kind == "enum" and reg.options:
        lines.append(f"Възможни стойности ({len(reg.options)}): " + (
            "; ".join(f"{k}={v}" for k, v in list(reg.options.items())[:12])
            + (" …" if len(reg.options) > 12 else "")))
    if reg.kind == "bytes" and (reg.hi_options or reg.lo_options):
        lines.append("Старши байт: " + "; ".join(f"{k}={v}" for k, v in list((reg.hi_options or {}).items())[:8]))
        lines.append("Младши байт: " + "; ".join(f"{k}={v}" for k, v in list((reg.lo_options or {}).items())[:8]))
    if reg.kind == "bits" and reg.bits:
        lines.append("Битове:")
        for b in reg.bits:
            if b.reserved:
                continue
            st = ", ".join(f"{k}={v}" for k, v in b.states.items())
            lines.append(f"  • {b.label()}" + (f"  ({st})" if st else ""))
    if reg.appendix:
        lines.append(f"Приложение: {reg.appendix}")
    if reg.danger:
        lines.append("⚠ Сервизна/командна настройка – записът изпълнява действие. Използвайте внимателно.")
    for n in notes_bg(reg):
        lines.append(f"• {n}")
    lines.append(f"Източник: {source_text(reg)}")
    for n in reg.notes:
        lines.append(f"Бележка при извличане: {n}")
    if full and reg.description:
        lines.append("")
        lines.append("Описание от документа (EN):")
        lines.append(reg.description)
    if full and reg.description_v32:
        lines.append("")
        lines.append("Описание във Ver3.2 (EN):")
        lines.append(reg.description_v32)
    return "\n".join(lines)


def tooltip(reg: Register, raw=None, accuracy_mode: Optional[int] = None) -> str:
    """Shorter text for hover tooltips (doc description cut to ~600 chars)."""
    t = explanation(reg, raw=raw, accuracy_mode=accuracy_mode, full=False)
    if reg.description:
        d = re.sub(r"\s+", " ", reg.description)
        t += "\n\nДокумент: " + (d[:600] + ("…" if len(d) > 600 else ""))
    return t
