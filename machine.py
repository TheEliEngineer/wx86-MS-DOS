# machine.py - IBM PC (5150) machine for the wx86 emulator
# Memory map, ROM loading, device ports, BIOS INT services, disk I/O,
# CGA text-mode display and keyboard.

import os
import pygame

from cpu import Cpu8086
from cpu import AX, CX, DX, BX, SP, BP, SI, DI
from cpu import AL, CL, DL, BL, AH, CH, DH, BH
from cpu import ES, CS, SS, DS

# CGA 16-colour palette (R,G,B)
CGA16 = [
    (0,0,0),(0,0,170),(0,170,0),(0,170,170),(170,0,0),(170,0,170),(170,85,0),(170,170,170),
    (85,85,85),(85,85,255),(85,255,85),(85,255,255),(255,85,85),(255,85,255),(255,255,85),(255,255,255),
]

# Map CP437 code points to Unicode for the few chars DOS screens actually use.
CP437 = {}
for c in range(32, 127):
    CP437[c] = chr(c)
_extra = {0x0B0:0x2591,0x0B1:0x2592,0x0B2:0x2593,0x0B3:0x2502,0x0B4:0x2524,0x0B5:0x2561,
0x0B6:0x2562,0x0B7:0x2556,0x0B8:0x2555,0x0B9:0x2563,0x0BA:0x2551,0x0BB:0x2557,0x0BC:0x255D,
0x0BD:0x255C,0x0BE:0x255B,0x0BF:0x2510,0x0C0:0x250C,0x0C1:0x2514,0x0C2:0x2534,0x0C3:0x252C,
0x0C4:0x2500,0x0C5:0x253C,0x0C6:0x255E,0x0C7:0x255F,0x0C8:0x255A,0x0C9:0x2554,0x0CA:0x2569,
0x0CB:0x2566,0x0CC:0x2560,0x0CD:0x2550,0x0CE:0x256C,0x0CF:0x2567,0x0D0:0x2568,0x0D1:0x2564,
0x0D2:0x2565,0x0D3:0x2559,0x0D4:0x2558,0x0D5:0x2552,0x0D6:0x2553,0x0D7:0x256B,0x0D8:0x256A,
0x0D9:0x2518,0x0DA:0x250C,0x0DB:0x2588,0x0DC:0x2584,0x0DD:0x258C,0x0DE:0x2590,0x0DF:0x2580,
0x0E0:0x0393,0x0E1:0x03B1,0x0E2:0x03B2,0x0F9:0x221A,0x0FC:0x2192,0x0FE:0x25A0}
for k,v in _extra.items(): CP437[k]=chr(v)
CP437[0x20]=chr(0x20)


class Machine:
    MEMSIZE = 0x100000           # 1 MB address space of the 8086
    VBASE_C = 0xB8000            # CGA colour text framebuffer
    VBASE_M = 0xB0000            # MDA/mono text framebuffer
    BOOTSEG = 0x0000
    BOOTOFF = 0x7C00

    HOOKED = {0x08,0x09,0x10,0x11,0x12,0x13,0x15,0x16,0x17,0x19,0x1A,0x1C}

    def __init__(self, diskfiles):
        self.mem = bytearray(self.MEMSIZE)
        self.diskfiles = diskfiles          # list of .IMG paths (A:, B: ...)
        self.disk_cache = [None]*len(diskfiles)
        self.cpu = Cpu8086(self)
        # video state
        self.cols = 80
        self.rows = 25
        self.vmode = 3
        self.vbase = self.VBASE_C
        self.cur_row = 0
        self.cur_col = 0
        self.cur_visible = True
        # keyboard
        self.keyq = []            # list of (scancode, ascii)
        self.shift = False
        self.ctrl = False
        self.alt = False
        self.caps = False
        self.num = False
        self.blocked = False
        self.fault_msg = None
        # timing
        self.ticks = 0
        # disk geometry: 360KB 5.25" 2 heads 40 cyl 9 sect
        self.geo_heads = 2
        self.geo_cyls  = 40
        self.geo_sect  = 9
        # display surfaces (created in init_display)
        self.screen = None
        self.cell_w = 9
        self.cell_h = 16
        self._cell_cache = {}

    # ---------- memory ----------
    def rb(self, a):
        return self.mem[a & 0xFFFFF]
    def wb(self, a, v):
        self.mem[a & 0xFFFFF] = v & 0xFF
    def rbs(self, a, n):
        a &= 0xFFFFF
        return bytes(self.mem[a:a+n])
    def wbs(self, a, data):
        a &= 0xFFFFF
        self.mem[a:a+len(data)] = data

    # ---------- ROM loading ----------
    def load_roms(self, bios, f6, f8, fa, fc):
        def put(addr, path):
            with open(path,'rb') as f: data=f.read()
            self.mem[addr:addr+len(data)] = data
        put(0xF6000, f6)
        put(0xF8000, f8)
        put(0xFA000, fa)
        put(0xFC000, fc)
        put(0xFE000, bios)
        # The 8086 reset vector is at 0xFFFF0 -> 0xF000:0000 = 0xFE000 (where the BIOS lives)
        # put a far jump there: EA 00 00 00 F0
        self.mem[0xFFFF0] = 0xEA
        self.mem[0xFFFF1] = 0x00
        self.mem[0xFFFF2] = 0x00
        self.mem[0xFFFF3] = 0x00
        self.mem[0xFFFF4] = 0xF0

    # ---------- BDA / IVT init ----------
    def init_bda(self):
        mem = self.mem
        # Interrupt vectors: point our hooked ints at a tiny IRET stub in ROM.
        # We put 16 bytes of 0xCF (IRET) at 0xFFFE0 so every vector can use it.
        for i in range(16):
            self.mem[0xFFFE0 + i] = 0xCF
        stub_seg = 0xF000
        stub_off = 0xFFE0
        for n in self.HOOKED:
            v = n * 4
            mem[v]   = stub_off & 0xFF
            mem[v+1] = stub_off >> 8
            mem[v+2] = stub_seg & 0xFF
            mem[v+3] = stub_seg >> 8
        # BDA at 0x400
        # Memory size (KB) at 0x413
        mem[0x413] = 0x80; mem[0x414] = 0x02          # 640 KB
        # Equipment word at 0x410
        # bit0=diskette, bits5-4=01b (2 drives), bits3-2=10b (80x25 colour)
        equip = 0x0041 | (1<<4) | (1<<0)
        mem[0x410] = equip & 0xFF; mem[0x411] = equip >> 8
        # Video mode/state at 0x449
        mem[0x449] = self.vmode
        mem[0x44A] = self.cols & 0xFF; mem[0x44B] = self.cols >> 8
        mem[0x484] = self.rows - 1
        mem[0x463] = 0xD4; mem[0x464] = 0x03          # CRTC port 0x03D4

    # ---------- reset / boot ----------
    def reset(self):
        self.init_bda()
        # Load the boot sector of disk 0 into 0000:7C00 and start there.
        if self.diskfiles:
            sec = self.read_sectors(0, 0, 0, 1, 1)   # cyl0 head0 sect1
            self.wbs(0x07C00, sec)
        c = self.cpu
        c.sregs[CS] = 0x0000
        c.ip = self.BOOTOFF
        # DL = boot drive (0x00)
        c.regs[DX] = 0x0000
        c.sregs[DS] = 0x0000; c.sregs[ES] = 0x0000; c.sregs[SS] = 0x0000
        c.regs[SP] = 0x0700

    # ---------- device ports (minimal, enough that DOS does not wedge) ----------
    def in_port(self, p):
        if p == 0x60:                                # keyboard data
            return self.keyq[0][0] if self.keyq else 0
        if p == 0x61:
            return 0x00
        if p == 0x21:                                 # PIC mask / ISR probe
            return 0x00
        return 0x00
    def out_port(self, p, v): pass
    def in_port16(self, p): return self.in_port(p) | (self.in_port(p+1)<<8)
    def out_port16(self, p, v): self.out_port(p, v); self.out_port(p+1, v>>8)

    # ---------- disk I/O (INT 13h backend) ----------
    def _disk_data(self, idx):
        if self.disk_cache[idx] is None:
            with open(self.diskfiles[idx],'rb') as f:
                self.disk_cache[idx] = f.read()
        return self.disk_cache[idx]

    def read_sectors(self, drive, cyl, head, sector, count):
        data = self._disk_data(drive)
        out = bytearray()
        for i in range(count):
            s = sector + i
            h = head
            c = cyl
            # normalize rolling over sectors-per-track into next head/cyl
            while s > self.geo_sect:
                s -= self.geo_sect; h += 1
                if h >= self.geo_heads:
                    h = 0; c += 1
            if c >= self.geo_cyls:
                out += b'\x00'*512; continue
            offset = ((c*self.geo_heads + h)*self.geo_sect + (s-1))*512
            out += data[offset:offset+512]
        return bytes(out)

    def write_sectors(self, drive, cyl, head, sector, count, data):
        # kept simple; not strictly needed for an interactive demo shell.
        return True

    # ===================================================================
    #  INT dispatch
    # ===================================================================
    def int_call(self, cpu, n):
        # Hooked interrupts are executed directly in Python (BIOS services).
        # Non-hooked interrupts use the real IVT mechanism so DOS can install
        # its own handlers (e.g. INT 21h).
        if n in self.HOOKED:
            return self.bios_int(n, cpu)
        v = n * 4
        off = self.mem[v] | (self.mem[v+1]<<8)
        seg = self.mem[v+2] | (self.mem[v+3]<<8)
        if off == 0 and seg == 0:
            # vector not installed yet: treat as a no-op, leave the stack intact
            return False
        # real INT: push FLAGS, CS, IP; jump to IVT[n]
        cpu.push16(cpu.get_flags())
        cpu.push16(cpu.sregs[CS])
        cpu.push16(cpu.ip)
        cpu.set_flag(0x0200|0x0100, False)             # clear IF, TF
        cpu.sregs[CS] = seg
        cpu.ip = off
        return False

    # ---------- BIOS interrupt services ----------
    def bios_int(self, n, cpu):
        if n == 0x10: return self.int10(cpu)
        if n == 0x13: return self.int13(cpu)
        if n == 0x15: return self.int15(cpu)
        if n == 0x16: return self.int16(cpu)
        if n == 0x19: return self.int19(cpu)
        if n == 0x11:
            cpu.regs[AX] = (self.mem[0x411]<<8) | self.mem[0x410]
            return False
        if n == 0x12:
            cpu.regs[AX] = self.mem[0x413] | (self.mem[0x414]<<8)
            return False
        if n == 0x1A:
            cpu.regs[DX] = self.ticks & 0xFFFF
            cpu.regs[CX] = (self.ticks >> 16) & 0xFFFF
            cpu.regs[AX] = 0
            return False
        if n == 0x08:                          # timer IRQ -> just IRET behaviour
            return False
        if n in (0x09,0x1C,0x17):
            return False
        return False

    # --- INT 10h video ---
    def _vcell(self, row, col):
        return self.vbase + (row*self.cols + col)*2

    def int10(self, cpu):
        ah = cpu.r8(AH)
        al = cpu.r8(AL)
        if ah == 0x00:                          # set mode
            self.vmode = al
            self.vbase = self.VBASE_M if al == 7 else self.VBASE_C
            self.mem[0x449] = al
            self.clear_screen(0x07)
            self.cur_row = 0; self.cur_col = 0
            return False
        if ah == 0x01:                          # set cursor type
            return False
        if ah == 0x02:                          # set cursor pos
            row = cpu.r8(DH); col = cpu.r8(DL)
            if row < self.rows: self.cur_row = row
            if col < self.cols: self.cur_col = col
            self.mem[0x450] = col; self.mem[0x451] = row
            return False
        if ah == 0x03:                          # get cursor pos
            cpu.w8(DH, self.cur_row); cpu.w8(DL, self.cur_col)
            cpu.w8(CH, 6); cpu.w8(CL, 7)
            return False
        if ah == 0x05:                          # set display page (ignore)
            return False
        if ah == 0x06 or ah == 0x07:            # scroll up / down
            lines = al
            attr = cpu.r8(BL)
            top = cpu.r8(CH); left = cpu.r8(CL)
            bot = cpu.r8(DH); right = cpu.r8(DL)
            if lines == 0: lines = bot - top + 1
            self.scroll(top,left,bot,right,lines,attr, down=(ah==0x07))
            return False
        if ah == 0x08:                          # read char+attr at cursor
            off = self._vcell(self.cur_row, self.cur_col)
            cpu.w8(AL, self.mem[off]); cpu.w8(AH, self.mem[off+1])
            return False
        if ah == 0x09:                          # write char+attr
            ch = al; attr = cpu.r8(BL); cnt = cpu.regs[CX] or 1
            for _ in range(cnt):
                self.putch(ch, attr, advance=False)
                if self.cur_col >= self.cols-1: break
                self.cur_col += 1
            return False
        if ah == 0x0A:                          # write char only (keep attr)
            ch = al; cnt = cpu.regs[CX] or 1
            for _ in range(cnt):
                off = self._vcell(self.cur_row, self.cur_col)
                self.mem[off] = ch
                if self.cur_col >= self.cols-1: break
                self.cur_col += 1
            return False
        if ah == 0x0E:                          # teletype output
            self.tty(ch=al, attr=0x07)
            return False
        if ah == 0x0F:                          # get video mode
            cpu.w8(AL, self.vmode); cpu.w8(AH, self.cols); cpu.w8(BH, 0)
            return False
        if ah == 0x13:                          # write string
            self.write_string(cpu)
            return False
        return False

    def write_string(self, cpu):
        sub = cpu.r8(AL)
        attr = cpu.r8(BL)
        count = cpu.regs[CX]
        sp = cpu.r16(BP)
        seg = cpu.sregs[ES]
        self.cur_row = cpu.r8(DH); self.cur_col = cpu.r8(DL)
        step = 2 if (sub & 1) else 1
        if sub & 2:
            self.cur_row = cpu.r8(DH); self.cur_col = cpu.r8(DL)
        for i in range(count):
            ch = self.rb((seg<<4) + (sp+i*step & 0xFFFF))
            a = attr if not (sub & 1) else self.rb((seg<<4)+(sp+i*step+1 & 0xFFFF))
            self.tty(ch=ch, attr=a)
        return False

    def putch(self, ch, attr, advance=True):
        off = self._vcell(self.cur_row, self.cur_col)
        self.mem[off] = ch & 0xFF
        self.mem[off+1] = attr & 0xFF
        if advance:
            self.cur_col += 1
            if self.cur_col >= self.cols:
                self.cur_col = 0
                self.cur_row += 1
                if self.cur_row >= self.rows:
                    self.cur_row = self.rows-1
                    self.scroll(0,0,self.rows-1,self.cols-1,1,0x07)

    def tty(self, ch, attr):
        if ch == 0x0D:                          # CR
            self.cur_col = 0; self.sync_cursor(); return
        if ch == 0x0A:                          # LF
            self.cur_row += 1
            if self.cur_row >= self.rows:
                self.cur_row = self.rows-1
                self.scroll(0,0,self.rows-1,self.cols-1,1,attr)
            self.sync_cursor(); return
        if ch == 0x08:                          # BS
            if self.cur_col > 0: self.cur_col -= 1
            off = self._vcell(self.cur_row, self.cur_col)
            self.mem[off] = 0x20; self.mem[off+1] = attr
            self.sync_cursor(); return
        if ch == 0x07:                          # BEL
            return
        self.putch(ch, attr)
        self.sync_cursor()

    def sync_cursor(self):
        self.mem[0x450] = self.cur_col & 0xFF
        self.mem[0x451] = self.cur_row & 0xFF

    def clear_screen(self, attr=0x07):
        base = self.vbase
        for i in range(self.cols*self.rows):
            self.mem[base+i*2] = 0x20
            self.mem[base+i*2+1] = attr
        self.cur_row = 0; self.cur_col = 0; self.sync_cursor()

    def scroll(self, top, left, bot, right, lines, attr, down=False):
        width = right-left+1
        if lines > (bot-top+1): lines = bot-top+1
        if lines == (bot-top+1):
            # blank
            for r in range(top,bot+1):
                for c in range(left,right+1):
                    o = self._vcell(r,c); self.mem[o]=0x20; self.mem[o+1]=attr
            return
        src = top + lines if not down else bot - lines
        for r in (range(top, bot-lines+1)) if not down else (range(bot, top+lines-1, -1)):
            for c in range(left, right+1):
                so = self._vcell(r,c)
                do = self._vcell((r+lines) if not down else (r-lines), c)
                self.mem[do] = self.mem[so]; self.mem[do+1] = self.mem[so+1]
        # blank freed rows
        if not down:
            for r in range(bot-lines+1, bot+1):
                for c in range(left,right+1):
                    o=self._vcell(r,c); self.mem[o]=0x20; self.mem[o+1]=attr
        else:
            for r in range(top, top+lines):
                for c in range(left,right+1):
                    o=self._vcell(r,c); self.mem[o]=0x20; self.mem[o+1]=attr

    # --- INT 15h (cassette / AT-services) ---
    def int15(self, cpu):
        # PC/XT: the original BIOS does not implement these services.
        # Returning CF=1 (unsupported) tells callers like DOS that the
        # function is not available (correct behaviour for a 5150).
        cpu.set_flag(0x0001, True)
        cpu.w8(AH, 0x86)            # function not implemented
        return False

    # --- INT 13h disk ---
    def int13(self, cpu):
        ah = cpu.r8(AH); dl = cpu.r8(DL)
        drive = dl & 0x7F
        if drive >= len(self.diskfiles):
            cpu.set_flag(0x0001, True); cpu.w8(AH, 0x80); return False
        if ah == 0x00:                          # reset
            cpu.w8(AH, 0); cpu.set_flag(0x0001, False); return False
        if ah == 0x02:                          # read sectors
            count = cpu.r8(AL)
            ch = cpu.r8(CH)
            cl = cpu.r8(CL)
            cyl = (ch | ((cl & 0xC0) << 2))
            sector = cl & 0x3F
            head = cpu.r8(DH)
            buf_seg = cpu.sregs[ES]; buf_off = cpu.regs[BX]
            try:
                data = self.read_sectors(drive, cyl, head, sector, count)
            except Exception:
                cpu.set_flag(0x0001, True); cpu.w8(AH, 0x04); return False
            for k in range(len(data)):
                self.wb((buf_seg<<4)+((buf_off+k)&0xFFFF), data[k])
            cpu.w8(AH, 0); cpu.w8(AL, count); cpu.set_flag(0x0001, False)
            return False
        if ah == 0x03:                          # write sectors
            cpu.w8(AH, 0); cpu.set_flag(0x0001, False); return False
        if ah == 0x08:                          # get drive params
            cpu.w8(BL, 0); cpu.w8(DL, len(self.diskfiles))
            cpu.w8(CH, (self.geo_cyls-1) & 0xFF)
            cpu.w8(CL, ((self.geo_cyls-1)>>2 & 0xC0) | (self.geo_sect & 0x3F))
            cpu.w8(DH, self.geo_heads-1)
            cpu.set_flag(0x0001, False); cpu.w8(AH, 0)
            return False
        cpu.set_flag(0x0001, True); cpu.w8(AH, 0x01)
        return False

    # --- INT 16h keyboard ---
    def int16(self, cpu):
        ah = cpu.r8(AH)
        if ah == 0x00 or ah == 0x10:            # read key (block)
            if not self.keyq:
                return True                     # tell CPU to block & yield
            sc, asc = self.keyq.pop(0)
            cpu.w8(AH, sc); cpu.w8(AL, asc)
            return False
        if ah == 0x01 or ah == 0x11:            # key available?
            if self.keyq:
                sc, asc = self.keyq[0]
                cpu.w8(AH, sc); cpu.w8(AL, asc)
                cpu.set_flag(0x0040, False)      # clear ZF -> available
            else:
                cpu.set_flag(0x0040, True)      # set ZF -> none
            return False
        if ah == 0x02:                          # shift status
            st = 0
            if self.shift: st |= 0x01|0x02
            if self.ctrl:  st |= 0x04
            if self.alt:   st |= 0x08
            if self.caps:  st |= 0x40
            if self.num:   st |= 0x20
            cpu.w8(AL, st); return False
        return False

    # --- INT 19h boot ---
    def int19(self, cpu):
        if self.diskfiles and self.diskfiles[0]:
            sec = self.read_sectors(0,0,0,1,1)
            self.wbs(0x07C00, sec)
        cpu.sregs[CS] = 0x0000
        cpu.ip = self.BOOTOFF
        cpu.regs[DX] = 0x0000
        cpu.sregs[DS] = 0; cpu.sregs[ES] = 0
        return False
        cpu.sregs[CS] = 0x0000
        cpu.ip = self.BOOTOFF
        cpu.regs[DX] = 0x0000
        cpu.sregs[DS] = 0; cpu.sregs[ES] = 0
        return False

    # ===================================================================
    #  Display
    # ===================================================================
    def insert_disk(self, slot, path):
        if not os.path.exists(path):
            return
        while slot >= len(self.diskfiles):
            self.diskfiles.append(None)
            self.disk_cache.append(None)
        self.diskfiles[slot] = path
        self.disk_cache[slot] = None   # invalidate; will lazy-load on next I/O

    def boot(self):
        c = self.cpu
        c.halted = False
        c.blocked = False
        c.faulted = False
        c.instr_count = 0
        c.seg_override = None
        c.rep_prefix = None
        c.flags = 0
        c.regs = [0] * 8
        c.sregs = [0] * 4
        c.ip = 0
        self.fault_msg = None
        self.keyq = []
        self.init_bda()
        if self.screen is not None:
            self.clear_screen(0x07)
        if self.diskfiles and self.diskfiles[0]:
            sec = self.read_sectors(0, 0, 0, 1, 1)
            self.wbs(0x07C00, sec)
        c.sregs[1] = 0x0000
        c.ip = self.BOOTOFF
        c.regs[DX] = 0x0000
        c.sregs[DS] = 0x0000; c.sregs[ES] = 0x0000; c.sregs[SS] = 0x0000
        c.regs[SP] = 0x0700

    def init_display(self, top_offset=0):
        pygame.init()
        w = self.cols * self.cell_w
        h = self.rows * self.cell_h + top_offset
        self.top_offset = top_offset
        try:
            self.screen = pygame.display.set_mode((w, h), pygame.SCALED, vsync=1)
        except pygame.error:
            self.screen = pygame.display.set_mode((w, h))
        pygame.display.set_caption("wx86 - IBM PC 5150 / MS-DOS 3.30")
        self.font = pygame.font.SysFont("Courier New,Consolas,DejaVu Sans Mono,monospace", max(10, self.cell_h-2), bold=False)
        self.clear_screen(0x07)

    def _cell_surface(self, ch, attr):
        key = (ch, attr)
        s = self._cell_cache.get(key)
        if s is not None:
            return s
        fg = CGA16[attr & 0x07] if (attr & 0x08) else CGA16[attr & 0x0F]
        # treat bit3 as intensity of fg; bit7 blink ignored (rendered as steady)
        fg = CGA16[attr & 0x0F]
        bg = CGA16[(attr >> 4) & 0x07]
        surf = pygame.Surface((self.cell_w, self.cell_h))
        surf.fill(bg)
        uni = CP437.get(ch, chr(ch) if ch < 256 else '?')
        gs = self.font.render(uni, True, fg)
        surf.blit(gs, (1, 0))
        self._cell_cache[key] = surf
        return surf

    def render(self):
        base = self.vbase
        oy = getattr(self, "top_offset", 0)
        for r in range(self.rows):
            y = r*self.cell_h + oy
            for c in range(self.cols):
                o = base + (r*self.cols + c)*2
                ch = self.mem[o]; attr = self.mem[o+1]
                self.screen.blit(self._cell_surface(ch, attr), (c*self.cell_w, y))
        # cursor block
        if self.cur_visible and (self.ticks // 8) % 2 == 0:
            cx = self.cur_col*self.cell_w
            cy = (self.cur_row+1)*self.cell_h - 3 + oy
            pygame.draw.rect(self.screen, (255,255,255), (cx, cy, self.cell_w-2, 2))
        if self.fault_msg:
            surf = self.font.render("emulation halted: see console", True, (255,255,255), (0,0,0))
            self.screen.blit(surf, (4, self.cell_h*24 + oy))