from __future__ import annotations

from enum import IntEnum

WORD_BYTES = 4
IMM_BITS = 12
IMM_MASK = (1 << IMM_BITS) - 1
SIGN_BIT = 1 << (IMM_BITS - 1)


class Opcode(IntEnum):
    NOP = 0x00
    ADD = 0x01
    SUB = 0x02
    MUL = 0x03
    DIV = 0x04
    MOD = 0x05
    CMP_EQ = 0x06
    CMP_GT = 0x07
    CMP_LT = 0x08
    LD = 0x0A
    ST = 0x0B
    LDI = 0x0C
    JMP = 0x10
    JZ = 0x11
    CALL = 0x12
    RET = 0x13
    CALLR = 0x14
    HLT = 0xFF


class Reg(IntEnum):
    R0 = 0  # zero register
    R1 = 1
    R2 = 2
    R3 = 3
    SP = 14  # data stack pointer
    RSP = 15  # return stack pointer


def sign_extend_12(value: int) -> int:
    value &= IMM_MASK
    if value & SIGN_BIT:
        return value - (1 << IMM_BITS)
    return value


def build_instruction(
    opcode: Opcode,
    rd: Reg = Reg.R0,
    rs1: Reg = Reg.R0,
    rs2: Reg = Reg.R0,
    imm: int = 0,
) -> int:
    """Encode one fixed-width 32-bit instruction.

    Format: [opcode:8][rd:4][rs1:4][rs2:4][imm:12].
    """
    return (
        (int(opcode) << 24)
        | (int(rd) << 20)
        | (int(rs1) << 16)
        | (int(rs2) << 12)
        | (imm & IMM_MASK)
    )


def decode_instruction(instr: int) -> tuple[Opcode, Reg, Reg, Reg, int]:
    opcode = Opcode((instr >> 24) & 0xFF)
    rd = Reg((instr >> 20) & 0x0F)
    rs1 = Reg((instr >> 16) & 0x0F)
    rs2 = Reg((instr >> 12) & 0x0F)
    imm = sign_extend_12(instr)
    return opcode, rd, rs1, rs2, imm


def code_to_binary(words: list[int]) -> bytes:
    result = bytearray()
    for word in words:
        result.extend(word.to_bytes(WORD_BYTES, byteorder="big", signed=False))
    return bytes(result)


def binary_to_words(binary_data: bytes) -> list[int]:
    if len(binary_data) % WORD_BYTES != 0:
        raise ValueError("Binary code size must be divisible by 4 bytes")
    return [
        int.from_bytes(binary_data[i : i + WORD_BYTES], byteorder="big", signed=False)
        for i in range(0, len(binary_data), WORD_BYTES)
    ]


def format_instruction(word: int) -> str:
    try:
        op, rd, rs1, rs2, imm = decode_instruction(word)
    except ValueError:
        return f".word {word}"
    if op in {
        Opcode.ADD,
        Opcode.SUB,
        Opcode.MUL,
        Opcode.DIV,
        Opcode.MOD,
        Opcode.CMP_EQ,
        Opcode.CMP_GT,
        Opcode.CMP_LT,
    }:
        return f"{op.name.lower()} {rd.name}, {rs1.name}, {rs2.name}"
    if op == Opcode.LD:
        return f"ld {rd.name}, [{rs1.name}]"
    if op == Opcode.ST:
        return f"st [{rd.name}], {rs1.name}"
    if op == Opcode.LDI:
        return f"ldi {rd.name}, {imm}"
    if op in {Opcode.JMP, Opcode.CALL}:
        return f"{op.name.lower()} {imm}"
    if op == Opcode.JZ:
        return f"jz {rs1.name}, {imm}"
    if op == Opcode.CALLR:
        return f"callr {rs1.name}"
    if op in {Opcode.NOP, Opcode.RET, Opcode.HLT}:
        return op.name.lower()
    return f"{op.name.lower()} {rd.name}, {rs1.name}, {rs2.name}, {imm}"


def binary_to_hex_dump(binary_data: bytes, code_words: int | None = None) -> str:
    result = []
    words = binary_to_words(binary_data)
    for addr, word in enumerate(words):
        if code_words is not None and addr >= code_words:
            result.append(f"{addr:04X} - {word:08X} - .word {word}")
        else:
            result.append(f"{addr:04X} - {word:08X} - {format_instruction(word)}")
    return "\n".join(result)
