from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Callable

from isa import Opcode, Reg, binary_to_words, decode_instruction, format_instruction

MEMORY_SIZE = 4096
DATA_STACK_START = 1024
RETURN_STACK_START = 2048
IO_IN_ADDR = -2  # encoded as 0xFFE
IO_OUT_CHAR_ADDR = -1  # encoded as 0xFFF
IO_OUT_INT_ADDR = -3  # encoded as 0xFFD
MAX_TICKS = 50_000_000


@dataclass(frozen=True)
class MicroInstruction:
    name: str
    action: Callable[["ControlUnit"], None]


class DataPath:
    def __init__(self, memory_size: int, input_stream: str) -> None:
        self.memory = [0] * memory_size
        self.registers = {reg: 0 for reg in Reg}
        self.input_stream = list(input_stream)
        self.output_buffer: list[str] = []

    def _normalize_addr(self, addr: int) -> int:
        return addr & 0xFFF

    def read_mem(self, addr: int) -> int:
        if addr == IO_IN_ADDR:
            if not self.input_stream:
                raise EOFError("Input stream is empty")
            value = ord(self.input_stream.pop(0))
            logging.debug("io_read char=%r code=%d", chr(value), value)
            return value
        return self.memory[self._normalize_addr(addr)]

    def write_mem(self, addr: int, value: int) -> None:
        if addr == IO_OUT_CHAR_ADDR:
            char = chr(value & 0xFF)
            self.output_buffer.append(char)
            logging.debug("io_write_char char=%r code=%d", char, value & 0xFF)
            return
        if addr == IO_OUT_INT_ADDR:
            self.output_buffer.append(f"{value} ")
            logging.debug("io_write_int value=%d", value)
            return
        self.memory[self._normalize_addr(addr)] = value

    def read_reg(self, reg: Reg) -> int:
        if reg == Reg.R0:
            return 0
        return self.registers[reg]

    def write_reg(self, reg: Reg, value: int) -> None:
        if reg != Reg.R0:
            self.registers[reg] = value

    def alu(self, opcode: Opcode, left: int, right: int) -> int:
        if opcode == Opcode.ADD:
            return left + right
        if opcode == Opcode.SUB:
            return left - right
        if opcode == Opcode.MUL:
            return left * right
        if opcode == Opcode.DIV:
            return 0 if right == 0 else left // right
        if opcode == Opcode.MOD:
            return 0 if right == 0 else left % right
        if opcode == Opcode.CMP_EQ:
            return int(left == right)
        if opcode == Opcode.CMP_GT:
            return int(left > right)
        if opcode == Opcode.CMP_LT:
            return int(left < right)
        raise ValueError(f"Unsupported ALU operation: {opcode}")

    def dump_registers(self) -> str:
        visible = [Reg.R1, Reg.R2, Reg.R3, Reg.SP, Reg.RSP]
        return " ".join(f"{reg.name}={self.read_reg(reg)}" for reg in visible)


class ControlUnit:
    def __init__(self, datapath: DataPath) -> None:
        self.dp = datapath
        self.pc = 0
        self.tick_count = 0
        self.ir = 0
        self.op = Opcode.NOP
        self.rd = Reg.R0
        self.rs1 = Reg.R0
        self.rs2 = Reg.R0
        self.imm = 0
        self.halted = False
        self.micro_pc = 0
        self.current_microprogram: tuple[MicroInstruction, ...] = FETCH_MICROPROGRAM
        self.next_microprogram: tuple[MicroInstruction, ...] | None = None

    def tick(self) -> None:
        if self.halted:
            return
        micro = self.current_microprogram[self.micro_pc]
        self.tick_count += 1
        micro.action(self)
        logging.debug(
            "tick=%06d mpc=%02d micro=%s pc=%d ir=%08X instr=%s %s out=%r",
            self.tick_count,
            self.micro_pc,
            micro.name,
            self.pc,
            self.ir,
            self._safe_instruction_text(),
            self.dp.dump_registers(),
            "".join(self.dp.output_buffer),
        )
        self.micro_pc += 1
        if self.micro_pc >= len(self.current_microprogram):
            self.current_microprogram = self.next_microprogram or FETCH_MICROPROGRAM
            self.next_microprogram = None
            self.micro_pc = 0

    def _safe_instruction_text(self) -> str:
        try:
            return format_instruction(self.ir)
        except ValueError:
            return f".word {self.ir}"


def mi_fetch(cu: ControlUnit) -> None:
    cu.ir = cu.dp.read_mem(cu.pc)
    cu.pc += 1


def mi_decode(cu: ControlUnit) -> None:
    cu.op, cu.rd, cu.rs1, cu.rs2, cu.imm = decode_instruction(cu.ir)
    cu.next_microprogram = MICROCODE_ROM[cu.op]


def mi_nop(_: ControlUnit) -> None:
    return


def mi_alu_to_reg(cu: ControlUnit) -> None:
    left = cu.dp.read_reg(cu.rs1)
    right = cu.dp.read_reg(cu.rs2)
    cu.dp.write_reg(cu.rd, cu.dp.alu(cu.op, left, right))


def mi_ldi(cu: ControlUnit) -> None:
    cu.dp.write_reg(cu.rd, cu.imm)


def mi_load(cu: ControlUnit) -> None:
    cu.dp.write_reg(cu.rd, cu.dp.read_mem(cu.dp.read_reg(cu.rs1)))


def mi_store(cu: ControlUnit) -> None:
    cu.dp.write_mem(cu.dp.read_reg(cu.rd), cu.dp.read_reg(cu.rs1))


def mi_jump(cu: ControlUnit) -> None:
    cu.pc = cu.imm


def mi_jump_if_zero(cu: ControlUnit) -> None:
    if cu.dp.read_reg(cu.rs1) == 0:
        cu.pc = cu.imm


def mi_call_store_return(cu: ControlUnit) -> None:
    cu.dp.write_mem(cu.dp.read_reg(Reg.RSP), cu.pc)


def mi_call_finish(cu: ControlUnit) -> None:
    cu.dp.write_reg(Reg.RSP, cu.dp.read_reg(Reg.RSP) + 1)
    cu.pc = cu.imm


def mi_callr_finish(cu: ControlUnit) -> None:
    target = cu.dp.read_reg(cu.rs1)
    cu.dp.write_reg(Reg.RSP, cu.dp.read_reg(Reg.RSP) + 1)
    cu.pc = target


def mi_ret_dec_rsp(cu: ControlUnit) -> None:
    cu.dp.write_reg(Reg.RSP, cu.dp.read_reg(Reg.RSP) - 1)


def mi_ret_load_pc(cu: ControlUnit) -> None:
    cu.pc = cu.dp.read_mem(cu.dp.read_reg(Reg.RSP))


def mi_halt(cu: ControlUnit) -> None:
    cu.halted = True


FETCH_MICROPROGRAM = (
    MicroInstruction("fetch_ir_from_mem_pc", mi_fetch),
    MicroInstruction("decode_ir_select_microprogram", mi_decode),
)

ALU_MICROPROGRAM = (MicroInstruction("alu_rs1_rs2_to_rd", mi_alu_to_reg),)

MICROCODE_ROM: dict[Opcode, tuple[MicroInstruction, ...]] = {
    Opcode.NOP: (MicroInstruction("nop", mi_nop),),
    Opcode.ADD: ALU_MICROPROGRAM,
    Opcode.SUB: ALU_MICROPROGRAM,
    Opcode.MUL: ALU_MICROPROGRAM,
    Opcode.DIV: ALU_MICROPROGRAM,
    Opcode.MOD: ALU_MICROPROGRAM,
    Opcode.CMP_EQ: ALU_MICROPROGRAM,
    Opcode.CMP_GT: ALU_MICROPROGRAM,
    Opcode.CMP_LT: ALU_MICROPROGRAM,
    Opcode.LDI: (MicroInstruction("write_imm_to_rd", mi_ldi),),
    Opcode.LD: (MicroInstruction("read_mem_rs1_to_rd", mi_load),),
    Opcode.ST: (MicroInstruction("write_rs1_to_mem_rd", mi_store),),
    Opcode.JMP: (MicroInstruction("pc_from_imm", mi_jump),),
    Opcode.JZ: (MicroInstruction("pc_from_imm_if_rs1_zero", mi_jump_if_zero),),
    Opcode.CALL: (
        MicroInstruction("write_return_pc_to_return_stack", mi_call_store_return),
        MicroInstruction("increment_rsp_and_jump_imm", mi_call_finish),
    ),
    Opcode.CALLR: (
        MicroInstruction("write_return_pc_to_return_stack", mi_call_store_return),
        MicroInstruction("increment_rsp_and_jump_rs1", mi_callr_finish),
    ),
    Opcode.RET: (
        MicroInstruction("decrement_rsp", mi_ret_dec_rsp),
        MicroInstruction("read_return_pc", mi_ret_load_pc),
    ),
    Opcode.HLT: (MicroInstruction("halt", mi_halt),),
}


def load_binary_to_memory(dp: DataPath, binary_code: bytes) -> None:
    words = binary_to_words(binary_code)
    if len(words) > len(dp.memory):
        raise ValueError("Program does not fit into memory")
    for addr, word in enumerate(words):
        dp.memory[addr] = word


def run_simulation(
    binary_code: bytes,
    input_str: str = "",
    max_ticks: int = MAX_TICKS,
) -> str:
    dp = DataPath(MEMORY_SIZE, input_str)
    dp.write_reg(Reg.SP, DATA_STACK_START)
    dp.write_reg(Reg.RSP, RETURN_STACK_START)
    load_binary_to_memory(dp, binary_code)

    cu = ControlUnit(dp)
    try:
        while not cu.halted and cu.tick_count < max_ticks:
            cu.tick()
    except EOFError:
        logging.warning("Input stream empty")

    if cu.tick_count >= max_ticks and not cu.halted:
        raise TimeoutError(f"Simulation stopped after {max_ticks} ticks")

    logging.info("Execution finished in %d ticks.", cu.tick_count)
    return "".join(dp.output_buffer)


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: machine.py <target.bin> <input.txt>")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with open(sys.argv[1], "rb") as file:
        binary = file.read()
    with open(sys.argv[2], encoding="utf-8") as file:
        input_data = file.read()
    print("Output:", run_simulation(binary, input_data))


if __name__ == "__main__":
    main()
