#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""Guarded restore of compatible FM1 firmware packages; dry-run by default."""
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
from pathlib import Path
import sys
import time
from pc_backup import WindowsScsi
from recovery_plan import extract_firmware, make_plan, APP_START, APP_END, SECTOR


class GuardedScsi(WindowsScsi):
    def validate_command(self, cdb, data_out):
        cmd = int.from_bytes(cdb[:2], 'big')
        if cmd in (0xFB01, 0xFB04):
            addr = int.from_bytes(cdb[2:6], 'big')
            if cmd == 0xFB01:
                if not APP_START <= addr < APP_END or addr % SECTOR:
                    raise ValueError('Erase outside application sectors')
            else:
                size = int.from_bytes(cdb[6:8], 'big')
                if size != 512 or data_out is None or len(data_out) != size:
                    raise ValueError('Invalid flash write size')
                if addr % 512 or addr < APP_START or addr + size > APP_END:
                    raise ValueError('Write outside application sectors')
            return
        if cmd == 0xFC0C:
            if bytes(cdb[2:6]) != b'\x00\x00\x00\x01':
                raise ValueError('Invalid restart argument')
            return
        # Restoration must not replace the already running RAM loader.
        if cmd in (0xFB06, 0xFB08):
            raise ValueError('RAM-loader replacement forbidden during restore')
        super().validate_command(cdb, data_out)


def read_all(loader):
    data = bytearray()
    for off in range(0, 0x100000, 512):
        if off % 0x4000 == 0:
            loader.online_device()
        data.extend(loader.flash_read(off, 512))
    return bytes(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--package', type=Path, required=True)
    ap.add_argument('--backup', type=Path, required=True)
    ap.add_argument('--reference', type=Path, required=True)
    ap.add_argument('--dependencies', type=Path, required=True)
    ap.add_argument('--device', required=True)
    ap.add_argument('--keeper-pid', type=int)
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()
    log = (args.backup / 'restore.log').open('a', encoding='utf8', buffering=1)
    sys.stdout = sys.stderr = log
    first = (args.backup / 'flash-1.bin').read_bytes()
    second = (args.backup / 'flash-2.bin').read_bytes()
    package = args.package.read_bytes()
    image = extract_firmware(package)
    print('PACKAGE SHA256', hashlib.sha256(package).hexdigest(), flush=True)
    expected, sectors = make_plan(first, second, image)
    print(f'PLAN: {len(sectors)} changed sectors; bootloader/device data preserved', flush=True)
    print('EXPECTED SHA256', hashlib.sha256(expected).hexdigest(), flush=True)
    if not args.write:
        print('DRY RUN; no device commands', flush=True)
        return
    if args.keeper_pid is None:
        raise ValueError('Require the active backup keeper process for handoff')
    sys.path[:0] = [str(args.reference), str(args.dependencies)]
    from jltech.uboot import JL_LoaderV2
    dev = GuardedScsi(args.device)
    try:
        inquiry = bytearray(36)
        dev.execute(bytes.fromhex('120000002400'), None, inquiry)
        if inquiry[8:16].strip() != b'WL82' or inquiry[16:32].strip() != b'UBOOT1.00':
            raise RuntimeError('Incorrect recovery device')
        loader = JL_LoaderV2(dev)
        if loader.chip_key() != 0x980F or loader.online_device() != {'type': 3, 'id': 0x856014}:
            raise RuntimeError('Incorrect chip or flash')
        api = C.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        api.OpenProcess.restype = W.HANDLE
        api.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        api.CloseHandle.argtypes = [W.HANDLE]
        keeper = api.OpenProcess(0x100000, False, args.keeper_pid)
        if not keeper:
            raise C.WinError(C.get_last_error())
        try:
            (args.backup / 'stop').touch(exist_ok=False)
            if api.WaitForSingleObject(keeper, 2000) != 0:
                raise RuntimeError('Backup keeper did not exit')
        finally:
            api.CloseHandle(keeper)
        print('Keeper handed off; checking fresh flash against backups', flush=True)
        if read_all(loader) != first:
            raise RuntimeError('Fresh full flash differs from verified backup; no writes')
        with (args.backup / 'expected.bin').open('xb') as out:
            out.write(expected)
        for index, addr in enumerate(sectors):
            loader.online_device()
            print(f'ERASE {index + 1}/{len(sectors)} {addr:#x}', flush=True)
            loader.flash_erase_sector(addr)
            for off in range(addr, addr + SECTOR, 512):
                loader.flash_write(off, expected[off:off + 512])
            for off in range(addr, addr + SECTOR, 512):
                if loader.flash_read(off, 512) != expected[off:off + 512]:
                    raise RuntimeError(f'Sector verification failed at {off:#x}')
            print(f'VERIFIED {addr:#x}', flush=True)
        final = read_all(loader)
        with (args.backup / 'restored.bin').open('xb') as out:
            out.write(final)
        if final != expected:
            raise RuntimeError('Full post-write verification failed')
        print('FULL RESTORE VERIFIED', hashlib.sha256(final).hexdigest(), flush=True)
        try:
            loader.run_app(1)
        except OSError as error:
            print('Restart disconnected USB:', error, flush=True)
        print('Restart requested', flush=True)
    finally:
        dev.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
