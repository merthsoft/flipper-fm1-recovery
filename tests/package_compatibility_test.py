# SPDX-License-Identifier: Unlicense
"""Hardware-free package fixtures exercise compatibility and corruption checks."""
from binascii import crc_hqx
from pathlib import Path
import struct
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import recovery_plan as policy


def encode(data):
    state = 0xFFFF
    output = bytearray()
    for byte in data:
        output.append(byte ^ (state & 0xFF))
        carry = state & 0x8000
        state = (state << 1) & 0xFFFF
        if carry:
            state ^= 0x1021
    return output


def flash_fixture(key=0x980F, app_start=0x4000, tone=1):
    image = bytearray([tone] * policy.APP_END)
    header = bytearray(32)
    struct.pack_into('<H', header, 0, crc_hqx(header[2:], 0))
    image[:32] = encode(header)
    key_blob = bytearray(32)
    for bit in range(16):
        key_blob[16 + bit] = 0 if key & (1 << bit) else 255
    configuration = key_blob + struct.pack('<H', crc_hqx(key_blob, 0))
    image[160:194] = configuration
    descriptors = [(b'uboot.boot', 194, 1, crc_hqx(image[194:195], 0)),
                   (b'isd_config.ini', 160, 34, crc_hqx(configuration, 0)),
                   (b'app_dir_head', app_start, 0xFFFFFFFF, 0xFFFF),
                   (b'key_mac', 0xFF000, 0x1000, 0xFFFF)]
    for index, (name, offset, size, checksum) in enumerate(descriptors):
        record = bytearray(32)
        struct.pack_into('<HII', record, 2, checksum, offset, size)
        record[16:16 + len(name)] = name
        struct.pack_into('<H', record, 0, crc_hqx(record[2:], 0))
        image[32 + index * 32:64 + index * 32] = encode(record)
    return bytes(image)


def package(image, product='FM-1_015', chip=b'AC791N', copies=1):
    total = 1024 + len(image) * copies
    logical = bytearray(total)
    directory = bytearray()
    for index in range(copies):
        entry = bytearray(80)
        offset = 1024 + index * len(image)
        struct.pack_into('<HHHHIII', entry, 0, 0, index, crc_hqx(image, 0),
                         0, offset, len(image), len(image))
        directory.extend(encode(entry))
        logical[offset:offset + len(image)] = image
    header = bytearray(64)
    struct.pack_into('<HIH', header, 2, crc_hqx(directory, 0), total, copies)
    header[16:16 + len(chip)] = chip
    struct.pack_into('<H', header, 0, crc_hqx(header[2:], 0))
    logical[:64] = encode(header)
    logical[64:64 + len(directory)] = directory
    wire = bytearray()
    for index in range(20):
        wire.extend(logical[index * 47:(index + 1) * 47])
        wire.append((ord(product[index]) + index + 1) % 256 if index < len(product) else 125)
    return bytes(wire + logical[940:])


class CompatibilityTests(unittest.TestCase):
    def test_accepts_different_firmware_versions_and_contents(self):
        for version, tone in [('FM-1_015', 1), ('FM-1_900', 2), ('FM-1_custom', 3)]:
            image = flash_fixture(tone=tone)
            self.assertEqual(policy.extract_firmware(package(image, version)), image)

    def test_rejects_other_product_and_chip(self):
        image = flash_fixture()
        for raw in (package(image, 'OTHER_900'), package(image, chip=b'AC790N')):
            with self.assertRaises(ValueError):
                policy.extract_firmware(raw)

    def test_rejects_incompatible_key_location_and_duplicate_images(self):
        for raw in (package(flash_fixture(key=0x1234)),
                    package(flash_fixture(app_start=0x5000)),
                    package(flash_fixture(), copies=2)):
            with self.assertRaises(ValueError):
                policy.extract_firmware(raw)

    def test_rejects_corruption_at_each_framing_layer(self):
        raw = package(flash_fixture())
        for offset in (0, 65, len(raw) - 1):
            corrupted = bytearray(raw)
            corrupted[offset] ^= 1
            with self.assertRaises(ValueError):
                policy.extract_firmware(corrupted)
        for truncated in (raw[:100], raw[:-1]):
            with self.assertRaises(ValueError):
                policy.extract_firmware(truncated)

    def test_checks_flash_metadata_even_with_valid_outer_package_crc(self):
        image = bytearray(flash_fixture())
        image[0] ^= 1
        with self.assertRaisesRegex(ValueError, 'Flash header CRC'):
            policy.extract_firmware(package(image))
        image = bytearray(flash_fixture())
        image[64] ^= 1
        with self.assertRaisesRegex(ValueError, 'Flash directory CRC'):
            policy.extract_firmware(package(image))


if __name__ == '__main__':
    unittest.main()
