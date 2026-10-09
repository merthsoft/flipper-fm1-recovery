# FM1 Recovery

Recover an FM1 after an interrupted firmware update using a **Flipper Zero,
a USB data cable, and a Windows PC**. No XIAO RP2040 or extra wiring is needed.

The Flipper puts the FM1 into its ROM recovery mode. Move the cable to the PC,
then the PC tools back up and restore its flash. The Flipper does not yet flash
firmware directly from its SD card.

Successfully recovered a real FM1 on 2026-10-09 with Momentum dev `d3f89dfe`
(API 87.1): two identical 1 MiB backups, 17 rewritten sectors, complete final
readback verification, normal USB audio/MIDI and working screen.

## Build the Flipper app

From this directory:

```powershell
python -m pip install ufbt
ufbt update --hw-target=f7 --url=https://github.com/Next-Flip/Momentum-Firmware/releases/download/mntm-012/flipper-z-f7-sdk-mntm-012.zip
ufbt
```

Use a matching SDK for your installed Flipper firmware. Copy
`dist/fm1_boot_key.fap` to `/ext/apps/Tools/fm1_boot_key.fap` with qFlipper.
Open **FM1 Recovery** in Tools.

## Enter recovery

1. Disconnect Flipper from PC/chargers and disable GPIO OTG power. Both
   devices run from their batteries. With no cable attached, press **Right**:
   the pin check must report `H3 L0`.
2. With the FM1 off, connect it directly to Flipper using a USB data cable.
   Press **OK**, then turn the FM1 on. **Back** cancels.
3. Wait for the helper to finish and restore its USB interface. Move the FM1
   cable to the PC without turning the FM1 off.
4. Windows should detect `4C4A:8057`, `WL82 UBOOT1.00`. This is the ROM
   interface, not the ordinary SLOOP web updater interface.

Traces are automatically saved on Flipper at `/ext/apps_data/fm1_boot_key/`.

## Back up on Windows

Prepare dependencies:

```powershell
git clone https://github.com/kagaimiq/jl-uboot-tool.git .local/jl-uboot-tool
python -m pip install --target .local/python crcmod pycryptodomex
```

In an **administrator PowerShell**, identify the ROM device's physical drive:

```powershell
Get-CimInstance Win32_DiskDrive | Select-Object DeviceID, Model
```

Use the path belonging to **WL82 UBOOT1.00 USB Device**. For example, if that
device is `PHYSICALDRIVE1`:

```powershell
python tools/pc_backup.py --device '\\.\PHYSICALDRIVE1' `
  --reference .local/jl-uboot-tool --dependencies .local/python `
  --output .local/my-backup
```

The tool checks the actual inquiry/chip/flash identity before loading the RAM
loader. It reads flash twice and requires identical files. Results go to
`backup.log`; it then stays running to keep the loader alive. Leave that
process running while preparing restoration. Use a new output directory for
each attempt. **Keep your backup directory outside Git and retain it.**

## Restore

Any compatible **FM1 `.fwsc` firmware package** is accepted, including stock,
upstream SLOOP and custom builds. There is no version or SHA-256 allowlist.
The tool checks package integrity, FM1 product identity, AC791N chip family,
flash layout and encryption key. It preserves the existing bootloader and
device data. Firmware requiring a different flash layout or bootloader is not
compatible with this application-only recovery path.

The normal web updater requires working firmware to serve the USB-MIDI
update protocol. ROM recovery is a SCSI/bulk interface and cannot use that
updater directly. After restoring working firmware, normal web updates work
again. A browser-based ROM recovery tool would need a separate implementation
and USB access/driver support; it is not part of this project.

First produce an offline plan:

```powershell
python tools/pc_restore.py --device '\\.\PHYSICALDRIVE1' `
  --reference .local/jl-uboot-tool --dependencies .local/python `
  --backup .local/my-backup --package C:\path\sloop-2.5-Merthsoft.4.fwsc
```

This is a dry run and sends no device commands. Inspect `restore.log`.
To perform the restore from a second administrator PowerShell,
repeat that command with `--write --keeper-pid <PID>` using the PID of the
still-running `pc_backup.py` process. The restorer takes over its keepalive,
compares a fresh full read to the backups, changes only differing sectors in
`0x4000..0x93000`, verifies each sector and the final full image, then restarts
the FM1. Bootloader and device data are preserved. Do not disconnect during
restoration. Writes/erases are not blindly retried after transport errors.

## Tests

```powershell
python -m unittest discover -s tests -p '*_test.py'
# Optional: verify an actual firmware package too.
$env:FM1_RECOVERY_PACKAGE='C:\path\sloop-2.5-Merthsoft.4.fwsc'
python -m unittest discover -s tests -p '*_test.py'
```

With a host C compiler:

```sh
cc -Wall -Wextra -Werror -fsanitize=undefined tests/flipper_trace_test.c -o trace-test
./trace-test
```

See [development notes](docs/DEVELOPMENT-NOTES.md) for diagnostic iterations
and the future standalone-host design.

## License

Original project code and assets: **[Unlicense](LICENSE)**.
External SDK, protocol-tool and loader terms are listed in
[THIRD-PARTY.md](THIRD-PARTY.md).
