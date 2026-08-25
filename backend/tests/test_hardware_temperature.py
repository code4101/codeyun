from __future__ import annotations

import struct

from backend.core.hardware_temperature import service
from backend.core.hardware_temperature.local_collector import LocalCollectorUnavailable
from backend.core.hardware_temperature.hwinfo_shared_memory import (
    ACTIVE_SIGNATURE,
    HEADER,
    parse_temperature_devices,
)


def _c_string(value: str, size: int) -> bytes:
    encoded = value.encode("utf-8")[: size - 1]
    return encoded + bytes(size - len(encoded))


def _shared_memory_sample() -> bytes:
    sensor_size = 264
    reading_size = 316
    sensors = [
        (10, 0, "CPU [#0]: AMD Ryzen 9"),
        (20, 0, "S.M.A.R.T.: Samsung SSD 990 PRO 4TB [F:]"),
    ]
    readings = [
        (0, 101, "CPU Package", 65.5),
        (1, 201, "Drive Temperature", 48.0),
        (1, 202, "Drive Temperature 2", 48.0),
        (1, 203, "Drive Temperature 3", 60.0),
        (1, 204, "Missing Temperature", 0.0),
        (1, 205, "Invalid Temperature", -1.0),
        (1, 206, "Broken Temperature", 999.0),
        (1, 207, "NaN Temperature", float("nan")),
    ]
    sensor_offset = HEADER.size
    reading_offset = sensor_offset + sensor_size * len(sensors)
    payload = bytearray(reading_offset + reading_size * len(readings))
    HEADER.pack_into(
        payload,
        0,
        ACTIVE_SIGNATURE,
        2,
        0,
        0,
        sensor_offset,
        sensor_size,
        len(sensors),
        reading_offset,
        reading_size,
        len(readings),
    )
    for index, (sensor_id, instance, name) in enumerate(sensors):
        offset = sensor_offset + index * sensor_size
        struct.pack_into("<II", payload, offset, sensor_id, instance)
        payload[offset + 8 : offset + 136] = _c_string(name, 128)
    for index, (sensor_index, reading_id, label, value) in enumerate(readings):
        offset = reading_offset + index * reading_size
        struct.pack_into("<III", payload, offset, 1, sensor_index, reading_id)
        payload[offset + 12 : offset + 140] = _c_string(label, 128)
        payload[offset + 268 : offset + 284] = _c_string("°C", 16)
        struct.pack_into("<dddd", payload, offset + reading_size - 32, value, value, value, value)
    return bytes(payload)


def test_parse_hwinfo_temperature_devices() -> None:
    devices = parse_temperature_devices(_shared_memory_sample())

    assert [device["kind"] for device in devices] == ["cpu", "storage"]
    assert devices[0]["temperature"] == 65.5
    assert devices[1]["drive_letters"] == ["F:"]
    assert devices[1]["temperature"] == 60.0
    assert [sensor["value"] for sensor in devices[1]["sensors"]] == [48.0, 48.0, 60.0]
    assert [sensor["name"] for sensor in devices[1]["sensors"]] == [
        "Drive Temperature",
        "Drive Temperature 2",
        "Drive Temperature 3",
    ]


def test_temperature_snapshot_reports_unavailable_local_collector(monkeypatch) -> None:
    def unavailable_collector():
        raise LocalCollectorUnavailable("采集器不可用")

    monkeypatch.setattr(service, "read_local_temperature_devices", unavailable_collector)

    payload = service.get_temperature_snapshot()

    assert payload["status"] == "unavailable"
    assert payload["source"] == "CodeYun"
    assert payload["devices"] == []
    assert payload["message"] == "采集器不可用"


def test_temperature_snapshot_returns_local_devices(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "read_local_temperature_devices",
        lambda: {
            "status": "ok",
            "observed_at": "2026-08-20T12:00:00+08:00",
            "elevated": True,
            "devices": [
                {
                    "id": "storage:0",
                    "kind": "storage",
                    "name": "NVMe",
                    "drive_letters": ["C:"],
                    "temperature": 60.0,
                    "sensors": [
                        {"id": "a", "name": "综合", "value": 48.0, "source": "smartctl"},
                        {"id": "b", "name": "传感器 2", "value": 60.0, "source": "smartctl"},
                    ],
                }
            ],
        },
    )

    payload = service.get_temperature_snapshot()

    assert payload["status"] == "ok"
    assert payload["source"] == "CodeYun"
    assert payload["elevated"] is True
    assert payload["devices"][0]["temperature"] == 60.0
    assert [sensor["value"] for sensor in payload["devices"][0]["sensors"]] == [48.0, 60.0]
