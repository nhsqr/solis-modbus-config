import threading
from pathlib import Path

from app.batch import (
    DRY_RUN_OK, ERROR, MISMATCH, OK, BatchOptions, BatchRunner, ReadOnlyClient,
    WriteForbidden, parse_timeout, validate_options, write_report_csv,
)
from app.devices import DeviceTarget
from app.writer import WriteItem
from tests.fake_modbus import FakeBus

A, B = 43090, 43091


def _items():
    return [WriteItem(A, "OV-G-V01", (230,), "230 V"), WriteItem(B, "OV-G-V01-T", (50,), "500 ms")]


def _run(bus, devices, items=None, **opt):
    return BatchRunner(
        devices, items or _items(), options=BatchOptions(retry_delay=0, **opt),
        client_factory=bus.client,
    ).run()


def test_parallel_ips_serial_ids():
    bus = FakeBus(latency=0.02)
    bus.add("10.0.0.1", 502, 1, "AAA")
    bus.add("10.0.0.1", 502, 2, "BBB")
    bus.add("10.0.0.2", 502, 1, "CCC")
    devs = [DeviceTarget("10.0.0.1", 502, 1), DeviceTarget("10.0.0.1", 502, 2),
            DeviceTarget("10.0.0.2", 502, 1)]
    s = _run(bus, devs, parallel=8, retries=0)
    assert s.count(OK) == 3
    assert bus.max_open_sessions == 2  # two IPs
    assert bus.max_per_endpoint[("10.0.0.1", 502)] == 1  # never two sessions to same IP


def test_verify_only_never_writes():
    bus = FakeBus()
    bus.add("10.0.0.1", 502, 1, "AAA", regs={A: 230, B: 50})
    s = _run(bus, [DeviceTarget("10.0.0.1", 502, 1)], verify_only=True, retries=0)
    assert s.count(OK) == 1
    assert not any(e[0] == "write" for e in bus.log)
    bus.regs[("10.0.0.1", 502, 1)][A] = 0
    s = _run(bus, [DeviceTarget("10.0.0.1", 502, 1)], verify_only=True, retries=0)
    assert s.count(MISMATCH) == 1
    assert bus.regs[("10.0.0.1", 502, 1)][A] == 0


def test_readonly_client_blocks_writes():
    bus = FakeBus()
    bus.add("10.0.0.1", 502, 1, "AAA")
    c = ReadOnlyClient(bus.client("10.0.0.1", 502, 1, 3))
    c.connect()
    try:
        c.write_single(A, 1)
        assert False
    except WriteForbidden:
        pass
    try:
        c.write_multiple(A, [1, 2])
        assert False
    except WriteForbidden:
        pass


def test_dry_run_reports_would_change():
    bus = FakeBus()
    bus.add("10.0.0.1", 502, 1, "AAA", regs={A: 100, B: 50})
    s = _run(bus, [DeviceTarget("10.0.0.1", 502, 1)], dry_run=True, retries=0)
    assert s.count(DRY_RUN_OK) == 1
    assert "would change" in s.results[0].message
    assert not any(e[0] == "write" for e in bus.log)
    assert bus.regs[("10.0.0.1", 502, 1)][A] == 100


def test_retries_then_ok():
    bus = FakeBus()
    bus.add("10.0.0.1", 502, 1, "AAA")
    bus.write_failures[("10.0.0.1", 502, 1, A)] = 2  # fail twice, succeed 3rd
    s = _run(bus, [DeviceTarget("10.0.0.1", 502, 1)], retries=2)
    assert s.count(OK) == 1
    assert s.results[0].write_attempts[A] == 3


def test_verify_rewrites_on_mismatch(tmp_path):
    bus = FakeBus()
    bus.add("10.0.0.1", 502, 1, "AAA")
    bus.ignore_write_times[("10.0.0.1", 502, 1, A)] = 1  # first write forgotten
    s = _run(bus, [DeviceTarget("10.0.0.1", 502, 1)], retries=2)
    assert s.count(OK) == 1
    p = write_report_csv(tmp_path / "r.csv", s, items=_items())
    text = Path(p).read_text(encoding="utf-8")
    assert "10.0.0.1" in text


def test_connect_failure():
    bus = FakeBus()
    bus.add("10.0.0.1", 502, 1, "AAA")
    bus.unreachable_hosts.add(("10.0.0.9", 502))
    s = _run(bus, [DeviceTarget("10.0.0.9", 502, 1)], retries=1)
    assert s.count(ERROR) == 1
    assert s.results[0].connect_attempts == 2


def test_u32_write_uses_two_words():
    bus = FakeBus()
    bus.add("10.0.0.1", 502, 1, "AAA")
    items = [WriteItem(43056, "U32", (0x0001, 0x0002), "")]
    s = _run(bus, [DeviceTarget("10.0.0.1", 502, 1)], items=items, retries=0)
    assert s.count(OK) == 1
    regs = bus.regs[("10.0.0.1", 502, 1)]
    assert regs[43056] == 1 and regs[43057] == 2
    writes = [e for e in bus.log if e[0] == "write"]
    assert writes[0][5] == [1, 2]  # one FC16, not two FC06


def test_validate_and_timeout():
    assert validate_options("8", "2", "3") == (8, 2, 3.0)
    assert isinstance(validate_options("0", "2", "3"), str)
    assert parse_timeout("1,5") == 1.5
    try:
        parse_timeout("0.1")
        assert False
    except ValueError:
        pass
