# wx86

**wx86** is a pure Python emulator of the original **IBM PC 5150**, featuring a software implementation of the **Intel 8088** processor.

The goal of the project is to accurately emulate the original IBM PC hardware and eventually boot and run **MS-DOS** and other real-mode software.

> **Project Status:** Work in Progress

---

## Features

- Intel 8088 CPU emulator
- 20-bit segmented memory model (1 MB)
- IBM PC 5150 machine architecture
- Floppy disk image loading
- Pure Python implementation
- Designed to run with **PyPy** for improved performance

---

## Current Status

The emulator is still under active development.

### Implemented

- Intel 8088 CPU (work in progress)
- Memory subsystem
- Machine framework
- Floppy image loading
- Basic emulator window

### Planned

- BIOS ROM support
- IBM Cassette BASIC support
- CGA video adapter
- Keyboard controller
- PIT (8253)
- PIC (8259)
- DMA controller (8237)
- BIOS interrupts
- MS-DOS boot support

---

## Requirements

- **PyPy 3.11** (recommended)
- pygame-ce

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

The emulator executes millions of CPU instructions in Python.

Running under **PyPy** provides significantly better performance than standard CPython thanks to its Just-In-Time (JIT) compiler.

---

## Repository Layout

```
wx86-MS-DOS/
│
├── cpu.py             # Intel 8088 CPU
├── machine.py         # IBM PC machine implementation
├── wx86.py            # Emulator entry point
├── requirements.txt
├── LICENSE
└── README.md
```

---

## BIOS & Disk Images

This repository **does not include IBM BIOS ROMs or MS-DOS disk images**.

To run the emulator, users should obtain compatible BIOS ROMs and DOS boot disks separately and place them in the appropriate directories.

---

## Roadmap

- [ ] Complete 8088 instruction set
- [ ] Accurate FLAGS behavior
- [ ] BIOS ROM execution
- [ ] Boot sector execution
- [ ] INT 13h disk services
- [ ] CGA graphics
- [ ] PIT timer
- [ ] PIC interrupt controller
- [ ] Keyboard input
- [ ] Boot MS-DOS 3.x
- [ ] Run DOS applications

---

## Screenshots

*Coming soon.*

---

## Contributing

Contributions, bug reports, and suggestions are welcome.

Feel free to open an Issue or submit a Pull Request.

---

## License

This project is licensed under the MIT License.
