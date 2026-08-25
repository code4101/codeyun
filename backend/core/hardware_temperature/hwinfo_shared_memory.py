from __future__ import annotations

import ctypes
import math
import re
import struct
import sys
from collections.abc import Callable
from typing import Any


MAPPING_NAME = r"Global\HWiNFO_SENS_SM2"
HEADER = struct.Struct("<IIIqIIIIII")
ACTIVE_SIGNATURE = int.from_bytes(b"HWiS", "little")
MAX_MAPPING_SIZE = 32 * 1024 * 1024
MIN_VALID_TEMPERATURE_C = 0.0
MAX_VALID_TEMPERATURE_C = 150.0


class SharedMemoryUnavailable(OSError):
    pass


def _decode_c_string(value: bytes) -> str:
    raw = value.split(b"\0", 1)[0]
    for encoding in ("utf-8", "cp1252"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace").strip()


def _preferred_name(user_name: bytes, original_name: bytes) -> str:
    return _decode_c_string(user_name) or _decode_c_string(original_name)


def _classify_device(name: str) -> str:
    lowered = name.casefold()
    if "s.m.a.r.t." in lowered or any(token in lowered for token in ("nvme", " ssd", " hdd")):
        return "storage"
    if "gpu" in lowered or any(token in lowered for token in ("nvidia", "radeon", "arc a")):
        return "gpu"
    if "cpu" in lowered or any(token in lowered for token in ("ryzen", "intel core")):
        return "cpu"
    return "other"


def _drive_letters(name: str) -> list[str]:
    matches = re.findall(r"(?<![A-Z])([A-Z]):", name.upper())
    return list(dict.fromkeys(f"{letter}:" for letter in matches))


def _display_name(name: str) -> str:
    return re.sub(r"^S\.M\.A\.R\.T\.:\s*", "", name, flags=re.IGNORECASE).strip()


class HWiNFOSharedMemoryReader:
    def __init__(self, read_mapping: Callable[[], bytes] | None = None) -> None:
        self._read_mapping = read_mapping or self._read_windows_mapping

    def read_temperature_devices(self) -> list[dict[str, Any]]:
        return parse_temperature_devices(self._read_mapping())

    @staticmethod
    def _read_windows_mapping() -> bytes:
        if sys.platform != "win32":
            raise SharedMemoryUnavailable("HWiNFO shared memory is only available on Windows")

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.OpenFileMappingW.restype = ctypes.c_void_p
        kernel32.MapViewOfFile.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_size_t,
        ]
        kernel32.MapViewOfFile.restype = ctypes.c_void_p
        kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        handle = kernel32.OpenFileMappingW(0x0004, False, MAPPING_NAME)
        if not handle:
            raise SharedMemoryUnavailable(ctypes.get_last_error(), MAPPING_NAME)
        address = kernel32.MapViewOfFile(handle, 0x0004, 0, 0, 0)
        if not address:
            kernel32.CloseHandle(handle)
            raise SharedMemoryUnavailable(ctypes.get_last_error(), MAPPING_NAME)

        try:
            header_bytes = ctypes.string_at(address, HEADER.size)
            header = HEADER.unpack(header_bytes)
            sensor_offset, sensor_size, sensor_count = header[4:7]
            reading_offset, reading_size, reading_count = header[7:10]
            required_size = max(
                HEADER.size,
                sensor_offset + sensor_size * sensor_count,
                reading_offset + reading_size * reading_count,
            )
            if required_size <= 0 or required_size > MAX_MAPPING_SIZE:
                raise ValueError("Invalid HWiNFO shared memory size")
            return ctypes.string_at(address, required_size)
        finally:
            kernel32.UnmapViewOfFile(address)
            kernel32.CloseHandle(handle)


def parse_temperature_devices(buffer: bytes) -> list[dict[str, Any]]:
    if len(buffer) < HEADER.size:
        raise ValueError("HWiNFO shared memory header is incomplete")

    (
        signature,
        _version,
        _revision,
        _poll_time,
        sensor_offset,
        sensor_size,
        sensor_count,
        reading_offset,
        reading_size,
        reading_count,
    ) = HEADER.unpack_from(buffer)
    if signature != ACTIVE_SIGNATURE or sensor_size < 264 or reading_size < 316:
        raise ValueError("Unsupported HWiNFO shared memory layout")
    if sensor_count > 4096 or reading_count > 65536:
        raise ValueError("Invalid HWiNFO shared memory counts")
    if sensor_offset + sensor_size * sensor_count > len(buffer):
        raise ValueError("HWiNFO sensor section is incomplete")
    if reading_offset + reading_size * reading_count > len(buffer):
        raise ValueError("HWiNFO reading section is incomplete")

    sensors: list[dict[str, Any]] = []
    for index in range(sensor_count):
        offset = sensor_offset + index * sensor_size
        sensor_id, sensor_instance = struct.unpack_from("<II", buffer, offset)
        name = _preferred_name(buffer[offset + 136 : offset + 264], buffer[offset + 8 : offset + 136])
        sensors.append(
            {
                "id": f"{sensor_id}:{sensor_instance}",
                "name": name or f"Sensor {index}",
                "kind": _classify_device(name),
                "temperatures": [],
            }
        )

    value_offset = reading_size - 32
    for index in range(reading_count):
        offset = reading_offset + index * reading_size
        reading_type, sensor_index, reading_id = struct.unpack_from("<III", buffer, offset)
        if sensor_index >= len(sensors):
            continue
        label = _preferred_name(buffer[offset + 140 : offset + 268], buffer[offset + 12 : offset + 140])
        unit = _decode_c_string(buffer[offset + 268 : offset + 284]).replace(" ", "").casefold()
        if reading_type != 1 and unit not in {"°c", "c"}:
            continue
        value = struct.unpack_from("<d", buffer, offset + value_offset)[0]
        if (
            not math.isfinite(value)
            or not MIN_VALID_TEMPERATURE_C < value <= MAX_VALID_TEMPERATURE_C
        ):
            continue
        sensors[sensor_index]["temperatures"].append(
            {
                "id": str(reading_id),
                "name": label or "温度",
                "value": round(value, 1),
                "source": "HWiNFO",
            }
        )

    devices: list[dict[str, Any]] = []
    for sensor in sensors:
        temperatures = sensor["temperatures"]
        if not temperatures or sensor["kind"] == "other":
            continue
        name = sensor["name"]
        devices.append(
            {
                "id": f"{sensor['kind']}:{sensor['id']}",
                "kind": sensor["kind"],
                "name": _display_name(name),
                "drive_letters": _drive_letters(name),
                "temperature": max(item["value"] for item in temperatures),
                "sensors": temperatures,
            }
        )

    order = {"cpu": 0, "gpu": 1, "storage": 2}
    return sorted(devices, key=lambda item: (order.get(item["kind"], 9), item["name"].casefold()))
