# wx86

**wx86** is a pure Python emulator of the original **IBM PC 5150**, featuring a software implementation of the **Intel 8086/8088** processor.

The goal of the project is to accurately emulate the original IBM PC hardware and eventually boot and run **MS-DOS** and other real-mode software.

> **Status:** Active Development

---

## Features

* Intel **8086/8088** CPU emulation
* 20-bit segmented memory model (1 MB)
* IBM PC 5150 hardware emulation
* Floppy disk image loading
* Pure Python implementation
* Optimized to run with **PyPy** for improved performance

---

## Current Status

The emulator is under active development.

### Implemented

* Intel 8086/8088 CPU (work in progress)
* Segmented memory subsystem
* Machine framework
* Floppy disk image loading
* Emulator window and display

### Planned

* Complete 8086/8088 instruction set
* BIOS ROM execution
* IBM Cassette BASIC support
* CGA video adapter
* Intel 8253 Programmable Interval Timer (PIT)
* Intel 8259 Programmable Interrupt Controller (PIC)
* Intel 8237 DMA Controller
* Keyboard controller
* BIOS interrupt services
* MS-DOS boot support
* DOS application compatibility

---

## Requirements

* **PyPy 3.11** (recommended)
* pygame-ce

Install dependencies:

```bash
pypy -m pip install -r requirements.txt
```

Run the emulator:

```bash
pypy wx86.py
```

---

## Why PyPy?

Emulating an x86 processor requires executing millions of Python operations per second.

Running under **PyPy** provides a significant performance improvement over standard CPython thanks to its Just-In-Time (JIT) compiler, making the emulator considerably faster.

---

## Repository Layout

```text
wx86-MS-DOS/
│
├── cpu.py             # Intel 8086/8088 CPU
├── machine.py         # IBM PC machine implementation
├── wx86.py            # Emulator entry point
├── requirements.txt
├── LICENSE
└── README.md
```

---

## BIOS ROMs & Disk Images

This repository **does not include IBM BIOS ROMs, IBM Cassette BASIC ROMs, or MS-DOS disk images**.

Users should obtain compatible ROMs and operating system disk images separately.

---

## Roadmap

* [ ] Complete 8086/8088 instruction set
* [ ] Accurate FLAGS emulation
* [ ] Prefetch queue emulation
* [ ] BIOS POST
* [ ] Boot sector execution
* [ ] INT 13h disk services
* [ ] CGA text mode
* [ ] Keyboard support
* [ ] PIT timer
* [ ] PIC interrupt controller
* [ ] Boot MS-DOS
* [ ] Run DOS applications

---

## Screenshots

*Coming soon.*

---

## Contributing

Bug reports, suggestions, and pull requests are welcome.

If you're interested in x86 emulation, retro computing, or IBM PC hardware, feel free to contribute.

---

## License

This project is licensed under the MIT License.
