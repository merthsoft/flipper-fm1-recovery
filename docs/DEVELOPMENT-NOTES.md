# FM1 recovery development notes

Verified on 2026-10-09: **Flipper boot entry, cable handoff to Windows, full flash backup and guarded firmware restoration succeeded**, without a XIAO or external USB host module. The Flipper itself does not flash; the PC performs the backup and restoration. Standalone flashing directly from the Flipper SD card remains unimplemented.

## Current implementation

`fm1_boot_key.c` performs boot entry, **not firmware installation**. It disables the Flipper's USB device connection, powers down the USB transceiver, temporarily configures PA12/D+ and PA11/D− as GPIO, sends the FM1-specific `0x16EF` key, observes D+, and generates calibration pulses on a DWT deadline grid. Version 0.7 suspends task switching during calibration while retaining interrupts outside the short pulse-critical sections. It restores the USB pin configuration and reinitializes the USB device interface on completion or cancellation. It never reads, erases, or writes FM1 flash.

`pc_backup.py` uses Windows SCSI passthrough and the external MIT-licensed [jl-uboot-tool](https://github.com/kagaimiq/jl-uboot-tool) RAM loader. It verifies ROM inquiry, chip key and flash ID, reads the full flash twice, requires identical backups, and keeps the RAM loader alive. Its command allowlist excludes persistent writes and erases. Windows administrator access is required.

`pc_restore.py` defaults to an offline dry run. Compatible FM1 `.fwsc`
packages are accepted without a version/hash pin. Product, chip, package and
flash-directory CRCs, application location and encryption key are validated.
Two identical backups and a fresh matching device read are required. Only
changed 4 KiB application sectors in `[0x4000, 0x93000)` are written; every
sector and the full final image are verified. Bootloader and device data are
preserved. The initial recovery used a Merthsoft.4 hash pin, which was removed
when generalizing the tool at the user's request.


## Verified recovery

Version 0.7 log `trace6.csv`: D+ detected, 6000 calibration pulses, zero starts more than 1us late, maximum start lateness 20 CPU cycles (0.3125us). Windows enumerated ROM `4C4A:8057`, and SCSI INQUIRY returned `WL82 UBOOT1.00 1.00`. CPU timing does not substitute for an oscilloscope measurement, but ROM entry and subsequent recovery were verified on the actual unit.

The RAM loader returned chip key `980F` and flash ID `856014` (1 MiB). Two consecutive full backups matched SHA-256 `bd562bc119d0b140ac1579dba3981bd873fbb6158f7dc125f09db945a1796dd4`. Occasional Windows error 31 on flash reads required bounded retries of idempotent reads; writes and erases are not blindly retried.

Restoration rewrote 17 sectors. The expected and final full flash images both hashed to `6c9cb4dd171a283108e88fbe9aaf232dd86ac5fad45f63496331c6736109e6fc`. The FM1 restarted and enumerated normal USB audio/MIDI `1209:0001` (Felucca); a read-only MIDI query returned `FM-1_900 [running]`. The user confirmed the screen is back and responding normally. Recovery artifacts are retained locally in `.local/fm1-recovery-backup-20261009-3/`: two original backups, expected image, restored readback, and backup/restore logs. Retain those files before cleaning ignored build outputs.

Host tests in `../tests/flipper_recovery_plan_test.py` cover preservation, changed-sector selection, inconsistent/truncated backups, unsupported packages, and transport bounds/forbidden commands. Set `FM1_RECOVERY_PACKAGE` to the release artifact to exercise its actual integrity validation. `../tests/flipper_trace_test.c` covers timestamp wrap and recorder saturation.

## Earlier diagnostic iterations

A D+ response is not verified ROM identification. Version 0.2 uses absolute CPU-cycle deadlines for the key's 8us low/12us high phases, drives the final clock edge low before releasing the pins, and observes D− with a weak input pull-up for possible held-low acknowledgement. A candidate pauses transmission for up to 500ms while checking for a stable D+ pull-up. The weak-pull-up observation differs from Transporter's active 2us probe. Actual signal timing is not measured. The calibration loop is still software-timed, includes GPIO configuration overhead and OS interrupts, and has not been shown equivalent to Transporter's PIO timing. The cable handoff to a PC may miss the ROM timeout. Do not describe this as a working replacement for Transporter.

Built against Momentum mntm-012 SDK API 87.1; the user's connected Momentum dev build d3f89dfe also reports API 87.1. API compatibility does not establish electrical or recovery compatibility.

Verification on 2026-10-09: version 0.1 produced “No D+ response detected” in the user's direct USB-C data cable test, with FM1 powered on after transmission began. This does not distinguish signal generation, cable continuity, or ROM acceptance. Version 0.2 compilation, linking and FAP import/API checks passed. Installed at `/ext/apps/Tools/fm1_boot_key.fap`; SD readback SHA-256 matched the local build (`DB648EDFBB70E92C9D899EBFB5C18569A00CBFA1E161D216543FFE3E4E3947F6`). No physical signal timing, ROM entry, USB host operation or recovery success has been verified. The revised probe is awaiting the user's observation.

The result includes `P` (packets), `A` (candidate acknowledgements), `D` (D− high samples under a weak pull-up), and `T` (last key's CPU-cycle duration in microseconds). D− high samples alone do not prove a cable or target is connected. T does not measure electrical edges, but helps expose timer/scheduling problems. Preserve these counts when reporting a failed attempt.

Version 0.2 also failed: user reported `P29056 A0 D0 T321us`. The user confirmed a direct USB-C data cable and switching FM1 on after starting the helper. The all-zero D− samples exposed an omitted hardware takeover step: Flipper's `furi_hal_usb_disable()` disconnects the USB pull-up, but leaves the peripheral/transceiver enabled. On shared STM32 USB pins this may override GPIO regardless of MODER. Version 0.3 explicitly sets `USB_CNTR.FRES|PDWN`, masks the USB interrupts during takeover, and calls the USB reinitialization API after restoring the pins. Actual pin ownership is still to be verified on the device.

Version 0.3 adds **Right: pin check**. Run this with **nothing plugged into Flipper USB**. Both weak input pull-ups should read high (`H3`), and both pull-downs should read low (`L0`). A failure here means the GPIO path must be investigated before sending more ROM keys. A pass tests input/pull control on the Flipper; it does not verify the cable, the external signal waveform, or ROM acceptance. USB restoration must also be checked by reconnecting to the PC afterward.

Version 0.3 build and FAP checks passed; installed and SD readback verified on 2026-10-09 with SHA-256 `D50A2FA24C8D413FA4AEC77A8A53E0B90503AE10819D1FF03259E07C8AC20056`. The user's no-cable pin check passed (`H3 L0`). The next FM1 attempt reported `P23666 A6 D94576 T321us`; no verified ROM entry or enumeration has been reported. D− now follows the weak pull-up, unlike v0.2, but the six candidate ACKs did not establish recovery. CPU-cycle packet duration remains 321us. Cable continuity and actual waveform acceptance remain unverified; candidate acknowledgements must not be described as confirmed FM1 communication.

The user confirmed that result still said “No D+ response detected”; another attempt reported `A4` with the remaining counts unspecified. No recovery progress can be inferred from A6 changing to A4. Next control: run the same OK/key attempt with no USB cable attached, recording all counts. A candidate ACK in that control would expose a false detection in the helper. Zero candidates would still not prove the FM1 responses are real ROM ACKs; cable capacitance/loading and electrical settling remain possible causes. Further identical FM1 retries do not validate the waveform.

The user's no-cable key control returned `P29634 A0 D118536 T321`. This establishes that the observed candidate ACKs require the connected setup in these runs; it does not distinguish ROM communication from loading/settling effects.

## Version 0.4 event logging

Normal attempts automatically save uniquely named CSV files under `/ext/apps_data/fm1_boot_key/trace.csv`, `trace1.csv`, etc. The screen reports the saved filename or a storage error. Existing logs are retained. No extra button is needed to enable logging.

The logger buffers up to 256 observations in heap memory during the attempt; filesystem I/O occurs only after USB is restored. It records abnormal inter-packet samples, candidate ACK timestamps, the first sample and each observed D+/D− change during the subsequent 500ms passive wait, and wait completion/cancellation. CSV columns are attempt time, packet number, time since the software packet-release timestamp, phase, D+, D−. The first normal gap is included as a reference. The header includes the dropped-event count; saturation retains the earliest observations. This is a sample-based capture, not an oscilloscope. Observed low spans are bounded by the sample spacing (approximately 35us in gaps and 50us plus processing/scheduling during passive waits); brief transitions may be missed. It does not record self-driven key edges or prove cable continuity.

Verification: API 87.1 build/import checks passed; timestamp-wrap and buffer-saturation tests in `../tests/flipper_trace_test.c` passed under the undefined-behavior sanitizer. An on-device `log-test` fixture (no GPIO/USB signal generation) saved two separate logs, and both were read back with the expected timestamps/columns. Version 0.4 FAP SD readback matched SHA-256 `834627F3E009DC8CBC7831CF4F91E1208E2BFD708F53DF8661A828E1726B93C6`. These checks verify logging, not ROM entry or a full-speed USB host. The first two logs on the user's Flipper are explicitly marked `SYNTHETIC SD TEST, no signals` and must be excluded from hardware analysis.

## Version 0.5: keep keying through unconfirmed ACKs

Retrieved v0.4 real runs `trace2.csv` (P26667 A3) and `trace3.csv` (P25675 A4), with the FM1-end cable orientation reversed between them. Neither detected D+; neither dropped capture events. D− began held low and first rose during an ACK-candidate wait at approximately 0.846s / 1.237s. This is consistent with the FM1 being switched on during the 500ms transmission pause, but the log does not directly measure the power switch or supply. D− also went low for about 18ms approximately eight seconds after that first rise; a reset is one possible explanation, not a confirmed diagnosis.

The concrete bug is that an unconfirmed D− low observation suppressed the key stream for 500ms. This can miss cold boot entry, especially when an unpowered device initially clamps the data line. Version 0.5 records low candidates without suspending key transmission and enters calibration only on an observed D+ pull-up. Candidate counting is once per observed low episode rather than once per packet. Gap logging now records changes between released-bus samples instead of every low sample, so the buffer is not consumed while waiting for the user to power the target on. Logs are still sampled evidence; key signals and actual ROM entry remain unverified. Old wait-phase names are retained for compatibility with v0.4 traces and the synthetic storage fixture.

Version 0.5 API 87.1 build checks passed and installation was verified by SD readback (`4A1000386D94AD7D13765A88414CC668B20A06ABF797569C8E9A8B68B60CE079`). Next hardware attempt is pending.

## Version 0.6: fixed-period calibration

The user's v0.5 `trace4.csv` reported `P1367 A7 D47 T321us`, with D+ high at 705878us and still high after approximately six seconds of calibration. The user moved FM1 to the PC on the cue. Windows then reported an attached `USB\VID_0000&PID_0002` device with “Device Descriptor Request Failed”. No `4C4A:8057` ROM identity was obtained. This is evidence of attachment and a successful D+ detection, not verified UBOOT communication.

The previous calibration period included GPIO HAL calls, queue checks and other processing in addition to a nominal 996us delay. That accumulates overhead into the ROM's calibration clock; it is a plausible explanation for failed USB enumeration, not yet a measured diagnosis. Version 0.6 uses absolute CPU-cycle deadlines spaced 1000us apart, a direct GPIO-mode change for each 4us low pulse, and a short interrupt-masked lead-in. Missed periods are skipped rather than emitted as rapid catch-up pulses. The CSV records pulse count, pulses more than 1us late, and maximum lateness in CPU cycles. Actual wire-level timing remains unmeasured; interrupt interference and PC handoff timing remain possible issues.

Version 0.6 API 87.1 build checks passed and FAP SD readback matched `B0BE10C09CA76C044C88CAB2E4794E65789DA36C54DA6B15F27DA15508F2F6CB`. Next hardware attempt is pending. No firmware has been written to FM1 by this tool.

## Version 0.7: isolate calibration scheduling

The user's v0.6 `trace5.csv` detected D+ at approximately 1.207s and emitted 6000 calibration pulses. However, 159 pulses were over 1us late and the maximum start delay was 61427 CPU cycles (approximately 960us). The subsequent PC attachment again failed device-descriptor enumeration, without a valid ROM VID/PID. Fixed deadlines alone did not eliminate disruptive latency.

Version 0.7 temporarily suspends task switching during calibration, while retaining interrupts outside the short pulse critical sections. The installed Momentum `kernel.c` implements this through `vTaskSuspendAll`, with restoration through `xTaskResumeAll`. Because FreeRTOS defers its ticks while suspended, the six-second bound uses DWT cycle counts, not OS ticks. Cancellation polls the active-low Back pin directly; it does not require the suspended input task. Scheduler restoration occurs before USB reinitialization and SD operations. The inspected HAL startup and power-service sources contained no scheduled watchdog-feed dependency; this is not a blanket guarantee for other Flipper firmware builds. Interrupt-induced lateness is still possible and remains logged.

Version 0.7 API 87.1 build checks passed and SD readback matched `DEACB091475601803834AFF8C8C5A6D34A96B22300728BCA16BE3B196BA498B2`. On-device calibration, cancellation and USB enumeration are pending verification. The screen/input task will pause during the bounded calibration phase; Back cancellation uses the raw physical pin.

User reports the FM1 red light appears when connected to the PC but not the Flipper. The helper intentionally supplies no USB VBUS power; the FM1 needs its own charged battery. This observation alone does not establish whether its processor is powered or executing ROM.

## Probe workflow

1. Copy the FAP to `/ext/apps/Tools/fm1_boot_key.fap` using qFlipper or an SD reader.
2. Disconnect Flipper from the PC and chargers. Disable GPIO OTG power. Run both devices on their own batteries. The helper refuses to start if it detects USB input voltage or enabled OTG power.
   First open the helper with no cable attached and press Right for the pin check; require `H3 L0` before proceeding.
3. With FM1 off, connect it directly to Flipper using the data cable. Open the helper and press OK; then turn FM1 on. Back cancels.
4. Keep this connection until the helper finishes and restores USB. Never connect an active signal generator to the PC. A response/handoff cue is only an experimental observation.
5. If requested, move FM1 to PC without power cycling it and inspect USB hardware IDs. Do not start a generic updater unless it supports the identified interface. ROM UBOOT (`4C4A:8057`) and application OTA are different protocols.

## Standalone flasher design and decision gates

Flipper's STM32WB55 has a **USB device-only peripheral**, not a USB host controller. Putting a firmware file on its SD card addresses storage, but not the host transport. Full standalone recovery requires a software full-speed USB host on its USB data pins. Transporter's RP2040 PIO implementation cannot be ported by substituting GPIO calls.

1. **Boot entry:** scope/logic-analyzer comparison if available; repeated cold-start tests, explicit ACK verification rather than D+ alone, deterministic key/calibration pulse generation, restoration/cancellation checks. No flash operations.
2. **Host feasibility:** experimentally establish reliable 12 Mbit/s transmit and receive, reset, SOF, turnaround, CRC, bit stuffing and USB handshakes on the 64 MHz STM32. Investigate bounded assembly/SRAM and timer/DMA implementations. Ordinary C bit-banging is not assumed sufficient. Check pin electrical behavior and host pull-down requirements. This stage can fail; no promise that a FAP alone is enough.
3. **Read-only loader:** enumerate the exact ROM device, inspect descriptors, implement required control/bulk transfers with retries and bounds, load the reviewed RAM-only WL82 loader, identify chip/flash, and stream flash backups to SD. Read the full flash twice and require identical hashes. A loader upload changes volatile RAM and must be distinguished from persistent writes.
4. **Image review:** parse the supported FM1 firmware format; validate product, sizes, checksums and flash layout. Retain bootloader/device-specific data. Reject unsupported chips/images/regions. Use Transporter's reviewed image policy as a reference, including its constrained `0x4000..0x93000` write window; independently confirm the layout before enabling writes.
5. **Guarded writes:** SD backup must exist and validate before erase. Sector-at-a-time erase/program/readback; retain a progress journal. Cancellation only between completed operations. Match fresh device reads to the backup before starting. Verify all target data and preserved regions after writing. On interruption keep the backup/journal and explain how to re-enter ROM; never assume a partially erased sector is intact.
6. **Release:** repeated real hardware read/recovery tests including interruption, bad files, disconnection and low battery. Document supported firmware/boards/cables, provenance and limitations. Publish as a separate GPL-compatible repository when the transport is proven, rather than advertising the probe as a recovery product.

The first critical go/no-go criterion for full flashing is **reliable read-only USB enumeration and repeated identical flash dumps**. A PC handoff helper could be useful independently if the timing window is proven, but does not meet the standalone goal.

## Build

Install `ufbt`, then choose the SDK matching the installed Flipper firmware:

```powershell
ufbt update --hw-target=f7 --url=https://github.com/Next-Flip/Momentum-Firmware/releases/download/mntm-012/flipper-z-f7-sdk-mntm-012.zip
ufbt
```

Output: `dist/fm1_boot_key.fap`. Do not replace the Flipper firmware just to install this app.

## Sources

- [Transporter architecture and timings](https://github.com/kurogedelic/FM-1-transporter/blob/main/docs/ARCHITECTURE.md)
- [Transporter recovery implementation](https://github.com/kurogedelic/FM-1-transporter/blob/main/src/recovery.c)
- [Transporter PIO key waveform](https://github.com/kurogedelic/FM-1-transporter/blob/main/pio/usb_key.pio)
- [Flipper USB GPIO definitions](https://github.com/flipperdevices/flipperzero-firmware/blob/dev/targets/f7/furi_hal/furi_hal_resources.c)
- [Momentum USB lifecycle implementation](https://github.com/Next-Flip/Momentum-Firmware/blob/d3f89dfe/targets/f7/furi_hal/furi_hal_usb.c)
- [ST confirms STM32WB USB peripheral is device-only](https://community.st.com/stm32-mcus-wireless-35/stm32wb-usb-only-device-exist-way-to-use-it-as-host-hid-2965)
- [ST explains USB transceiver ownership of shared GPIO pins](https://community.st.com/stm32-mcus-60/faq-why-usb-can-take-control-of-shared-dp-dm-pins-on-stm32-167523)
- [uFBT build and SDK instructions](https://github.com/flipperdevices/flipperzero-ufbt)

Original recovery code has been extracted into its own project under the Unlicense. See ../THIRD-PARTY.md for external component terms.

## Generic firmware validation

The package hash allowlist was removed after the initial successful recovery.
Ten host tests pass with the real Merthsoft.4 package supplied, including
synthetic packages with different version identities/content and refusals for
wrong product/chip/key, changed application location, duplicate images,
truncation and CRC corruption. Upstream SLOOP 2.5 and custom 2.4.1 and
2.5 Merthsoft.4 packages pass offline extraction and preservation planning.
These checks do not claim hardware flashing of each package.
