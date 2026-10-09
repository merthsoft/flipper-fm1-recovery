# SPDX-License-Identifier: Unlicense
"""Pure image validation and bounded, preservation-aware restoration planning."""
from binascii import crc_hqx
import struct
import re

FLASH_SIZE = 0x100000
APP_START, APP_END, SECTOR = 0x4000, 0x93000, 0x1000
CHIP_KEY = 0x980F


def decode_metadata(encoded):
    """Decode the format's 16-bit LFSR stream, freshly seeded per record."""
    register = 65535
    result = bytearray()
    for value in encoded:
        result.append(value ^ (register & 255))
        register = ((register * 2) ^ ((register >> 15) * 0x1021)) & 65535
    return bytes(result)


def parse_package(raw):
    """Independent parser of the UFW framing; no SLOOP source dependency."""
    if len(raw) < 960:
        raise ValueError('Truncated identity blocks')
    identity = []
    fragments = []
    for index, start in enumerate(range(0, 960, 48)):
        marker = raw[start + 47]
        if marker != 125:
            identity.append(chr((marker - index - 1) % 256))
        fragments.append(raw[start:start + 47])
    logical = b''.join(fragments) + raw[960:]
    header = decode_metadata(logical[:64])
    header_crc, directory_crc, total, count = struct.unpack_from('<HHIH', header)
    if total != len(logical) or not 1 <= count <= 8:
        raise ValueError('Invalid package length or file count')
    if crc_hqx(header[2:], 0) != header_crc:
        raise ValueError('Header CRC mismatch')
    directory = logical[64:64 + count * 80]
    if len(directory) != count * 80 or crc_hqx(directory, 0) != directory_crc:
        raise ValueError('Directory CRC mismatch')
    files = []
    spans = []
    for start in range(0, len(directory), 80):
        entry = decode_metadata(directory[start:start + 80])
        kind, _, checksum, _, offset, size = struct.unpack_from('<HHHHII', entry)
        end = offset + size
        if not size or offset < 64 + len(directory) or end > total:
            raise ValueError('Invalid file extent')
        if any(offset < previous_end and previous_start < end for previous_start, previous_end in spans):
            raise ValueError('Overlapping package files')
        spans.append((offset, end))
        content = logical[offset:end]
        if crc_hqx(content, 0) != checksum:
            raise ValueError('File CRC mismatch')
        files.append((kind, content))
    return ''.join(identity), header[16:22], files


def validate_flash_layout(image):
    if len(image) != APP_END:
        raise ValueError('Unsupported FM1 flash layout size')
    header = decode_metadata(image[:32])
    if crc_hqx(header[2:], 0) != int.from_bytes(header[:2], 'little'):
        raise ValueError('Flash header CRC mismatch')
    entries = {}
    for offset in range(32, 160, 32):
        record = decode_metadata(image[offset:offset + 32])
        if crc_hqx(record[2:], 0) != int.from_bytes(record[:2], 'little'):
            raise ValueError('Flash directory CRC mismatch')
        _, checksum, begin, size = struct.unpack_from('<HHII', record)
        name = record[16:].split(b'\0')[0]
        if name in entries:
            raise ValueError('Duplicate flash directory entry')
        entries[name] = (checksum, begin, size)
    if b'app_dir_head' not in entries or entries[b'app_dir_head'][1] != APP_START:
        raise ValueError('Unsupported application location')
    if b'isd_config.ini' not in entries:
        raise ValueError('Missing package chip-key configuration')
    checksum, begin, size = entries[b'isd_config.ini']
    if size < 34 or begin < 160 or begin + size > APP_START:
        raise ValueError('Invalid chip-key configuration extent')
    configuration = image[begin:begin + size]
    if crc_hqx(configuration, 0) != checksum:
        raise ValueError('Chip-key configuration CRC mismatch')
    key_data = configuration[:32]
    if crc_hqx(key_data, 0) != int.from_bytes(configuration[32:34], 'little'):
        raise ValueError('Chip-key data CRC mismatch')
    threshold = sum(key_data[:16]) & 255
    threshold = 0x55 if threshold <= 0x10 else 0xAA if threshold >= 0xE0 else threshold
    key = sum(int((key_data[16 + bit] ^ key_data[15 - bit]) < threshold) << bit
              for bit in range(16))
    if key != CHIP_KEY:
        raise ValueError('Firmware encryption key does not match FM1')


def extract_firmware(raw):
    """Accept compatible FM1 packages by format and hardware, without a hash pin."""
    product, chip, files = parse_package(raw)
    if not re.fullmatch(r'FM-1_[A-Za-z0-9][A-Za-z0-9._-]*', product):
        raise ValueError('Incorrect product')
    if chip != b'AC791N':
        raise ValueError('Incorrect chip')
    found = [data for kind, data in files if kind == 0]
    if len(found) != 1:
        raise ValueError('Require exactly one flash image')
    validate_flash_layout(found[0])
    return found[0]


def make_plan(first, second, image):
    if len(first) != FLASH_SIZE or first != second:
        raise ValueError('Two identical full flash backups are required')
    if len(image) != APP_END:
        raise ValueError('Unexpected image size')
    expected = first[:APP_START] + image[APP_START:APP_END] + first[APP_END:]
    sectors = [addr for addr in range(APP_START, APP_END, SECTOR)
               if first[addr:addr + SECTOR] != expected[addr:addr + SECTOR]]
    return expected, sectors
