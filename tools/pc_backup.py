#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""Read-only FM1 ROM backup using the reference jl-uboot-tool RAM loader.

Requires administrator access on Windows. Never erases or programs flash.
Keeps the RAM loader alive after backing up until a stop file is created.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
from pathlib import Path
import sys
import time


class SPTD(C.Structure):
    _fields_ = [('Length', W.WORD), ('Status', W.BYTE), ('PathId', W.BYTE),
                ('TargetId', W.BYTE), ('Lun', W.BYTE), ('CdbLength', W.BYTE),
                ('SenseLength', W.BYTE), ('Direction', W.BYTE),
                ('DataLength', W.DWORD), ('Timeout', W.DWORD),
                ('Data', C.c_void_p), ('SenseOffset', W.DWORD),
                ('Cdb', W.BYTE * 16)]


class WindowsScsi:
    def __init__(self, path):
        self.api = C.WinDLL('kernel32', use_last_error=True)
        self.api.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD,
            C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
        self.api.CreateFileW.restype = W.HANDLE
        self.api.DeviceIoControl.argtypes = [W.HANDLE, W.DWORD, C.c_void_p,
            W.DWORD, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
        self.api.DeviceIoControl.restype = W.BOOL
        self.api.CloseHandle.argtypes = [W.HANDLE]
        self.handle = self.api.CreateFileW(path, 0xC0000000, 3, None, 3, 0, None)
        if self.handle == W.HANDLE(-1).value:
            raise C.WinError(C.get_last_error())

    def validate_command(self, cdb, data_out):
        # Deliberately exclude all persistent write/erase/chip-key commands.
        if cdb[0] != 0x12 and int.from_bytes(cdb[:2], 'big') not in {
                0xFB06, 0xFB08, 0xFC09, 0xFC0A, 0xFD05}:
            raise ValueError('Command outside read-only/RAM-loader allowlist')

    def execute(self, cdb, data_out, data_in):
        self.validate_command(cdb, data_out)
        if len(cdb) > 16 or (data_out is not None and data_in is not None):
            raise ValueError('Invalid transfer')
        size = len(data_out if data_out is not None else data_in)
        buf = C.create_string_buffer(size)
        if data_out is not None:
            C.memmove(buf, bytes(data_out), size)
        req = SPTD()
        req.Length = C.sizeof(req)
        req.CdbLength = len(cdb)
        req.Direction = 0 if data_out is not None else 1
        req.DataLength = size
        req.Timeout = 5
        req.Data = C.addressof(buf)
        req.Cdb[:len(cdb)] = cdb
        returned = W.DWORD()
        for attempt in range(3):
            if self.api.DeviceIoControl(self.handle, 0x4D014, C.byref(req),
                    C.sizeof(req), C.byref(req), C.sizeof(req), C.byref(returned), None):
                break
            error = C.get_last_error()
            # Retry only idempotent flash reads; never RAM writes or jumps.
            if int.from_bytes(cdb[:2], 'big') != 0xFD05 or attempt == 2:
                raise C.WinError(error)
            print(f'Read retry {attempt + 1}: {bytes(cdb).hex()} error={error}', flush=True)
            req.DataLength = size
            time.sleep(0.05)
        if req.Status or req.DataLength != size:
            raise RuntimeError(f'SCSI status={req.Status}, length={req.DataLength}/{size}')
        if data_in is not None:
            data_in[:] = buf.raw
        return 0

    def close(self):
        self.api.CloseHandle(self.handle)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--device', required=True)
    ap.add_argument('--reference', type=Path, required=True)
    ap.add_argument('--dependencies', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    log = (args.output / 'backup.log').open('a', encoding='utf8', buffering=1)
    sys.stdout = sys.stderr = log
    sys.path[:0] = [str(args.reference), str(args.dependencies)]
    from jltech.uboot import JL_UBOOT, JL_LoaderV2
    dev = WindowsScsi(args.device)
    try:
        inquiry = bytearray(36)
        dev.execute(bytes.fromhex('120000002400'), None, inquiry)
        print('INQUIRY', bytes(inquiry), flush=True)
        if inquiry[8:16].strip() != b'WL82' or inquiry[16:32].strip() != b'UBOOT1.00':
            raise RuntimeError('Refusing non-FM1 ROM device')
        blob = (args.reference / 'data/loaderblobs/usb/wl82loader.bin').read_bytes()
        if len(blob) != 24064:
            raise RuntimeError('Unexpected RAM loader size')
        print('RAM loader SHA256', hashlib.sha256(blob).hexdigest(), flush=True)
        rom = JL_UBOOT(dev)
        for off in range(0, len(blob), 512):
            rom.mem_write(0x1C02000 + off, blob[off:off + 512])
        print('RAM loader uploaded; starting', flush=True)
        rom.mem_jump(0x1C02000, 1)
        loader = JL_LoaderV2(dev)
        key, flash = loader.chip_key(), loader.online_device()
        print(f'KEY {key:04X} FLASH {flash}', flush=True)
        if key != 0x980F or flash != {'type': 3, 'id': 0x856014}:
            raise RuntimeError('Unexpected chip or flash')
        first = None
        for attempt in (1, 2):
            started = time.monotonic()
            data = bytearray()
            for off in range(0, 0x100000, 512):
                if off % 0x4000 == 0:
                    loader.online_device()
                data.extend(loader.flash_read(off, 512))
                if off % 0x10000 == 0:
                    print(f'BACKUP {attempt}: {off:#x} t={time.monotonic() - started:.3f}s', flush=True)
            path = args.output / f'flash-{attempt}.bin'
            with path.open('xb') as out:
                out.write(data)
            print(path.name, hashlib.sha256(data).hexdigest(), flush=True)
            if first is None:
                first = data
            elif data != first:
                raise RuntimeError('Full flash backups differ')
        print('BACKUPS VERIFIED. No flash writes. Keeping loader alive.', flush=True)
        while not (args.output / 'stop').exists():
            loader.online_device()
            time.sleep(0.5)
    finally:
        dev.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
