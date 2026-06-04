from __future__ import annotations

from isa import Opcode

# Microinstruction format (32-bit word, bit 31 is unused):
# 30     fetch            IR <- MEM[PC], PC <- PC + 1
# 29..27 AR source        0 none, 1 PC, 2 RD, 3 RS1, 4 R15
# 26..25 DR source        0 none, 1 MEM[AR], 2 PC, 3 RS1
# 24     IR latch         IR <- DR
# 23     MEM write        MEM[AR] <- DR
# 22..21 register target  0 none, 1 RD, 2 R15
# 20..19 writeback source 0 none, 1 IMM, 2 DR, 3 ALU
# 18..17 ALU left source  0 none, 1 RS1, 2 R15
# 16..15 ALU right source 0 none, 1 RS2, 2 +1, 3 -1
# 14..11 ALU operation    0 opcode from IR, 1 ADD
# 10..9  PC source        0 none, 1 PC+1, 2 IMM, 3 DR
# 8      PC condition     0 always, 1 only if RS1 == 0
# 7      halt
# 6..5   next uPC mode    0 uPC+1, 1 fetch, 2 decode opcode
# 4..0   unused

AR_NONE = 0
AR_PC = 1
AR_RD = 2
AR_RS1 = 3
AR_R15 = 4

DR_NONE = 0
DR_MEM = 1
DR_PC = 2
DR_RS1 = 3

DST_NONE = 0
DST_RD = 1
DST_R15 = 2

WB_NONE = 0
WB_IMM = 1
WB_DR = 2
WB_ALU = 3

ALU_A_NONE = 0
ALU_A_RS1 = 1
ALU_A_R15 = 2

ALU_B_NONE = 0
ALU_B_RS2 = 1
ALU_B_ONE = 2
ALU_B_NEG_ONE = 3

ALU_FROM_OPCODE = 0
ALU_ADD = 1

PC_NONE = 0
PC_INC = 1
PC_IMM = 2
PC_DR = 3

NEXT_SEQ = 0
NEXT_FETCH = 1
NEXT_DECODE = 2
NEXT_MASK = 0b11
PC_MASK = 0b11
ALU_OP_MASK = 0xF
SRC_MASK = 0b11
AR_MASK = 0b111

NEXT_SHIFT = 5
HALT_SHIFT = 7
PC_ZERO_SHIFT = 8
PC_SHIFT = 9
ALU_OP_SHIFT = 11
ALU_B_SHIFT = 15
ALU_A_SHIFT = 17
WB_SHIFT = 19
DST_SHIFT = 21
MEM_WRITE_SHIFT = 23
IR_LATCH_SHIFT = 24
DR_SHIFT = 25
AR_SHIFT = 27
FETCH_SHIFT = 30

UADDR_FETCH = 0
MICROCODE_SIZE = 32

_AR_NAMES = ["-", "pc", "rd", "rs1", "r15"]
_DR_NAMES = ["-", "mem", "pc", "rs1"]
_DST_NAMES = ["-", "rd", "r15"]
_WB_NAMES = ["-", "imm", "dr", "alu"]
_ALU_A_NAMES = ["-", "rs1", "r15"]
_ALU_B_NAMES = ["-", "rs2", "1", "-1"]
_PC_NAMES = ["-", "inc", "imm", "dr"]
_NEXT_NAMES = ["seq", "fetch", "decode", "-"]


def field(word: int, shift: int, mask: int = 1) -> int:
    return (word >> shift) & mask


def encode(
    *,
    ar: int = AR_NONE,
    dr: int = DR_NONE,
    ir: int = 0,
    mem_write: int = 0,
    dst: int = DST_NONE,
    wb: int = WB_NONE,
    alu_a: int = ALU_A_NONE,
    alu_b: int = ALU_B_NONE,
    alu_op: int = ALU_FROM_OPCODE,
    pc: int = PC_NONE,
    pc_zero: int = 0,
    halt: int = 0,
    next_: int = NEXT_SEQ,
    fetch: int = 0,
) -> int:
    return (
        ((fetch & 1) << FETCH_SHIFT)
        | ((ar & AR_MASK) << AR_SHIFT)
        | ((dr & SRC_MASK) << DR_SHIFT)
        | ((ir & 1) << IR_LATCH_SHIFT)
        | ((mem_write & 1) << MEM_WRITE_SHIFT)
        | ((dst & SRC_MASK) << DST_SHIFT)
        | ((wb & SRC_MASK) << WB_SHIFT)
        | ((alu_a & SRC_MASK) << ALU_A_SHIFT)
        | ((alu_b & SRC_MASK) << ALU_B_SHIFT)
        | ((alu_op & ALU_OP_MASK) << ALU_OP_SHIFT)
        | ((pc & PC_MASK) << PC_SHIFT)
        | ((pc_zero & 1) << PC_ZERO_SHIFT)
        | ((halt & 1) << HALT_SHIFT)
        | ((next_ & NEXT_MASK) << NEXT_SHIFT)
    )


def signal_text(word: int, opcode: Opcode | None = None) -> str:
    signals: list[str] = []
    ar = field(word, AR_SHIFT, AR_MASK)
    dr = field(word, DR_SHIFT, SRC_MASK)
    dst = field(word, DST_SHIFT, SRC_MASK)
    wb = field(word, WB_SHIFT, SRC_MASK)
    pc = field(word, PC_SHIFT, PC_MASK)
    next_ = field(word, NEXT_SHIFT, NEXT_MASK)

    if field(word, FETCH_SHIFT):
        signals.append("fetch=mem[pc]->ir,pc+1")
    if ar:
        signals.append(f"ar={_AR_NAMES[ar]}")
    if dr:
        signals.append(f"dr={_DR_NAMES[dr]}")
    if field(word, IR_LATCH_SHIFT):
        signals.append("ir_latch")
    if field(word, MEM_WRITE_SHIFT):
        signals.append("mem_write")
    if dst:
        signals.append(f"reg={_DST_NAMES[dst]}<-{_WB_NAMES[wb]}")
    if wb == WB_ALU:
        alu = field(word, ALU_OP_SHIFT, ALU_OP_MASK)
        if alu == ALU_FROM_OPCODE:
            op_name = opcode.name.lower() if opcode is not None else "opcode"
        elif alu == ALU_ADD:
            op_name = "add"
        else:
            op_name = str(alu)
        a = _ALU_A_NAMES[field(word, ALU_A_SHIFT, SRC_MASK)]
        b = _ALU_B_NAMES[field(word, ALU_B_SHIFT, SRC_MASK)]
        signals.append(f"alu={op_name}({a},{b})")
    if pc:
        suffix = " if rs1==0" if field(word, PC_ZERO_SHIFT) else ""
        signals.append(f"pc={_PC_NAMES[pc]}{suffix}")
    if field(word, HALT_SHIFT):
        signals.append("halt")
    if next_ != NEXT_SEQ:
        signals.append(f"next={_NEXT_NAMES[next_]}")
    return ",".join(signals) or "-"


def _put(addr: int, name: str, word: int) -> None:
    _mrom[addr] = word
    _microcode_names[addr] = name


_mrom: list[int] = [0] * MICROCODE_SIZE
_microcode_names: list[str] = ["unused"] * MICROCODE_SIZE

# Fetch-decode cycle.
_put(0, "fetch_ir_pc_decode", encode(fetch=1, next_=NEXT_DECODE))

# Instruction microprograms.
_put(3, "nop", encode(next_=NEXT_FETCH))
_put(
    4,
    "alu_rd_rs1_rs2",
    encode(dst=DST_RD, wb=WB_ALU, alu_a=ALU_A_RS1, alu_b=ALU_B_RS2, next_=NEXT_FETCH),
)
_put(5, "ldi_rd_imm", encode(dst=DST_RD, wb=WB_IMM, next_=NEXT_FETCH))

_put(6, "ld_ar_rs1", encode(ar=AR_RS1))
_put(7, "ld_dr_mem", encode(dr=DR_MEM))
_put(8, "ld_rd_dr", encode(dst=DST_RD, wb=WB_DR, next_=NEXT_FETCH))

_put(9, "st_ar_rd", encode(ar=AR_RD))
_put(10, "st_dr_rs1", encode(dr=DR_RS1))
_put(11, "st_mem_dr", encode(mem_write=1, next_=NEXT_FETCH))

_put(12, "jmp_imm", encode(pc=PC_IMM, next_=NEXT_FETCH))
_put(13, "jz_rs1_imm", encode(pc=PC_IMM, pc_zero=1, next_=NEXT_FETCH))

_put(14, "call_ar_r15", encode(ar=AR_R15))
_put(15, "call_dr_pc", encode(dr=DR_PC))
_put(16, "call_store_ret", encode(mem_write=1))
_put(
    17,
    "call_r15_inc_pc_imm",
    encode(
        dst=DST_R15,
        wb=WB_ALU,
        alu_a=ALU_A_R15,
        alu_b=ALU_B_ONE,
        alu_op=ALU_ADD,
        pc=PC_IMM,
        next_=NEXT_FETCH,
    ),
)

_put(18, "callr_ar_r15", encode(ar=AR_R15))
_put(19, "callr_dr_pc", encode(dr=DR_PC))
_put(20, "callr_store_ret", encode(mem_write=1))
_put(
    21,
    "callr_r15_inc",
    encode(dst=DST_R15, wb=WB_ALU, alu_a=ALU_A_R15, alu_b=ALU_B_ONE, alu_op=ALU_ADD),
)
_put(22, "callr_dr_rs1", encode(dr=DR_RS1))
_put(23, "callr_pc_dr", encode(pc=PC_DR, next_=NEXT_FETCH))

_put(
    24,
    "ret_r15_dec",
    encode(
        dst=DST_R15,
        wb=WB_ALU,
        alu_a=ALU_A_R15,
        alu_b=ALU_B_NEG_ONE,
        alu_op=ALU_ADD,
    ),
)
_put(25, "ret_ar_r15", encode(ar=AR_R15))
_put(26, "ret_dr_mem", encode(dr=DR_MEM))
_put(27, "ret_pc_dr", encode(pc=PC_DR, next_=NEXT_FETCH))

_put(28, "halt", encode(halt=1, next_=NEXT_FETCH))

_decoder: list[int] = [0] * 256
_decoder[Opcode.NOP] = 3
_decoder[Opcode.ADD] = 4
_decoder[Opcode.SUB] = 4
_decoder[Opcode.MUL] = 4
_decoder[Opcode.DIV] = 4
_decoder[Opcode.MOD] = 4
_decoder[Opcode.CMP_EQ] = 4
_decoder[Opcode.CMP_GT] = 4
_decoder[Opcode.CMP_LT] = 4
_decoder[Opcode.LDI] = 5
_decoder[Opcode.LD] = 6
_decoder[Opcode.ST] = 9
_decoder[Opcode.JMP] = 12
_decoder[Opcode.JZ] = 13
_decoder[Opcode.CALL] = 14
_decoder[Opcode.CALLR] = 18
_decoder[Opcode.RET] = 24
_decoder[Opcode.HLT] = 28

MICROCODE_ROM: tuple[int, ...] = tuple(_mrom[:29])
MICROCODE_NAMES: tuple[str, ...] = tuple(_microcode_names[:29])
DECODER: tuple[int, ...] = tuple(_decoder)


def microinstruction_name(addr: int) -> str:
    if 0 <= addr < len(MICROCODE_NAMES):
        return MICROCODE_NAMES[addr]
    return "invalid"
