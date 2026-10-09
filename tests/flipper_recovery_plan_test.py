# SPDX-License-Identifier: Unlicense
"""Recovery tests cover preservation and refusals, without hardware access."""
import importlib.util
from pathlib import Path
import unittest
import os
import sys
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from pc_restore import GuardedScsi
from pc_backup import WindowsScsi

spec = importlib.util.spec_from_file_location('recovery_plan', root / 'tools/recovery_plan.py')
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)


class PlanTests(unittest.TestCase):
    def test_transport_blocks_destructive_commands_and_outside_writes(self):
        readonly = WindowsScsi.__new__(WindowsScsi)
        writer = GuardedScsi.__new__(GuardedScsi)
        for command in (0xFB00, 0xFB02, 0xFC12):
            with self.assertRaises(ValueError):
                writer.validate_command(command.to_bytes(2, 'big') + bytes(14), None)
        for addr in (0, plan.APP_START - 512, plan.APP_END, plan.APP_START + 1):
            cdb = b'\xfb\x04' + addr.to_bytes(4, 'big') + b'\x02\x00' + bytes(8)
            with self.assertRaises(ValueError):
                writer.validate_command(cdb, bytes(512))
        cdb = b'\xfb\x04' + plan.APP_START.to_bytes(4, 'big') + b'\x02\x00' + bytes(8)
        writer.validate_command(cdb, bytes(512))
        with self.assertRaises(ValueError):
            readonly.validate_command(cdb, bytes(512))
        with self.assertRaises(ValueError):
            writer.validate_command(b'\xfb\x01' + (plan.APP_START + 512).to_bytes(4, 'big') + bytes(10), None)
    def test_preserves_bootloader_and_device_data(self):
        backup = bytes([0x42]) * plan.FLASH_SIZE
        image = bytes([0x31]) * plan.APP_END
        expected, sectors = plan.make_plan(backup, backup, image)
        self.assertEqual(expected[:plan.APP_START], backup[:plan.APP_START])
        self.assertEqual(expected[plan.APP_END:], backup[plan.APP_END:])
        self.assertEqual(sectors[0], plan.APP_START)
        self.assertEqual(sectors[-1], plan.APP_END - plan.SECTOR)
        self.assertEqual(len(expected), plan.FLASH_SIZE)

    def test_only_changed_sector_and_noop(self):
        backup = bytes(plan.FLASH_SIZE)
        image = bytearray(plan.APP_END)
        image[plan.APP_START + 1] = 7
        self.assertEqual(plan.make_plan(backup, backup, image)[1], [plan.APP_START])
        self.assertEqual(plan.make_plan(backup, backup, bytes(plan.APP_END))[1], [])

    def test_refuses_missing_truncated_or_disagreeing_backups(self):
        backup = bytes(plan.FLASH_SIZE)
        for other in (b'', backup[:-1], b'X' + backup[1:]):
            with self.assertRaises(ValueError):
                plan.make_plan(backup, other, bytes(plan.APP_END))
        with self.assertRaises(ValueError):
            plan.make_plan(backup, backup, bytes(plan.APP_END + 1))

    def test_release_integrity(self):
        path = os.environ.get('FM1_RECOVERY_PACKAGE')
        if not path:
            self.skipTest('Set FM1_RECOVERY_PACKAGE to verify a real firmware package')
        raw = Path(path).read_bytes()
        self.assertEqual(len(plan.extract_firmware(raw)), plan.APP_END)
        bad = bytearray(raw)
        bad[-1] ^= 1
        with self.assertRaises(ValueError):
            plan.extract_firmware(bad)


if __name__ == '__main__':
    unittest.main()
