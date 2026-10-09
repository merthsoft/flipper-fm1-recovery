# Provenance and dependencies

The original application, Windows transport, image validation/planning, tests,
icon and documentation in this repository are released under the Unlicense.
The earlier GPL headers reflected the containing SLOOP repository; the user
authorized releasing this original work under the Unlicense when extracting it.
No SLOOP firmware source, GPL package parser, firmware package or device flash
backup is included in the publishable source tree.

External components retain their own terms:

- [jl-uboot-tool](https://github.com/kagaimiq/jl-uboot-tool): MIT, copyright
  Andrey Grigoryev. The PC scripts import its protocol implementation and load
  its `wl82loader.bin` into FM1 RAM. Obtain it separately; preserve its LICENSE
  when redistributing its files. The RAM-loader binary's underlying provenance
  is not established here; this project does not relicense or bundle it.
- [FM-1 Transporter](https://github.com/kurogedelic/FM-1-transporter): MIT,
  used as a reference for ROM key timing, protocol and preservation policy.
  No RP2040 implementation is bundled. The Flipper signal generation and
  Windows transport are separate implementations.
- [Momentum](https://github.com/Next-Flip/Momentum-Firmware) and
  [Flipper firmware](https://github.com/flipperdevices/flipperzero-firmware):
  GPL-3.0 and their component licenses. The application uses their SDK and API;
  our Unlicense does not replace the terms governing SDK/runtime code or
  linked components in built FAPs. SDK/toolchain caches and built binaries are
  excluded from the source repository by `.gitignore`.
- Python dependencies installed for `jl-uboot-tool` retain their respective
  licenses. They are downloaded into an ignored local directory, not vendored.

Protocol constants and hardware timings are documented in the reference
projects. The standalone package parser uses Python's CRC implementation and
an independently written framing/stream decoder; it does not import SLOOP.
