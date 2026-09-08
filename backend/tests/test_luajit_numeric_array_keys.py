"""Byte-level LuaJIT table regression; no game or GUI simulation."""
import struct
from backend.core.fanxiu.instrumentation.runtime_memory import LuaJitReader


class TableBytes:
    def __init__(self):
        header = bytearray(64)
        struct.pack_into('<Q', header, 16, 0x2000)
        struct.pack_into('<Q', header, 40, 0x3000)
        struct.pack_into('<II', header, 48, 3, 0)
        self.blocks = {0x1000: bytes(header), 0x2000: struct.pack('<ddd', 10, 20, 30),
                       0x3000: struct.pack('<ddQ', 40, 3, 0)}

    def read(self, address, size):
        for start, data in self.blocks.items():
            if start <= address and address + size <= start + len(data):
                return data[address-start:address-start+size]
        raise AssertionError('Out-of-bounds read')

    def readable_region(self, address, size):
        return any(start <= address and address+size <= start+len(data)
                   for start, data in self.blocks.items())


def test_key_zero_first_last_and_hash_boundary_are_not_shifted():
    reader = LuaJitReader(TableBytes())
    assert reader.numeric_fields(0x1000, frozenset({0,1,2,3})) == {0:10,1:20,2:30,3:40}


def test_missing_key_is_not_previous_array_value():
    reader = LuaJitReader(TableBytes())
    assert reader.numeric_fields(0x1000, frozenset({4})) == {}
