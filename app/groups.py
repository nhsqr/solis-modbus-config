"""Register groups (tabs) for settings (FC03/06) and monitoring (FC04).

Groups are assigned by ordered keyword rules on the English register name
(first match wins), then by address range. The catalog builder stores the
result in data/registers.json; the GUI only reads ``group``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Group:
    id: str
    title_en: str
    title_bg: str
    intro_bg: str


SETTING_GROUPS: List[Group] = [
    Group("system", "System, time & communication", "Система, време и комуникация",
          "Часовник на инвертора, Modbus адрес, скорост на RS485, рестарт, обновяване на "
          "софтуера и данни за логера. Командите за рестарт/фабрични настройки са опасни."),
    Group("grid_protection", "Grid standard & U/f protection", "Мрежов стандарт и защити U/f",
          "Национален стандарт (43068) и праговете/времената за изключване при пре- и "
          "понапрежение, пре- и почестота, ROCOF, условия за повторно включване и защити "
          "от утечка/изолация. Смяната на стандарта може да върне защитите към неговите стойности."),
    Group("vrt", "Voltage ride-through (LVRT/HVRT)", "Преминаване през пропад/скок (LVRT/HVRT)",
          "Поведение при кратки пропади (LVRT) и повишения (HVRT) на мрежовото напрежение: "
          "разрешаване, прагове, времена и подаване на реактивен ток."),
    Group("freq_response", "Frequency response P(f), FSM, FCAS", "Честотна реакция P(f), FSM, FCAS",
          "Намаляване/увеличаване на активната мощност при отклонение на честотата "
          "(over/under-frequency derating), FSM чувствителност, FCAS и честотни droop настройки."),
    Group("active_power", "Active power, export & ramps", "Активна мощност, износ и рампи",
          "Ограничение на активната мощност, на износа към мрежата (EPM/backflow), "
          "скорости на нарастване/намаляване (рампи), Volt-Watt и свързани режими."),
    Group("reactive_power", "Reactive power, PF, Q(U) & grid curves", "Реактивна мощност, PF, Q(U) и криви",
          "Фактор на мощността, фиксирана реактивна мощност, Volt-Var Q(U), Volt-Watt, P-PF криви и "
          "стандартните режими на работа (01/02/05/06 Working Mode) за мрежова поддръжка."),
    Group("battery", "Battery & BMS", "Батерия и BMS",
          "Модел на батерията, граници на SOC, токове и напрежения на заряд/разряд, "
          "оловно-киселинни параметри (изравняване, плаващ заряд), събуждане на батерията."),
    Group("storage_modes", "Storage modes", "Режими на съхранение",
          "Превключвател на режимите (самоконсумация, приоритет на износа, off-grid, резерв, "
          "peak-shaving), приоритети на изхода и заряда, пасивен режим."),
    Group("tou", "Time-of-use schedules", "Графици по време (TOU)",
          "Часови периоди за заряд/разряд от/към мрежата (Time-of-Use, Revenue V1/V2, седмичен режим): "
          "начален/краен час и минута, ток, мощност, напрежение и SOC за всеки период."),
    Group("remote", "Remote control & EMS dispatch", "Дистанционно управление и EMS",
          "Команди за дистанционно управление (мощност в AC порта, заряд/разряд на батерията) и "
          "EMS диспечеризация (44100+). Повечето изискват периодично повтаряне и не се пазят при "
          "изключване."),
    Group("backup", "Backup / EPS / off-grid", "Backup / EPS / автономен режим",
          "Backup (EPS) изход: разрешаване, напрежение, честота, време за превключване, "
          "SOC граници в автономен режим и претоварване."),
    Group("generator", "Generator", "Генератор",
          "Настройки на генератор: вход, мощност, стартиране/спиране по SOC или напрежение, "
          "ръчно управление и график на зареждане от генератор."),
    Group("parallel", "Parallel system", "Паралелна работа",
          "Работа на няколко инвертора в паралел: master/slave, брой устройства, фаза, "
          "синхронизация и общи ограничения."),
    Group("meter", "Meter / CT", "Електромер / токови трансформатори",
          "Тип и място на електромера или CT, коефициент на CT, посока и начин на комуникация."),
    Group("smart_load", "Smart port, AC coupling & loads", "Smart порт, AC coupling и товари",
          "Smart Load/Smart Port, AC coupling с мрежов PV инвертор, сухи контакти и "
          "управление на товари."),
    Group("diagnostics", "Tests, calibration & service", "Тестове, калибриране и сервиз",
          "Самопроверки (вкл. италиански CEI 0-21 тестове), IV крива, AFCI, калибриране на "
          "енергия и измерване, фабрични тестови режими. Само за сервиз!"),
    Group("other", "Other settings", "Други настройки",
          "Настройки, които не попадат в другите групи."),
]

MONITOR_GROUPS: List[Group] = [
    Group("info", "Device information", "Информация за устройството",
          "Модел, сериен номер, версии на софтуера и протокола."),
    Group("status", "Status, alarms & faults", "Статус, аларми и грешки",
          "Текущо състояние, кодове на аларми/грешки (декодирани по приложенията) и флагове."),
    Group("pv", "PV / DC input", "PV / DC вход", "Напрежения, токове и мощности на PV стринговете/MPPT."),
    Group("ac_grid", "AC grid & power", "AC мрежа и мощности",
          "Напрежения, токове, честота, активна/реактивна/пълна мощност и фактор на мощността."),
    Group("battery", "Battery & BMS", "Батерия и BMS",
          "Напрежение, ток, мощност, SOC/SOH и данни от BMS."),
    Group("meter", "Meter", "Електромер", "Стойности, измерени от външния електромер/CT."),
    Group("energy", "Energy counters", "Енергийни броячи", "Произведена, консумирана, внесена и изнесена енергия."),
    Group("backup_load", "Backup, load & generator", "Backup, товари и генератор",
          "Backup (EPS) изход, товари, Smart порт и генератор."),
    Group("grid_limits", "Grid protection: measured & limits", "Мрежови защити: измерено и граници",
          "Измерени времена на защитите (самотест) и допустимите горни/долни граници на "
          "параметрите на мрежовия стандарт."),
    Group("temps", "Temperatures & internals", "Температури и вътрешни величини",
          "Температури, напрежение на шината и други вътрешни стойности."),
    Group("system", "Parallel system, EMS & FCAS", "Паралелна система, EMS и FCAS",
          "Обобщени стойности за паралелна система, EMS диспечеризация и FCAS."),
    Group("other", "Other values", "Други стойности", "Стойности извън другите групи."),
]

_R = lambda *ws: re.compile("|".join(ws), re.I)  # noqa: E731

SETTING_RULES: Sequence[Tuple[str, "re.Pattern[str]"]] = [
    ("remote", _R(r"^remote", r"remote control", r"remote dispatch", r"\bEMS\b", r"dispatch")),
    ("generator", _R(r"generator", r"\bgen[_ ]", r"\bgen$")),
    ("tou", _R(r"\bTOU\b", r"time of use", r"time-of-use", r"charg\w* period", r"discharg\w* period", r"\bperiod\b",
               r"enhancement mode", r"daily same", r"weekday|monday|tuesday|wednesday|thursday|friday|saturday|sunday")),
    ("storage_modes", _R(r"energy storage control switch", r"storage control", r"peak", r"revenue",
                         r"self-use|self use|feed-in priority|feed in priority", r"work(ing)? mode set",
                         r"output priority", r"charging priority", r"supports loads", r"passive mode", r"\bECO\b",
                         r"energy saving", r"storage function control")),
    ("parallel", _R(r"parallel", r"master", r"\bslave\b(?! address)", r"connection phase", r"physical address id")),
    ("vrt", _R(r"\bVRT\b", r"LVRT", r"HVRT", r"ride[- ]?through", r"zero current", r"\bFRT\b",
               r"static voltage", r"voltage sudden change")),
    ("system", _R(r"TCP port", r"\bPOE\b", r"\bDNS\b", r"gateway", r"subnet", r"port number", r"bluetooth", r"ethernet",
                  r"networking", r"collector", r"\bMAC\b", r"indicator", r"\bLCD\b", r"light board", r"historical energy",
                  r"fault/(warning|alarm) record", r"solis hub", r"\bSN\b", r"serial number")),
    ("reactive_power", _R(r"working mode [VP]\d|working mode (leading|lagging|plock|b power|c power)",
                          r"\d\s*working mode", r"standard mode control", r"const voltage mode")),
    ("active_power", _R(r"feed[- ]?in power", r"feeder power", r"control signal", r"\bDRM\b", r"anti-reverse",
                        r"PCS limiting", r"G100", r"apparent current output", r"grid-side power import",
                        r"over-limit load disconnect")),
    ("smart_load", _R(r"smart ?load", r"smart ?port", r"ac coupl", r"dry contact", r"heat pump", r"backup2load",
                      r"load shedding(?!.*frequency)")),
    ("backup", _R(r"utility bypass", r"backup", r"\bEPS\b", r"off[- ]grid", r"\bUPS\b", r"overload")),
    ("meter", _R(r"meter", r"\bCT\b", r"CT ratio", r"current transformer")),
    ("diagnostics", _R(r"IV curve", r"self[- ]?test", r"self[- ]?check", r"\btest\b", r"calibrat", r"compensation",
                       r"sampling", r"open loop", r"AFCI", r"feedforward", r"one[- ]?key", r"factory",
                       r"italy", r"italian", r"clear energy", r"fan self")),
    ("grid_protection", _R(r"national standard", r"grid standard", r"accuracy", r"81[<>]", r"\b59\.", r"\b27\.",
                           r"OV-G", r"UN-G", r"over[- ‑]?voltage", r"under[- ‑]?voltage", r"over[- ‑]?frequency(?!.*(derat|load|power|slope))",
                           r"under[- ‑]?frequency(?!.*(derat|ramp|power|slope))", r"recovery", r"first power", r"start[- ‑]?up time",
                           r"reconnect", r"ROCOF", r"island", r"leakage", r"\biso\b", r"insulation", r"protection",
                           r"grid (upper|lower)", r"10[- ‑]?minute", r"phase polarity")),
    ("freq_response", _R(r"frequency derat", r"over[- ]?frequency", r"under[- ]?frequency", r"\bFSM\b", r"FCAS",
                         r"\bLSB\b|\bHSB\b|\bLDB\b|\bHDB\b", r"droop", r"P\(f\)", r"fstop|fstart|fpmax", r"frequency response")),
    ("reactive_power", _R(r"reactive", r"\bPF\b", r"power factor", r"volt[- ]?var", r"\bQ\(U\)", r"\bvar\b", r"\bQ\b",
                          r"P-PF", r"standard operating mode", r"standard working mode", r"vref", r"cos")),
    ("active_power", _R(r"active power", r"power limit", r"limit(ed)? power", r"export", r"backflow", r"reverse power",
                        r"\bEPM\b", r"fail ?safe", r"grid-fed", r"slope", r"wgra", r"ramp", r"power (up|down|reduce|control)",
                        r"volt[- ]?watt", r"rated power", r"\bP%|\bPmax", r"power on system grid", r"anti[- ]?reflux")),
    ("battery", _R(r"rated capacity", r"ambient temperature status", r"batter", r"\bSOC\b", r"charg", r"discharg", r"lead[- ‑]?acid", r"equaliz", r"equalis", r"float",
                   r"\bBMS\b", r"wake[- ‑]?up", r"healing", r"over-discharge")),
    ("system", _R(r"real[- ]?time clock", r"\byear\b|\bmonth\b|\bday\b|\bhour\b|\bminute\b|\bsecond\b", r"slave address",
                  r"baud", r"datalogger", r"restart", r"reset", r"buzzer", r"\bLED\b", r"upgrade", r"power off saving",
                  r"startup/shutdown", r"start(up)?/(shut|stop)", r"password", r"language", r"\bIP\b", r"wifi|wi-fi",
                  r"time zone|timezone", r"\bRTC\b", r"initial")),
]

MONITOR_RULES: Sequence[Tuple[str, "re.Pattern[str]"]] = [
    ("grid_limits", _R(r"upper limit", r"lower limit", r"measurement (time|frequency|voltage)", r"measurement .*value")),
    ("info", _R(r"^(year|month|day|hour|minute|second)$", r"system time", r"national standard", r"manufacturer")),
    ("system", _R(r"parallel system", r"system public", r"\bsystem\b.*(power|energy|soc)", r"remote", r"dispatch",
                  r"FCAS", r"module id", r"online status", r"each energy storage", r"available (charge|discharge|import|export)")),
    ("status", _R(r"status", r"fault", r"alarm", r"warning", r"error", r"\bflag", r"state", r"code\b", r"self[- ]?check",
                  r"protection", r"mode")),
    ("info", _R(r"serial number", r"\bSN\b", r"version", r"model", r"product", r"inverter type", r"protocol",
                r"rated", r"firmware", r"\bname\b", r"definition of solis", r"capacity")),
    ("energy", _R(r"energy", r"kwh", r"generation", r"consumption")),
    ("meter", _R(r"meter", r"\bCT\b")),
    ("battery", _R(r"batter", r"\bBMS\b", r"\bSOC\b", r"\bSOH\b", r"\bcell\b", r"charg")),
    ("pv", _R(r"\bPV\b", r"\bDC\s?\d", r"\bDC\b(?! bus)", r"MPPT", r"string")),
    ("backup_load", _R(r"backup", r"\bEPS\b", r"load", r"smart", r"generator", r"off-grid")),
    ("temps", _R(r"temperat", r"\bbus\b", r"leakage", r"insulation", r"\biso\b")),
    ("ac_grid", _R(r"grid", r"\bAC\b", r"phase", r"voltage", r"current", r"frequency", r"power", r"factor", r"inverter")),
]


def setting_group(name: str, address: int) -> str:
    for gid, rx in SETTING_RULES:
        if rx.search(name):
            return gid
    if 43000 <= address <= 43005:
        return "system"
    if 44100 <= address <= 44199:
        return "remote"
    return "other"


def monitor_group(name: str, address: int) -> str:
    if address == 35000 or 33000 <= address <= 33021:
        return "info"
    if 25000 <= address <= 25999:
        return "system"
    for gid, rx in MONITOR_RULES:
        if rx.search(name):
            return gid
    return "other"


def group_by_id(gid: str, monitor: bool = False) -> Optional[Group]:
    for g in (MONITOR_GROUPS if monitor else SETTING_GROUPS):
        if g.id == gid:
            return g
    return None
