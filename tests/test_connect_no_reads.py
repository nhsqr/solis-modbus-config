"""Connect must not issue any Modbus request (same contract as v1)."""
from app.modbus_client import connected_status
from tests.fake_modbus import FakeBus


def test_connect_does_not_read_or_write():
    bus = FakeBus()
    bus.add("192.168.85.104", 502, 1, "SN")
    c = bus.client("192.168.85.104", 502, 1, 3)
    c.connect()
    ops = [e[0] for e in bus.log]
    assert ops == ["connect"]
    assert "read" not in ops and "write" not in ops
    assert "Connected to" in connected_status(c)
    c.disconnect()


def test_real_client_connect_source_has_no_read():
    """Guard against regressions: the connect() body must not call read_*."""
    import inspect
    from app.modbus_client import SolisModbusClient
    src = inspect.getsource(SolisModbusClient.connect)
    assert "read_" not in src
    assert "write_" not in src
