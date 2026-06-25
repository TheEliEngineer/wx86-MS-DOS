# wx86.py - IBM PC 5150 emulator running MS-DOS 3.30
# Entry point: set up machine, boot, run loop with pygame.

import os
import sys
import time
import pygame
import tkinter as tk
from tkinter import filedialog

from machine import Machine

BUTTON_BAR_H = 34
BUTTONS = [("Load Disk", 0), ("Reset", 1)]

# ---- scan-code (set 1) map for common keys ----
SCORE = {
    pygame.K_ESCAPE:0x01, pygame.K_1:0x02, pygame.K_2:0x03, pygame.K_3:0x04,
    pygame.K_4:0x05, pygame.K_5:0x06, pygame.K_6:0x07, pygame.K_7:0x08,
    pygame.K_8:0x09, pygame.K_9:0x0A, pygame.K_0:0x0B, pygame.K_MINUS:0x0C,
    pygame.K_EQUALS:0x0D, pygame.K_BACKSPACE:0x0E, pygame.K_TAB:0x0F,
    pygame.K_q:0x10, pygame.K_w:0x11, pygame.K_e:0x12, pygame.K_r:0x13,
    pygame.K_t:0x14, pygame.K_y:0x15, pygame.K_u:0x16, pygame.K_i:0x17,
    pygame.K_o:0x18, pygame.K_p:0x19, pygame.K_LEFTBRACKET:0x1A,
    pygame.K_RIGHTBRACKET:0x1B, pygame.K_RETURN:0x1C, pygame.K_LCTRL:0x1D,
    pygame.K_a:0x1E, pygame.K_s:0x1F, pygame.K_d:0x20, pygame.K_f:0x21,
    pygame.K_g:0x22, pygame.K_h:0x23, pygame.K_j:0x24, pygame.K_k:0x25,
    pygame.K_l:0x26, pygame.K_SEMICOLON:0x27, pygame.K_QUOTE:0x28,
    pygame.K_BACKQUOTE:0x29, pygame.K_LSHIFT:0x2A, pygame.K_BACKSLASH:0x2B,
    pygame.K_z:0x2C, pygame.K_x:0x2D, pygame.K_c:0x2E, pygame.K_v:0x2F,
    pygame.K_b:0x30, pygame.K_n:0x31, pygame.K_m:0x32, pygame.K_COMMA:0x33,
    pygame.K_PERIOD:0x34, pygame.K_SLASH:0x35, pygame.K_RSHIFT:0x36,
    pygame.K_SPACE:0x39, pygame.K_CAPSLOCK:0x3A,
    pygame.K_F1:0x3B, pygame.K_F2:0x3C, pygame.K_F3:0x3D, pygame.K_F4:0x3E,
    pygame.K_F5:0x3F, pygame.K_F6:0x40, pygame.K_F7:0x41, pygame.K_F8:0x42,
    pygame.K_F9:0x43, pygame.K_F10:0x44,
    pygame.K_HOME:0x47, pygame.K_UP:0x48, pygame.K_PAGEUP:0x49,
    pygame.K_LEFT:0x4B, pygame.K_RIGHT:0x4D, pygame.K_END:0x4F,
    pygame.K_DOWN:0x50, pygame.K_PAGEDOWN:0x51, pygame.K_DELETE:0x53,
}

_ALPHA = set(range(pygame.K_a, pygame.K_z + 1))
_DIGIT = {pygame.K_1:'!',pygame.K_2:'@',pygame.K_3:'#',pygame.K_4:'$',
          pygame.K_5:'%',pygame.K_6:'^',pygame.K_7:'&',pygame.K_8:'*',
          pygame.K_9:'(',pygame.K_0:')'}

_SHIFT_MAP = {
    pygame.K_MINUS:'_', pygame.K_EQUALS:'+', pygame.K_LEFTBRACKET:'{',
    pygame.K_RIGHTBRACKET:'}', pygame.K_SEMICOLON:':', pygame.K_QUOTE:'"',
    pygame.K_BACKQUOTE:'~', pygame.K_BACKSLASH:'|', pygame.K_COMMA:'<',
    pygame.K_PERIOD:'>', pygame.K_SLASH:'?',
}
_NORMAL_MAP = {
    pygame.K_MINUS:'-', pygame.K_EQUALS:'=', pygame.K_LEFTBRACKET:'[',
    pygame.K_RIGHTBRACKET:']', pygame.K_SEMICOLON:';', pygame.K_QUOTE:"'",
    pygame.K_BACKQUOTE:'`', pygame.K_BACKSLASH:'\\', pygame.K_COMMA:',',
    pygame.K_PERIOD:'.', pygame.K_SLASH:'/',
}

def ascii_for(ev, mach):
    sc = SCORE.get(ev.key)
    if sc is None:
        return None, None
    mods = ev.mod
    shift = bool(mods & pygame.KMOD_SHIFT) or mach.shift
    k = ev.key
    ch = None
    if k == pygame.K_SPACE: ch = ' '
    elif k == pygame.K_RETURN: ch = '\r'
    elif k == pygame.K_TAB: ch = '\t'
    elif k == pygame.K_BACKSPACE: ch = '\x08'
    elif k in _ALPHA:
        c = chr(k)
        upper = shift != mach.caps
        ch = c.upper() if upper else c.lower()
    elif k in _DIGIT:
        ch = _DIGIT[k] if shift else chr(k - pygame.K_1 + ord('1'))
    elif k in _SHIFT_MAP:
        ch = _SHIFT_MAP[k] if shift else _NORMAL_MAP[k]
    if mach.ctrl and k in _ALPHA:
        ch = chr(k - pygame.K_a + 1)
    asc = ord(ch) if ch is not None else 0
    return sc, asc


def find_files(base):
    def pick(*names):
        for n in names:
            p = os.path.join(base, n)
            if os.path.exists(p): return p
        return names[0] if names else None
    bios = pick("pc102782.bin")
    disks = []
    for d in ("DISK01.IMG","DISK02.IMG"):
        p = os.path.join(base, d)
        if os.path.exists(p): disks.append(p)
    return {
        "bios": bios,
        "f6": pick("basicc11.f6"),
        "f8": pick("basicc11.f8"),
        "fa": pick("basicc11.fa"),
        "fc": pick("basicc11.fc"),
        "disks": disks,
    }


def pick_disk_file(base):
    root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
    path = filedialog.askopenfilename(
        initialdir=base,
        title="Load disk image",
        filetypes=[("Disk images","*.img *.ima *.vfd"), ("All files","*.*")],
    )
    root.destroy()
    return path or None


def draw_button_bar(screen, font, hover_idx):
    bar_rect = pygame.Rect(0, 0, screen.get_width(), BUTTON_BAR_H)
    pygame.draw.rect(screen, (18, 18, 28), bar_rect)
    pygame.draw.line(screen, (60, 60, 80), (0, BUTTON_BAR_H-1), (screen.get_width(), BUTTON_BAR_H-1))
    rects = []
    x = 8; y = 5; bw = 110; bh = BUTTON_BAR_H - 10
    for label, idx in BUTTONS:
        rect = pygame.Rect(x, y, bw, bh)
        col = (70, 110, 200) if hover_idx == idx else (50, 60, 90)
        pygame.draw.rect(screen, col, rect, border_radius=4)
        ts = font.render(label, True, (230, 230, 240))
        screen.blit(ts, (x + (bw - ts.get_width())//2, y + (bh - ts.get_height())//2))
        rects.append((rect, idx))
        x += bw + 8
    hint = font.render("F2: load disk   Ctrl+Alt+Esc: quit", True, (160, 160, 180))
    screen.blit(hint, (x + 12, y + (bh - hint.get_height())//2))
    return rects


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    files = find_files(base)
    mach = Machine(files["disks"])
    mach.load_roms(files["bios"], files["f6"], files["f8"], files["fa"], files["fc"])
    mach.reset()
    mach.init_display(BUTTON_BAR_H)

    cpu = mach.cpu
    clock = pygame.time.Clock()
    ui_font = pygame.font.SysFont("Arial,DejaVu Sans,Helvetica", 13, bold=True)
    running = True
    IPPF = 3_000_000          # instructions per frame
    target_dt = 1.0 / 60.0    # aim for 60 fps
    perf_timer = time.perf_counter()
    perf_count = 0
    perf_ips = 0.0

    while running:
        budget = IPPF
        ran = 0
        # run CPU until budget exhausted or it blocks / halts / faults
        while budget > 0 and not cpu.halted and not cpu.blocked and not cpu.faulted:
            try:
                cpu.step()
            except Exception as e:
                cpu.faulted = True
                msg = "%s @ %04X:%04X" % (e, cpu.sregs[1], (cpu.ip - 1) & 0xFFFF)
                print("CPU fault:", msg)
                mach.fault_msg = msg
                break
            ran += 1
            budget -= 1

        perf_count += ran

        mouse = pygame.mouse.get_pos()
        hover_idx = -1
        if mouse[1] < BUTTON_BAR_H:
            for rect, idx in BUTTONS:
                if pygame.Rect(8 + idx*(118), 5, 110, BUTTON_BAR_H-10).collidepoint(mouse):
                    hover_idx = idx

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 and ev.pos[1] < BUTTON_BAR_H:
                for rect, idx in draw_button_bar(mach.screen, ui_font, hover_idx):
                    if rect.collidepoint(ev.pos):
                        if idx == 0:   # Load Disk
                            path = pick_disk_file(base)
                            if path:
                                mach.insert_disk(0, path)
                                mach.boot()
                        elif idx == 1:   # Reset
                            mach.boot()
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE and (ev.mod & pygame.KMOD_ALT):
                    running = False; continue
                if ev.key == pygame.K_F2:
                    path = pick_disk_file(base)
                    if path:
                        mach.insert_disk(0, path)
                        mach.boot()
                if ev.key in (pygame.K_LSHIFT, pygame.K_RSHIFT): mach.shift = True
                if ev.key in (pygame.K_LCTRL, pygame.K_RCTRL): mach.ctrl = True
                if ev.key == pygame.K_LALT: mach.alt = True
                if ev.key == pygame.K_CAPSLOCK: mach.caps = not mach.caps
                if ev.key == pygame.K_NUMLOCK: mach.num = not mach.num
                if ev.key == pygame.K_F12:
                    print("AX=%04X BX=%04X CX=%04X DX=%04X SI=%04X DI=%04X BP=%04X SP=%04X"
                          % (cpu.regs[0],cpu.regs[3],cpu.regs[1],cpu.regs[2],
                             cpu.regs[6],cpu.regs[7],cpu.regs[5],cpu.regs[4]))
                sc, asc = ascii_for(ev, mach)
                if sc is not None:
                    mach.keyq.append((sc, asc))
                    if cpu.blocked and (asc not in (0,) or ev.key not in _ALPHA):
                        cpu.blocked = False
            elif ev.type == pygame.KEYUP:
                if ev.key in (pygame.K_LSHIFT, pygame.K_RSHIFT): mach.shift = False
                if ev.key in (pygame.K_LCTRL, pygame.K_RCTRL): mach.ctrl = False
                if ev.key == pygame.K_LALT: mach.alt = False

        if cpu.blocked and mach.keyq:
            cpu.blocked = False

        mach.ticks = (mach.ticks + 1) & 0xFFFFFFFF
        mach.render()
        draw_button_bar(mach.screen, ui_font, hover_idx)
        if perf_ips:
            ts = ui_font.render("%.1f MIPS  IPPF=%d" % (perf_ips, IPPF), True, (180,180,200))
            mach.screen.blit(ts, (mach.screen.get_width() - ts.get_width() - 8, BUTTON_BAR_H - 22))
        pygame.display.flip()

        now = time.perf_counter()
        if now - perf_timer >= 1.0:
            perf_ips = perf_count / 1_000_000.0 / (now - perf_timer)
            perf_count = 0
            perf_timer = now

        try:
            clock.tick(60)
        except Exception:
            pass

    pygame.quit()


if __name__ == "__main__":
    main()