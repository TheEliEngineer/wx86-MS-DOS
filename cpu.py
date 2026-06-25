# cpu.py - Intel 8086/8088 CPU core for the wx86 emulator
# Implements the original 8086/8088 instruction set.

# 16-bit general register order (used by ModR/M 'reg' field, word ops)
AX, CX, DX, BX, SP, BP, SI, DI = range(8)
# 8-bit register order
AL, CL, DL, BL, AH, CH, DH, BH = range(8)
# Segment register order (for ModR/M sreg ops): ES,CS,SS,DS
ES, CS, SS, DS = range(4)

# Flag bit positions (for get/set FLAGS word)
F_CF = 0x0001
F_PF = 0x0004
F_AF = 0x0010
F_ZF = 0x0040
F_SF = 0x0080
F_TF = 0x0100
F_IF = 0x0200
F_DF = 0x0400
F_OF = 0x0800

PARITY = [bin(i).count("1") % 2 == 0 for i in range(256)]


def s8(v):  return ((v & 0xFF) ^ 0x80) - 0x80
def s16(v): return ((v & 0xFFFF) ^ 0x8000) - 0x8000
def s32(v): return ((v & 0xFFFFFFFF) ^ 0x80000000) - 0x80000000


class Cpu8086:
    def __init__(self, machine):
        self.m = machine
        self.regs = [0] * 8            # AX,CX,DX,BX,SP,BP,SI,DI
        self.sregs = [0] * 4           # ES,CS,SS,DS
        self.ip = 0
        self.flags = 0                 # packed FLAGS word
        self.halted = False
        self.blocked = False
        self.faulted = False
        self.instr_count = 0
        # current prefixes for the instruction being decoded
        self.seg_override = None       # index into sregs, or None
        self.rep_prefix = None         # None, 0xF2, 0xF3
        self.io_wait = 0               # cycles to wait (for FPS pacing handled by machine)

    # ---------- register access ----------
    def r16(self, i): return self.regs[i]
    def w16(self, i, v): self.regs[i] = v & 0xFFFF
    def r8(self, i):
        r = self.regs[i & 3]
        return r & 0xFF if (i & 4) == 0 else (r >> 8) & 0xFF
    def w8(self, i, v):
        v &= 0xFF
        idx = i & 3
        if i & 4:
            self.regs[idx] = (self.regs[idx] & 0x00FF) | (v << 8)
        else:
            self.regs[idx] = (self.regs[idx] & 0xFF00) | v

    # ---------- flags ----------
    def set_flag(self, mask, cond):
        if cond: self.flags |= mask
        else: self.flags &= ~mask
    @property
    def CF(self): return bool(self.flags & F_CF)
    @property
    def ZF(self): return bool(self.flags & F_ZF)
    @property
    def SF(self): return bool(self.flags & F_SF)
    @property
    def OF(self): return bool(self.flags & F_OF)
    @property
    def PF(self): return bool(self.flags & F_PF)
    @property
    def AF(self): return bool(self.flags & F_AF)
    @property
    def DF(self): return bool(self.flags & F_DF)
    @property
    def IF(self): return bool(self.flags & F_IF)

    def set_szp8(self, r):
        r &= 0xFF
        self.set_flag(F_ZF, r == 0)
        self.set_flag(F_SF, (r & 0x80) != 0)
        self.set_flag(F_PF, PARITY[r])
    def set_szp16(self, r):
        r &= 0xFFFF
        self.set_flag(F_ZF, r == 0)
        self.set_flag(F_SF, (r & 0x8000) != 0)
        self.set_flag(F_PF, PARITY[r & 0xFF])

    def flags_add(self, a, b, width):
        res = (a + b) & ((1 << width) - 1)
        msb = 1 << (width - 1)
        self.set_flag(F_CF, (a + b) > ((1 << width) - 1))
        self.set_flag(F_AF, ((a ^ b ^ res) & 0x10) != 0)
        self.set_flag(F_OF, (~(a ^ b) & (a ^ res) & msb) != 0)
        if width == 8: self.set_szp8(res)
        else: self.set_szp16(res)
        return res
    def flags_sub(self, a, b, width):
        res = (a - b) & ((1 << width) - 1)
        msb = 1 << (width - 1)
        self.set_flag(F_CF, a < b)
        self.set_flag(F_AF, ((a ^ b ^ res) & 0x10) != 0)
        self.set_flag(F_OF, ((a ^ b) & (a ^ res) & msb) != 0)
        if width == 8: self.set_szp8(res)
        else: self.set_szp16(res)
        return res
    def flags_logic(self, res, width):
        res &= (1 << width) - 1
        self.set_flag(F_CF, False)
        self.set_flag(F_OF, False)
        self.set_flag(F_AF, False)
        if width == 8: self.set_szp8(res)
        else: self.set_szp16(res)
        return res

    # ---------- memory access ----------
    def lin(self, seg, off):
        return ((self.sregs[seg] << 4) + (off & 0xFFFF)) & 0xFFFFF
    def phys(self, seg, off):  # alias
        return ((self.sregs[seg] << 4) + (off & 0xFFFF)) & 0xFFFFF

    def rd8(self, seg, off): return self.m.rb(self.lin(seg, off))
    def rd16(self, seg, off):
        p = self.lin(seg, off)
        return self.m.rb(p) | (self.m.rb((p + 1) & 0xFFFFF) << 8)
    def wr8(self, seg, off, v): self.m.wb(self.lin(seg, off), v & 0xFF)
    def wr16(self, seg, off, v):
        p = self.lin(seg, off)
        self.m.wb(p, v & 0xFF)
        self.m.wb((p + 1) & 0xFFFFF, (v >> 8) & 0xFF)

    # ---------- code fetch ----------
    def fetch8(self):
        v = self.m.rb((self.sregs[CS] << 4) + self.ip)
        self.ip = (self.ip + 1) & 0xFFFF
        return v
    def fetch16(self):
        lo = self.fetch8(); hi = self.fetch8(); return lo | (hi << 8)
    def fetchs8(self): return s8(self.fetch8())
    def fetchs16(self): return s16(self.fetch16())

    # ---------- ModR/M ----------
    def decode_modrm(self):
        mod = self.fetch8()
        mod_field = (mod >> 6) & 3
        reg = (mod >> 3) & 7
        rm = mod & 7
        ea = None
        rm_is_reg = False
        reg_is_mem = False
        default_seg = DS
        if mod_field == 3:
            rm_is_reg = True
            ea = rm           # register index for the r/m operand
        else:
            disp = 0
            if mod_field == 0 and rm == 6:
                disp = self.fetch16()
            else:
                if rm == 0 or rm == 1 or rm == 4 or rm == 5 or rm == 7:
                    pass  # uses BX/SI/DI - default DS
                if rm == 0: off = self.regs[BX] + self.regs[SI]
                elif rm == 1: off = self.regs[BX] + self.regs[DI]
                elif rm == 2: off = self.regs[BP] + self.regs[SI]; default_seg = SS
                elif rm == 3: off = self.regs[BP] + self.regs[DI]; default_seg = SS
                elif rm == 4: off = self.regs[SI]
                elif rm == 5: off = self.regs[DI]
                elif rm == 6: off = self.regs[BP]; default_seg = SS
                elif rm == 7: off = self.regs[BX]
                if mod_field == 1: disp = self.fetchs8()
                elif mod_field == 2: disp = self.fetchs16()
                disp &= 0xFFFF
                off = (off + disp) & 0xFFFF
                disp = off
            seg = self.seg_override if self.seg_override is not None else default_seg
            ea = (seg, disp & 0xFFFF)
        return reg, rm_is_reg, ea

    def seg_for_ea(self, ea, default=DS):
        if self.seg_override is not None:
            return self.seg_override
        return default

    def get_rm8(self, rm_is_reg, ea):
        if rm_is_reg: return self.r8(ea)
        return self.rd8(ea[0], ea[1])
    def set_rm8(self, rm_is_reg, ea, v):
        if rm_is_reg: self.w8(ea, v)
        else: self.wr8(ea[0], ea[1], v)
    def get_rm16(self, rm_is_reg, ea):
        if rm_is_reg: return self.r16(ea)
        return self.rd16(ea[0], ea[1])
    def set_rm16(self, rm_is_reg, ea, v):
        if rm_is_reg: self.w16(ea, v)
        else: self.wr16(ea[0], ea[1], v)

    # ---------- stack ----------
    def push16(self, v):
        self.regs[SP] = (self.regs[SP] - 2) & 0xFFFF
        self.wr16(SS, self.regs[SP], v)
    def pop16(self):
        v = self.rd16(SS, self.regs[SP])
        self.regs[SP] = (self.regs[SP] + 2) & 0xFFFF
        return v

    # ---------- segmented addresses for jumps ----------
    def near_jmp(self, off): self.ip = (off & 0xFFFF)
    def rel8(self):
        d = self.fetchs8()
        self.ip = (self.ip + d) & 0xFFFF
    def rel16(self):
        d = self.fetchs16()
        self.ip = (self.ip + d) & 0xFFFF

    # ---------- flags helpers ----------
    def get_flags(self): return self.flags & 0x0FD7 | 0xF002  # reserved bits set as on 8086
    def set_flags(self, v):
        self.flags = v & 0x0FD5

    def cond(self, c):
        c &= 0x0F
        if c == 0x0: return self.OF                          # JO
        if c == 0x1: return not self.OF                      # JNO
        if c == 0x2: return self.CF                          # JB/JC
        if c == 0x3: return not self.CF                       # JNB/JNC/JAE
        if c == 0x4: return self.ZF                           # JE/JZ
        if c == 0x5: return not self.ZF                       # JNE/JNZ
        if c == 0x6: return self.CF or self.ZF                # JBE
        if c == 0x7: return not (self.CF or self.ZF)           # JA/JNBE
        if c == 0x8: return self.SF                           # JS
        if c == 0x9: return not self.SF                       # JNS
        if c == 0xA: return self.PF                           # JP/JPE
        if c == 0xB: return not self.PF                       # JNP/JPO
        if c == 0xC: return self.SF != self.OF                # JL
        if c == 0xD: return self.SF == self.OF                # JGE/JNL
        if c == 0xE: return (self.ZF or (self.SF != self.OF))  # JLE
        if c == 0xF: return not (self.ZF or (self.SF != self.OF))  # JG/JNLE
        return False

    # ===================================================================
    #  main step
    # ===================================================================
    def step(self):
        if self.halted: return
        # reset prefixes
        self.seg_override = None
        self.rep_prefix = None
        # consume prefixes
        while True:
            op = self.fetch8()
            if op == 0xF0:           # LOCK: ignore
                continue
            if op in (0xF2, 0xF3):   # REPNE / REP
                self.rep_prefix = op
                # REP only meaningful for string ops; prefix repeats until handled
                self._exec_string(op)
                return
            if op in (0x26, 0x2E, 0x36, 0x3E):
                self.seg_override = {0x26: ES, 0x2E: CS, 0x36: SS, 0x3E: DS}[op]
                continue
            break
        self.instr_count += 1
        self.exec_op(op)

    def _exec_string(self, rep):
        # handle a single rep-prefixed string instruction (opcode follows prefix)
        op = self.fetch8()
        self.instr_count += 1
        self.rep_prefix = rep
        # non-string instruction with REP prefix = treat as NOP prefix; just execute once.
        if op not in (0xA4,0xA5,0xA6,0xA7,0xAA,0xAB,0xAC,0xAD,0xAE,0xAF):
            # REP before non-string: behave as if prefix absent (execute op once)
            self.rep_prefix = None
            self.exec_op(op)
            return
        do_rep = rep == 0xF3  # REPE/REP. For 0xF2 = REPNE.
        # determine if it's a comparison-class string (CMPS/SCAS) which use ZF
        is_cmp = op in (0xA6,0xA7,0xAE,0xAF)
        first = True
        while first or self.regs[CX] != 0:
            first = False
            if is_cmp:
                if do_rep and self.ZF: break       # REPE: stop when ZF=0 (not equal). actually REPE repeats while equal.
                if (not do_rep) and (not self.ZF): break
            self.exec_string_op(op)
            self.regs[CX] = (self.regs[CX] - 1) & 0xFFFF
            if not is_cmp and self.regs[CX] == 0: break
            if not do_rep and self.rep_prefix == 0xF2:
                pass
            # safety for compares: stop if CX becomes 0
            if is_cmp and self.regs[CX] == 0: break

    def exec_string_op(self, op):
        delta_word = op in (0xA5,0xA7,0xAD,0xAF)
        delta = -(2 if delta_word else 1) if self.DF else (2 if delta_word else 1)
        si_seg = self.seg_override if self.seg_override is not None else DS
        if op == 0xA4:  # MOVSB
            v = self.rd8(si_seg, self.regs[SI]); self.wr8(ES, self.regs[DI], v)
        elif op == 0xA5:  # MOVSW
            v = self.rd16(si_seg, self.regs[SI]); self.wr16(ES, self.regs[DI], v)
        elif op == 0xA6:  # CMPSB
            a = self.rd8(si_seg, self.regs[SI]); b = self.rd8(ES, self.regs[DI]); self.flags_sub(a, b, 8)
        elif op == 0xA7:  # CMPSW
            a = self.rd16(si_seg, self.regs[SI]); b = self.rd16(ES, self.regs[DI]); self.flags_sub(a, b, 16)
        elif op == 0xAA:  # STOSB
            self.wr8(ES, self.regs[DI], self.r8(AL))
        elif op == 0xAB:  # STOSW
            self.wr16(ES, self.regs[DI], self.regs[AX])
        elif op == 0xAC:  # LODSB
            self.w8(AL, self.rd8(si_seg, self.regs[SI]))
        elif op == 0xAD:  # LODSW
            self.w16(AX, self.rd16(si_seg, self.regs[SI]))
        elif op == 0xAE:  # SCASB
            a = self.r8(AL); b = self.rd8(ES, self.regs[DI]); self.flags_sub(a, b, 8)
        elif op == 0xAF:  # SCASW
            a = self.regs[AX]; b = self.rd16(ES, self.regs[DI]); self.flags_sub(a, b, 16)
        step = 2 if delta_word else 1
        uses_si = op in (0xA4,0xA5,0xA6,0xA7,0xAC,0xAD)   # MOVS/CMPS/LODS
        uses_di = op in (0xA4,0xA5,0xA6,0xA7,0xAA,0xAB,0xAE,0xAF)  # MOVS/CMPS/STOS/SCAS
        if uses_si:
            self.regs[SI] = (self.regs[SI] + delta) & 0xFFFF
        if uses_di:
            self.regs[DI] = (self.regs[DI] + delta) & 0xFFFF

    # ===================================================================
    def exec_op(self, op):
        m = self.m
        r8 = self.r8; w8 = self.w8; r16 = self.r16; w16 = self.w16

        # ---- group of simple single-byte ops & register-inc/dec ----
        if 0x00 <= op <= 0x05:        # ADD
            self._alu_op(op, "add")
        elif 0x08 <= op <= 0x0D:      # OR
            self._alu_op(op, "or")
        elif 0x10 <= op <= 0x15:      # ADC
            self._alu_op(op, "adc")
        elif 0x18 <= op <= 0x1D:      # SBB
            self._alu_op(op, "sbb")
        elif 0x20 <= op <= 0x25:      # AND
            self._alu_op(op, "and")
        elif 0x28 <= op <= 0x2D:      # SUB
            self._alu_op(op, "sub")
        elif 0x30 <= op <= 0x35:      # XOR
            self._alu_op(op, "xor")
        elif op in (0x06, 0x0E, 0x16, 0x1E):     # PUSH sreg (ES/CS/SS/DS)
            self.push16(self.sregs[{0x06:ES,0x0E:CS,0x16:SS,0x1E:DS}[op]])
        elif op == 0x86 or op == 0x87:          # XCHG r/m,r
            reg, rm_is_reg, ea = self.decode_modrm()
            if op == 0x87:
                a = self.r16(reg); self.w16(reg, self.get_rm16(rm_is_reg, ea)); self.set_rm16(rm_is_reg, ea, a)
            else:
                a = self.r8(reg); self.w8(reg, self.get_rm8(rm_is_reg, ea)); self.set_rm8(rm_is_reg, ea, a)
        elif op in (0x07, 0x17, 0x1F):     # POP sreg (ES/SS/DS)
            self.sregs[{0x07:ES,0x17:SS,0x1F:DS}[op]] = self.pop16()
        elif op == 0x0F:                       # POP CS (rare 8086 opcode)
            self.sregs[CS] = self.pop16()
        elif 0x38 <= op <= 0x3D:      # CMP
            self._alu_op(op, "cmp")
        elif op == 0x27:  # DAA
            self._daa()
        elif op == 0x2F:  # DAS
            self._das()
        elif op == 0x37:  # AAA
            self._aaa()
        elif op == 0x3F:  # AAS
            self._aas()
        elif 0x40 <= op <= 0x47:      # INC r16
            i = op - 0x40
            w16(i, self.flags_inc_dec(self.r16(i), 1, 16))
        elif 0x48 <= op <= 0x4F:      # DEC r16
            i = op - 0x48
            w16(i, self.flags_inc_dec(self.r16(i), -1, 16))
        elif 0x50 <= op <= 0x57:      # PUSH r16
            self.push16(self.r16(op - 0x50))
        elif 0x58 <= op <= 0x5F:      # POP r16
            self.w16(op - 0x58, self.pop16())
        elif 0x70 <= op <= 0x7F:      # Jcc rel8
            if self.cond(op & 0x0F):
                self.rel8()
            else:
                self.fetch8()
        elif op == 0x80 or op == 0x81 or op == 0x83:  # group1 r/m,imm
            self._grp1_imm(op)
        elif op == 0x82:  # (alias of 0x80 on 8086) signed imm8 group
            self._grp1_imm(0x80)
        elif 0x88 <= op <= 0x8B:      # MOV r/m,r and r,r/m
            self._mov_rm_reg(op)
        elif op == 0x8C:              # MOV r/m,sreg
            self._mov_sreg_to_rm()
        elif op == 0x8D:              # LEA
            reg, rm_is_reg, ea = self.decode_modrm()
            if ea is not None: w16(reg, ea[1])  # offset only
        elif op == 0x8E:              # MOV sreg,r/m
            self._mov_rm_to_sreg()
        elif op == 0x8F:              # POP r/m16 (reg=0 only meaningful)
            reg, rm_is_reg, ea = self.decode_modrm()
            self.set_rm16(rm_is_reg, ea, self.pop16())
        elif 0x90 <= op <= 0x97:      # XCHG AX,r16 (0x90 = NOP)
            i = op - 0x90
            if i != 0:
                t = self.regs[AX]; self.w16(AX, self.r16(i)); self.w16(i, t)
        elif op == 0x98:  # CBW
            self.w16(AX, s8(self.r8(AL)) & 0xFFFF)
        elif op == 0x99:  # CWD
            self.w16(DX, 0xFFFF if (self.regs[AX] & 0x8000) else 0)
        elif op == 0x9A:  # CALL far ptr16:16
            new_ip = self.fetch16(); new_cs = self.fetch16()
            self.push16(self.sregs[CS]); self.push16(self.ip)
            self.sregs[CS] = new_cs; self.ip = new_ip
        elif op == 0x9B:  # WAIT
            pass
        elif op == 0x9C:  # PUSHF
            self.push16(self.get_flags())
        elif op == 0x9D:  # POPF
            self.set_flags(self.pop16())
        elif op == 0x9E:  # SAHF
            self.set_flags((self.flags & 0xFF00) | self.r8(AH))
        elif op == 0x9F:  # LAHF
            self.w8(AH, self.flags & 0xFF)
        elif 0xA0 <= op <= 0xA3:     # MOV AL/AX,[moffs] and [moffs],AL/AX
            addr = self.fetch16()
            seg = self.seg_override if self.seg_override is not None else DS
            if op == 0xA0: w8(AL, self.rd8(seg, addr))
            elif op == 0xA1: w16(AX, self.rd16(seg, addr))
            elif op == 0xA2: self.wr8(seg, addr, r8(AL))
            elif op == 0xA3: self.wr16(seg, addr, r16(AX))
        elif op == 0xA8:  # TEST AL,imm8
            self.flags_logic(self.r8(AL) & self.fetch8(), 8)
        elif op == 0xA9:  # TEST AX,imm16
            self.flags_logic(self.r16(AX) & self.fetch16(), 16)
        elif op in (0xA4,0xA5,0xA6,0xA7,0xAA,0xAB,0xAC,0xAD,0xAE,0xAF):
            # string ops without REP prefix execute exactly once
            was_rep = self.rep_prefix
            self.rep_prefix = None
            self.exec_string_op(op)
        elif 0xB0 <= op <= 0xB7:     # MOV r8,imm8
            w8(op - 0xB0, self.fetch8())
        elif 0xB8 <= op <= 0xBF:     # MOV r16,imm16
            w16(op - 0xB8, self.fetch16())
        elif op == 0xC2:              # RETN imm16
            n = self.fetch16(); self.ip = self.pop16(); self.regs[SP] = (self.regs[SP] + n) & 0xFFFF
        elif op == 0xC3:              # RETN
            self.ip = self.pop16()
        elif op == 0xCA:              # RETF imm16
            n = self.fetch16(); self.ip = self.pop16(); self.sregs[CS] = self.pop16(); self.regs[SP] = (self.regs[SP] + n) & 0xFFFF
        elif op == 0xCB:              # RETF
            self.ip = self.pop16(); self.sregs[CS] = self.pop16()
        elif op == 0xC4:              # LES r16,m
            self._load_far(ES)
        elif op == 0xC5:              # LDS r16,m
            self._load_far(DS)
        elif op == 0xC6:              # MOV r/m8,imm8
            self.set_rm8(*self.decode_modrm()[1:], self.fetch8())
        elif op == 0xC7:              # MOV r/m16,imm16
            self.set_rm16(*self.decode_modrm()[1:], self.fetch16())
        elif op == 0xCB+0 and False:
            pass
        elif op == 0xD0 or op == 0xD1 or op == 0xD2 or op == 0xD3:  # shift/rotate group
            self._shift_group(op)
        elif op == 0xD4:  # AAM
            base = self.fetch8()
            a = self.r8(AL)
            self.w8(AH, (a // (base or 1)) & 0xFF)
            self.w8(AL, (a % (base or 1)) & 0xFF)
            self.set_szp8(self.r8(AL))
        elif op == 0xD5:  # AAD
            base = self.fetch8()
            a = (self.r8(AH) * (base or 1) + self.r8(AL)) & 0xFF
            self.w16(AX, a)
            self.set_szp8(a)
        elif op == 0xD7:  # XLAT
            seg = self.seg_override if self.seg_override is not None else DS
            self.w8(AL, self.rd8(seg, (self.regs[BX] + self.r8(AL)) & 0xFFFF))
        elif 0xD8 <= op <= 0xDF:     # ESC (FPU) - ignore operand, read modrm
            self.decode_modrm()
        elif op == 0xE0:  # LOOPNE
            d = self.fetchs8()
            self.regs[CX] = (self.regs[CX] - 1) & 0xFFFF
            if self.regs[CX] != 0 and not self.ZF: self.ip = (self.ip + d) & 0xFFFF
        elif op == 0xE1:  # LOOPE
            d = self.fetchs8()
            self.regs[CX] = (self.regs[CX] - 1) & 0xFFFF
            if self.regs[CX] != 0 and self.ZF: self.ip = (self.ip + d) & 0xFFFF
        elif op == 0xE2:  # LOOP
            d = self.fetchs8()
            self.regs[CX] = (self.regs[CX] - 1) & 0xFFFF
            if self.regs[CX] != 0: self.ip = (self.ip + d) & 0xFFFF
        elif op == 0xE3:  # JCXZ
            d = self.fetchs8()
            if self.regs[CX] == 0: self.ip = (self.ip + d) & 0xFFFF
        elif op == 0xE4:  # IN AL,imm8
            self.w8(AL, m.in_port(self.fetch8()))
        elif op == 0xE5:  # IN AX,imm8
            self.w16(AX, m.in_port16(self.fetch8()))
        elif op == 0xE6:  # OUT imm8,AL
            m.out_port(self.fetch8(), self.r8(AL))
        elif op == 0xE7:  # OUT imm8,AX
            m.out_port16(self.fetch8(), self.r16(AX))
        elif op == 0xE8:  # CALL rel16
            d = self.fetchs16()
            self.push16(self.ip)
            self.ip = (self.ip + d) & 0xFFFF
        elif op == 0xE9:  # JMP rel16
            self.rel16()
        elif op == 0xEA:  # JMP far ptr16:16
            new_ip = self.fetch16(); new_cs = self.fetch16()
            self.ip = new_ip; self.sregs[CS] = new_cs
        elif op == 0xEB:  # JMP rel8
            self.rel8()
        elif op == 0xEC:  # IN AL,DX
            self.w8(AL, m.in_port(self.regs[DX] & 0xFF))
        elif op == 0xED:  # IN AX,DX
            self.w16(AX, m.in_port16(self.regs[DX] & 0xFF))
        elif op == 0xEE:  # OUT DX,AL
            m.out_port(self.regs[DX] & 0xFF, self.r8(AL))
        elif op == 0xEF:  # OUT DX,AX
            m.out_port16(self.regs[DX] & 0xFF, self.r16(AX))
        elif op == 0xF4:  # HLT
            self.halted = True
        elif op == 0xF5:  # CMC
            self.set_flag(F_CF, not self.CF)
        elif op == 0xF6 or op == 0xF7:  # group3
            self._grp3(op)
        elif op == 0xF8:  self.set_flag(F_CF, False)   # CLC
        elif op == 0xF9:  self.set_flag(F_CF, True)    # STC
        elif op == 0xFA:  self.set_flag(F_IF, False)   # CLI
        elif op == 0xFB:  self.set_flag(F_IF, True)    # STI
        elif op == 0xFC:  self.set_flag(F_DF, False)   # CLD
        elif op == 0xFD:  self.set_flag(F_DF, True)    # STD
        elif op == 0xFE:  # INC/DEC byte r/m
            reg, rm_is_reg, ea = self.decode_modrm()
            v = self.get_rm8(rm_is_reg, ea)
            if reg & 1: self.set_rm8(rm_is_reg, ea, self.flags_inc_dec(v, -1, 8))
            else:      self.set_rm8(rm_is_reg, ea, self.flags_inc_dec(v, 1, 8))
        elif op == 0xFF:  # group5
            self._grp5()
        elif op == 0xCC:  # INT 3
            self._do_int(3)
        elif op == 0xCD:  # INT imm8
            n = self.fetch8()
            self._do_int(n)
        elif op == 0xCE:  # INTO
            if self.OF: self._do_int(4)
        elif op == 0xCF:  # IRET
            self.ip = self.pop16()
            self.sregs[CS] = self.pop16()
            self.set_flags(self.pop16())
        elif op == 0xF1 or op == 0x62 or op == 0x63 or op == 0xF3+0 and False:
            pass
        else:
            raise NotImplementedError("Unknown opcode 0x%02X at %04X:%04X" % (op, self.sregs[CS], (self.ip - 1) & 0xFFFF))

    def _do_int(self, n):
        blocked = self.m.int_call(self, n)
        if blocked:
            # rewind to re-execute the INT so the main loop can pump events
            self.ip = (self.ip - 2) & 0xFFFF
            self.blocked = True

    # ---------- ALU ops 0x00..0x3D ----------
    def _alu_op(self, op, kind):
        sub = op & 7
        w = (op & 1) == 1            # word?
        # direction: d=0 r/m<-r ; d=1 r<-r/m   (for opcodes 0x00-0x05)
        # layout: op = base | (d<<1) | w  ->  d bit = bit1, w bit = bit0
        d = (op >> 1) & 1
        if sub == 4 or sub == 5:     # AL/AX, imm
            if w: imm = self.fetch16(); a = self.regs[AX]; res = self._apply(kind, a, imm, 16)
            else: imm = self.fetch8();  a = self.r8(AL);    res = self._apply(kind, a, imm, 8)
            if kind != "cmp": (self.w16(AX, res) if w else self.w8(AL, res))
            return
        reg, rm_is_reg, ea = self.decode_modrm()
        if w:
            if d == 0:  # r/m , r
                a = self.get_rm16(rm_is_reg, ea); b = self.r16(reg)
                res = self._apply(kind, a, b, 16)
                if kind != "cmp": self.set_rm16(rm_is_reg, ea, res)
            else:
                a = self.r16(reg); b = self.get_rm16(rm_is_reg, ea)
                res = self._apply(kind, a, b, 16)
                if kind != "cmp": self.w16(reg, res)
        else:
            if d == 0:
                a = self.get_rm8(rm_is_reg, ea); b = self.r8(reg)
                res = self._apply(kind, a, b, 8)
                if kind != "cmp": self.set_rm8(rm_is_reg, ea, res)
            else:
                a = self.r8(reg); b = self.get_rm8(rm_is_reg, ea)
                res = self._apply(kind, a, b, 8)
                if kind != "cmp": self.w8(reg, res)

    def _apply(self, kind, a, b, width):
        if kind == "add": return self.flags_add(a, b, width)
        if kind == "adc":
            return self.flags_add(a, (b + (1 if self.CF else 0)) & ((1<<width)-1), width)
        if kind == "sub": return self.flags_sub(a, b, width)
        if kind == "sbb":
            return self.flags_sub(a, (b + (1 if self.CF else 0)) & ((1<<width)-1), width)
        if kind == "and": return self.flags_logic(a & b, width)
        if kind == "or":  return self.flags_logic(a | b, width)
        if kind == "xor": return self.flags_logic(a ^ b, width)
        if kind == "cmp": return self.flags_sub(a, b, width)

    def flags_inc_dec(self, a, by, width):
        # CF preserved
        res = (a + by) & ((1 << width) - 1)
        msb = 1 << (width - 1)
        self.set_flag(F_AF, ((a ^ (by if by > 0 else (-by & ((1<<width)-1))) ^ res) & 0x10) != 0)
        # overflow: a and result differ in sign, by has the changed sign
        # inc: a positive and res sign negative -> OF. dec: opposite.
        if by > 0:
            self.set_flag(F_OF, (a & msb) and not (res & msb))
        else:
            self.set_flag(F_OF, not (a & msb) and (res & msb))
        if width == 8: self.set_szp8(res)
        else: self.set_szp16(res)
        return res

    # ---------- group1 imm (0x80/0x81/0x83) ----------
    def _grp1_imm(self, op):
        reg, rm_is_reg, ea = self.decode_modrm()
        sub = reg
        w = (op & 1) == 1
        if op == 0x80:
            imm = self.fetch8(); width = 8; a = self.get_rm8(rm_is_reg, ea)
            full = imm
        elif op == 0x81:
            if w: imm = self.fetch16(); width = 16; a = self.get_rm16(rm_is_reg, ea)
            else: imm = self.fetch8();  width = 8; a = self.get_rm8(rm_is_reg, ea)
            full = imm
        else:  # 0x83 sign-extended imm8
            simm = self.fetchs8()
            if w: full = simm & 0xFFFF; width = 16; a = self.get_rm16(rm_is_reg, ea)
            else: full = simm & 0xFF; width = 8; a = self.get_rm8(rm_is_reg, ea)
        kind = ["add","or","adc","sbb","and","sub","xor","cmp"][sub]
        res = self._apply(kind, a, full, width)
        if kind != "cmp":
            if width == 8: self.set_rm8(rm_is_reg, ea, res)
            else: self.set_rm16(rm_is_reg, ea, res)

    # ---------- MOV r/m,r ; r,r/m ----------
    def _mov_rm_reg(self, op):
        w = (op & 1) == 1
        d = (op >> 1) & 1
        reg, rm_is_reg, ea = self.decode_modrm()
        if w:
            if d == 0: self.set_rm16(rm_is_reg, ea, self.r16(reg))
            else: self.w16(reg, self.get_rm16(rm_is_reg, ea))
        else:
            if d == 0: self.set_rm8(rm_is_reg, ea, self.r8(reg))
            else: self.w8(reg, self.get_rm8(rm_is_reg, ea))

    def _mov_sreg_to_rm(self):
        reg, rm_is_reg, ea = self.decode_modrm()
        self.set_rm16(rm_is_reg, ea, self.sregs[reg & 3])

    def _mov_rm_to_sreg(self):
        reg, rm_is_reg, ea = self.decode_modrm()
        self.sregs[reg & 3] = self.get_rm16(rm_is_reg, ea)

    def _load_far(self, sreg):
        reg, rm_is_reg, ea = self.decode_modrm()
        off = self.rd16(ea[0], ea[1]) if not rm_is_reg else 0
        seg = self.rd16(ea[0], (ea[1] + 2) & 0xFFFF) if not rm_is_reg else 0
        self.w16(reg, off)
        self.sregs[sreg] = seg

    # ---------- shift/rotate group ----------
    def _shift_group(self, op):
        reg, rm_is_reg, ea = self.decode_modrm()
        sub = reg & 7
        # count: v1 ->1, v2/d3 -> CL
        if op == 0xD0 or op == 0xD1:
            count = 1
        else:
            count = self.r8(CL)
        w = (op & 1) == 1
        if w: val = self.get_rm16(rm_is_reg, ea)
        else: val = self.get_rm8(rm_is_reg, ea)
        res = self._shift(sub, val, count, w)
        if w: self.set_rm16(rm_is_reg, ea, res)
        else: self.set_rm8(rm_is_reg, ea, res)

    def _shift(self, sub, val, count, w):
        size = 16 if w else 8
        mask = (1 << size) - 1
        msb = 1 << (size - 1)
        if count == 0:
            return val & mask
        is_shift = sub in (4, 5, 6, 7)
        for _ in range(count):
            if sub == 0:    # ROL
                cf = (val & msb) != 0
                val = ((val << 1) | (1 if cf else 0)) & mask
                self.set_flag(F_CF, cf)
            elif sub == 1:  # ROR
                cf = (val & 1) != 0
                val = (val >> 1) | (msb if cf else 0)
                self.set_flag(F_CF, cf)
            elif sub == 2:  # RCL
                cf = self.CF
                self.set_flag(F_CF, (val & msb) != 0)
                val = ((val << 1) | (1 if cf else 0)) & mask
            elif sub == 3:  # RCR
                cf = self.CF
                self.set_flag(F_CF, (val & 1) != 0)
                val = (val >> 1) | (msb if cf else 0)
            elif sub in (4, 6):  # SHL/SAL
                self.set_flag(F_CF, (val & msb) != 0)
                val = (val << 1) & mask
            elif sub == 5:  # SHR
                self.set_flag(F_CF, (val & 1) != 0)
                val = (val >> 1) & mask
            elif sub == 7:  # SAR
                self.set_flag(F_CF, (val & 1) != 0)
                val = ((val >> 1) | (val & msb)) & mask
        if is_shift:
            self.set_szp(val, w)
            if sub in (4, 6):
                self.set_flag(F_OF, self.CF != ((val & msb) != 0))
            elif sub == 5:
                self.set_flag(F_OF, (val ^ (val << 1)) & msb != 0)
            else:
                self.set_flag(F_OF, False)
        return val & mask

    def set_szp(self, v, w):
        if w: self.set_szp16(v)
        else: self.set_szp8(v)

    # ---------- group3 ----------
    def _grp3(self, op):
        reg, rm_is_reg, ea = self.decode_modrm()
        w = (op & 1) == 1
        if reg == 0 or reg == 1:    # TEST r/m,imm
            if w:
                imm = self.fetch16(); self.flags_logic(self.get_rm16(rm_is_reg, ea) & imm, 16)
            else:
                imm = self.fetch8();  self.flags_logic(self.get_rm8(rm_is_reg, ea) & imm, 8)
        elif reg == 2:             # NOT
            if w: self.set_rm16(rm_is_reg, ea, (~self.get_rm16(rm_is_reg, ea)) & 0xFFFF)
            else: self.set_rm8(rm_is_reg, ea, (~self.get_rm8(rm_is_reg, ea)) & 0xFF)
        elif reg == 3:             # NEG
            if w:
                v = self.get_rm16(rm_is_reg, ea); r = self.flags_sub(0, v, 16); self.set_rm16(rm_is_reg, ea, r)
            else:
                v = self.get_rm8(rm_is_reg, ea);  r = self.flags_sub(0, v, 8);  self.set_rm8(rm_is_reg, ea, r)
        elif reg == 4:             # MUL
            self._mul(rm_is_reg, ea, w, signed=False)
        elif reg == 5:             # IMUL
            self._mul(rm_is_reg, ea, w, signed=True)
        elif reg == 6:             # DIV
            self._div(rm_is_reg, ea, w, signed=False)
        elif reg == 7:             # IDIV
            self._div(rm_is_reg, ea, w, signed=True)

    def _mul(self, rm_is_reg, ea, w, signed):
        if w:
            operand = self.get_rm16(rm_is_reg, ea)
            a = s16(self.regs[AX]) if signed else self.regs[AX]
            b = s16(operand) if signed else operand
            res = a * b
            self.w16(AX, res & 0xFFFF); self.w16(DX, (res >> 16) & 0xFFFF)
            hi = (res >> 16) & 0xFFFF
            self.set_flag(F_CF, hi != 0)
            self.set_flag(F_OF, self.CF)
        else:
            operand = self.get_rm8(rm_is_reg, ea)
            a = s8(self.r8(AL)) if signed else self.r8(AL)
            b = s8(operand) if signed else operand
            res = a * b
            self.w16(AX, res & 0xFFFF)
            self.set_flag(F_CF, ((res >> 8) & 0xFF) != 0)
            self.set_flag(F_OF, self.CF)

    def _div(self, rm_is_reg, ea, w, signed):
        if w:
            divisor = self.get_rm16(rm_is_reg, ea)
            if divisor == 0:
                self.m.int_call(self, 0); return
            dividend = (self.regs[DX] << 16) | self.regs[AX]
            if signed:
                dividend = s32(dividend); divisor = s16(divisor)
            else:
                dividend &= 0xFFFFFFFF
            q = int(dividend / divisor) if signed else dividend // divisor
            r = dividend - q * divisor
            self.w16(AX, q & 0xFFFF); self.w16(DX, r & 0xFFFF)
        else:
            divisor = self.get_rm8(rm_is_reg, ea)
            if divisor == 0:
                self.m.int_call(self, 0); return
            dividend = self.regs[AX]
            if signed:
                dividend = s16(dividend)
            q = int(dividend / divisor) if signed else dividend // divisor
            r = dividend - q * divisor
            self.w8(AL, q & 0xFF); self.w8(AH, r & 0xFF)

    def _grp5(self):
        reg, rm_is_reg, ea = self.decode_modrm()
        sub = reg
        if sub == 0:    # INC r/m16
            v = self.get_rm16(rm_is_reg, ea)
            self.set_rm16(rm_is_reg, ea, self.flags_inc_dec(v, 1, 16))
        elif sub == 1:  # DEC r/m16
            v = self.get_rm16(rm_is_reg, ea)
            self.set_rm16(rm_is_reg, ea, self.flags_inc_dec(v, -1, 16))
        elif sub == 2:  # CALL near r/m
            tgt = self.get_rm16(rm_is_reg, ea)
            self.push16(self.ip)
            self.ip = tgt
        elif sub == 3:  # CALL far m
            off = self.rd16(ea[0], ea[1]); seg = self.rd16(ea[0], (ea[1]+2)&0xFFFF)
            self.push16(self.sregs[CS]); self.push16(self.ip)
            self.sregs[CS] = seg; self.ip = off
        elif sub == 4:  # JMP near r/m
            self.ip = self.get_rm16(rm_is_reg, ea)
        elif sub == 5:  # JMP far m
            off = self.rd16(ea[0], ea[1]); seg = self.rd16(ea[0], (ea[1]+2)&0xFFFF)
            self.sregs[CS] = seg; self.ip = off
        elif sub == 6:  # PUSH r/m16
            self.push16(self.get_rm16(rm_is_reg, ea))

    # ---------- BCD adjustments ----------
    def _daa(self):
        al = self.r8(AL); old_cf = self.CF
        if (al & 0x0F) > 9 or self.AF:
            al += 6; self.set_flag(F_CF, old_cf or True)
            self.flags ^= (self.flags ^ (0 if not (al>0xFF) else F_CF)) & 0  # noop
            self.set_flag(F_AF, True)
        else:
            self.set_flag(F_AF, False)
        if (self.r8(AL) >> 4) > 9 or self.CF:
            al = (al + 0x60) & 0xFF; self.set_flag(F_CF, True)
        else:
            self.set_flag(F_CF, False)
        self.w8(AL, al); self.set_szp8(al)
    def _das(self):
        al = self.r8(AL); old_cf = self.CF
        if (al & 0x0F) > 9 or self.AF:
            al -= 6; self.set_flag(F_AF, True)
        else:
            self.set_flag(F_AF, False)
        if al > 0x99 or old_cf:
            al = (al - 0x60) & 0xFF; self.set_flag(F_CF, True)
        else:
            self.set_flag(F_CF, False)
        self.w8(AL, al); self.set_szp8(al)
    def _aaa(self):
        al = self.r8(AL); ah = self.r8(AH)
        if (al & 0x0F) > 9 or self.AF:
            al = (al + 6) & 0xFF; ah = (ah + 1) & 0xFF
            self.set_flag(F_AF, True); self.set_flag(F_CF, True)
        else:
            self.set_flag(F_AF, False); self.set_flag(F_CF, False)
        self.w16(AX, ((ah << 8) | (al & 0x0F)))
    def _aas(self):
        al = self.r8(AL); ah = self.r8(AH)
        if (al & 0x0F) > 9 or self.AF:
            al = (al - 6) & 0xFF; ah = (ah - 1) & 0xFF
            self.set_flag(F_AF, True); self.set_flag(F_CF, True)
        else:
            self.set_flag(F_AF, False); self.set_flag(F_CF, False)
        self.w16(AX, ((ah << 8) | (al & 0x0F)))