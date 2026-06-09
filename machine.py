from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

import microcode as mc
from isa import (
    Opcode,
    Reg,
    binary_to_words,
    decode_instruction,
    format_instruction,
    to_signed32,
    to_unsigned32,
)

MEMORY_SIZE = 4096
DATA_STACK_START = 1024
RETURN_STACK_START = 2048
IO_IN_ADDR = -2  # encoded as 0xFFE
IO_OUT_CHAR_ADDR = -1  # encoded as 0xFFF
IO_OUT_INT_ADDR = -3  # encoded as 0xFFD
IO_IN_PORT = IO_IN_ADDR & 0xFFF
IO_OUT_CHAR_PORT = IO_OUT_CHAR_ADDR & 0xFFF
IO_OUT_INT_PORT = IO_OUT_INT_ADDR & 0xFFF
MAX_TICKS = 50_000_000
DATA_STACK_REG = Reg.R14
RETURN_STACK_REG = Reg.R15


class DataPath:
    def __init__(self, memory_size: int, input_stream: str) -> None:
        self.memory = [0] * memory_size
        self.registers = dict.fromkeys(Reg, 0)
        self.input_stream = list(input_stream)
        self.output_buffer: list[str] = []
        self.pc = 0
        self.ir = 0
        self.ar = 0
        self.dr = 0

    def _normalize_addr(self, addr: int) -> int:
        return addr & 0xFFF

    def _check_memory_addr(self, addr: int) -> int:
        normalized = self._normalize_addr(addr)
        if normalized >= len(self.memory):
            raise IndexError(f"Memory address is out of range: {normalized}")
        return normalized

    def read_mem(self, addr: int) -> int:
        normalized = self._normalize_addr(addr)
        if normalized == IO_IN_PORT:
            if not self.input_stream:
                raise EOFError("Input stream is empty")
            value = ord(self.input_stream.pop(0))
            logging.debug("io_read char=%r code=%d", chr(value), value)
            return value
        return self.memory[self._check_memory_addr(addr)]

    def write_mem(self, addr: int, value: int) -> None:
        normalized = self._normalize_addr(addr)
        if normalized == IO_OUT_CHAR_PORT:
            char = chr(value & 0xFF)
            self.output_buffer.append(char)
            logging.debug("io_write_char char=%r code=%d", char, value & 0xFF)
            return
        if normalized == IO_OUT_INT_PORT:
            self.output_buffer.append(f"{to_signed32(value)} ")
            logging.debug("io_write_int value=%d", to_signed32(value))
            return
        self.memory[self._check_memory_addr(addr)] = to_signed32(value)

    def read_reg(self, reg: Reg) -> int:
        if reg == Reg.R0:
            return 0
        return self.registers[reg]

    def write_reg(self, reg: Reg, value: int) -> None:
        if reg == Reg.R0:
            return
        self.registers[reg] = to_signed32(value)

    def alu(self, opcode: Opcode, left: int, right: int) -> int:
        if opcode == Opcode.ADD:
            return to_signed32(left + right)
        if opcode == Opcode.SUB:
            return to_signed32(left - right)
        if opcode == Opcode.MUL:
            return to_signed32(left * right)
        if opcode == Opcode.DIV:
            return 0 if right == 0 else to_signed32(int(left / right))
        if opcode == Opcode.MOD:
            return 0 if right == 0 else to_signed32(left % right)
        if opcode == Opcode.CMP_EQ:
            return int(left == right)
        if opcode == Opcode.CMP_GT:
            return int(left > right)
        if opcode == Opcode.CMP_LT:
            return int(left < right)
        raise ValueError(f"Unsupported ALU operation: {opcode}")

    def dump_registers(self) -> str:
        register_text = " ".join(f"{reg.name}={self.read_reg(reg)}" for reg in Reg)
        return f"PC={self.pc} AR={self.ar} DR={self.dr} {register_text}"


class ControlUnit:
    def __init__(self, datapath: DataPath) -> None:
        self.dp = datapath
        self.tick_count = 0
        self.op = Opcode.NOP
        self.rd = Reg.R0
        self.rs1 = Reg.R0
        self.rs2 = Reg.R0
        self.imm = 0
        self.halted = False
        self.micro_pc = mc.UADDR_FETCH
        self.mir = 0

    @property
    def pc(self) -> int:
        return self.dp.pc

    @property
    def ir(self) -> int:
        return self.dp.ir

    def tick(self) -> None:
        if self.halted:
            return
        micro_pc_before = self.micro_pc
        word = mc.MICROCODE_ROM[micro_pc_before]
        self.mir = word
        self.tick_count += 1
        self._execute_microinstruction(word)
        logging.debug("\n%s", format_trace_line(self, micro_pc_before, word))
        self._advance_microprogram(word)

    def _execute_microinstruction(self, word: int) -> None:
        ar_src = mc.field(word, mc.AR_SHIFT, mc.AR_MASK)
        dr_src = mc.field(word, mc.DR_SHIFT, mc.SRC_MASK)
        dst = mc.field(word, mc.DST_SHIFT, mc.SRC_MASK)
        wb = mc.field(word, mc.WB_SHIFT, mc.SRC_MASK)
        pc_src = mc.field(word, mc.PC_SHIFT, mc.PC_MASK)

        fetch_word = (
            self.dp.read_mem(self.dp.pc) if mc.field(word, mc.FETCH_SHIFT) else None
        )
        ar_next = self._select_ar(ar_src) if ar_src != mc.AR_NONE else None
        dr_next = self._select_dr(dr_src) if dr_src != mc.DR_NONE else None
        reg_next = self._select_wb(wb, word) if dst != mc.DST_NONE else None
        pc_next: int | None = None
        if fetch_word is not None:
            pc_next = self.dp.pc + 1
        elif self._should_latch_pc(pc_src, mc.field(word, mc.PC_ZERO_SHIFT)):
            pc_next = self._select_pc(pc_src)

        if mc.field(word, mc.MEM_WRITE_SHIFT):
            self.dp.write_mem(self.dp.ar, self.dp.dr)
        if ar_next is not None:
            self.dp.ar = ar_next
        if dr_next is not None:
            self.dp.dr = dr_next
        if fetch_word is not None:
            self.dp.ir = to_unsigned32(fetch_word)
        elif mc.field(word, mc.IR_LATCH_SHIFT):
            self.dp.ir = to_unsigned32(self.dp.dr)
        if dst != mc.DST_NONE:
            if reg_next is None:
                name = mc.microinstruction_name(self.micro_pc)
                raise ValueError(f"Missing write-back source in {name}")
            self.dp.write_reg(self._select_reg_target(dst), reg_next)
        if pc_next is not None:
            self.dp.pc = pc_next & 0xFFF
        if mc.field(word, mc.HALT_SHIFT):
            self.halted = True

    def _advance_microprogram(self, word: int) -> None:
        if self.halted:
            return
        next_mode = mc.field(word, mc.NEXT_SHIFT, mc.NEXT_MASK)
        if next_mode == mc.NEXT_DECODE:
            self._decode_ir()
            self.micro_pc = mc.DECODER[int(self.op)]
        elif next_mode == mc.NEXT_FETCH:
            self.micro_pc = mc.UADDR_FETCH
        elif next_mode == mc.NEXT_SEQ:
            self.micro_pc += 1
        else:
            name = mc.microinstruction_name(self.micro_pc)
            raise ValueError(f"Unsupported next uPC mode in {name}: {next_mode}")

    def _decode_ir(self) -> None:
        self.op, self.rd, self.rs1, self.rs2, self.imm = decode_instruction(self.dp.ir)

    def _safe_instruction_text(self) -> str:
        try:
            return format_instruction(self.dp.ir)
        except ValueError:
            return f".word {self.dp.ir}"

    def _select_ar(self, source: int) -> int:
        if source == mc.AR_RD:
            return self.dp.read_reg(self.rd)
        if source == mc.AR_RS1:
            return self.dp.read_reg(self.rs1)
        if source == mc.AR_R15:
            return self.dp.read_reg(RETURN_STACK_REG)
        raise ValueError(f"Unsupported AR source: {source}")

    def _select_dr(self, source: int) -> int:
        if source == mc.DR_MEM:
            return self.dp.read_mem(self.dp.ar)
        if source == mc.DR_PC:
            return self.dp.pc
        if source == mc.DR_RS1:
            return self.dp.read_reg(self.rs1)
        raise ValueError(f"Unsupported DR source: {source}")

    def _select_wb(self, source: int, word: int) -> int:
        if source == mc.WB_IMM:
            return self.imm
        if source == mc.WB_DR:
            return self.dp.dr
        if source == mc.WB_ALU:
            return self.dp.alu(
                self._select_alu_op(word),
                self._select_alu_left(),
                self._select_alu_b(mc.field(word, mc.ALU_B_SHIFT, mc.SRC_MASK)),
            )
        raise ValueError(f"Unsupported write-back source: {source}")

    def _select_alu_op(self, word: int) -> Opcode:
        alu_op = mc.field(word, mc.ALU_OP_SHIFT, mc.ALU_OP_MASK)
        if alu_op == mc.ALU_FROM_OPCODE:
            return self.op
        if alu_op == mc.ALU_ADD:
            return Opcode.ADD
        raise ValueError(f"Unsupported ALU operation code: {alu_op}")

    def _select_alu_left(self) -> int:
        if self.op in {Opcode.CALL, Opcode.CALLR, Opcode.RET}:
            return self.dp.read_reg(RETURN_STACK_REG)
        return self.dp.read_reg(self.rs1)

    def _select_alu_b(self, source: int) -> int:
        if source == mc.ALU_B_RS2:
            return self.dp.read_reg(self.rs2)
        if source == mc.ALU_B_ONE:
            return 1
        if source == mc.ALU_B_NEG_ONE:
            return -1
        raise ValueError(f"Unsupported ALU right source: {source}")

    def _select_reg_target(self, target: int) -> Reg:
        if target == mc.DST_RD:
            return self.rd
        if target == mc.DST_R15:
            return RETURN_STACK_REG
        raise ValueError(f"Unsupported register target: {target}")

    def _should_latch_pc(self, pc_src: int, pc_zero: int) -> bool:
        if pc_src == mc.PC_NONE:
            return False
        if pc_zero:
            return self.dp.read_reg(self.rs1) == self.dp.read_reg(Reg.R0)
        return True

    def _select_pc(self, source: int) -> int:
        if source == mc.PC_INC:
            return self.dp.pc + 1
        if source == mc.PC_IMM:
            return self.imm
        if source == mc.PC_DR:
            return self.dp.dr
        raise ValueError(f"Unsupported PC source: {source}")


def load_binary_to_memory(dp: DataPath, binary_code: bytes) -> None:
    words = binary_to_words(binary_code)
    if len(words) > len(dp.memory):
        raise ValueError("Program does not fit into memory")
    for addr, word in enumerate(words):
        dp.memory[addr] = to_unsigned32(word)


def _format_int(value: int) -> str:
    return f"{value} (0x{to_unsigned32(value):08X})"


def _format_register_rows(dp: DataPath) -> list[str]:
    rows = ["Registers:"]
    regs = list(Reg)
    for start in range(0, len(regs), 4):
        cells = []
        for reg in regs[start : start + 4]:
            cells.append(f"{reg.name:<4}= {dp.read_reg(reg):>11}")
        rows.append("\t" + "\t".join(cells))
    return rows


def _format_signal_rows(micro_word: int, opcode: Opcode) -> list[str]:
    items = mc.signal_items(micro_word, opcode)
    rows = ["Signals:"]
    if not items:
        rows.append("\t-")
        return rows
    max_name_len = max(len(name) for name, _ in items)
    for name, value in items:
        rows.append(f"\t{name:<{max_name_len}} │ {value}")
    return rows


def format_trace_line(cu: ControlUnit, micro_pc: int, micro_word: int) -> str:
    rows = [
        "=" * 78,
        f"Tick {cu.tick_count:06d} │ uPC {micro_pc:02d} │ MIR 0x{micro_word:08X}",
        "-" * 78,
        f"Instruction:\t{cu._safe_instruction_text()}",
        f"Microcode:\t{mc.microinstruction_name(micro_pc)}",
        "",
        "State:",
        (
            f"\tPC = 0x{cu.pc:04X}\tIR = 0x{to_unsigned32(cu.ir):08X}"
            f"\tAR = {_format_int(cu.dp.ar)}\tDR = {_format_int(cu.dp.dr)}"
        ),
        "",
    ]
    rows.extend(_format_signal_rows(micro_word, cu.op))
    rows.append("")
    rows.extend(_format_register_rows(cu.dp))
    rows.extend(["", f"Output:\t{''.join(cu.dp.output_buffer)!r}"])
    return "\n".join(rows)



def format_event_block(tick_count: int, event: str) -> str:
    return "\n".join(
        [
            "=" * 78,
            f"Tick {tick_count:06d} │ event",
            "-" * 78,
            f"Event:\t{event}",
        ]
    )


def format_ellipsis_block() -> str:
    return "\n".join(["=" * 78, "... trace truncated ..."])


def format_summary_block(
    tick_count: int, instructions_fetched: int, output: str, halt_reason: str
) -> str:
    return "\n".join(
        [
            "=" * 78,
            "Summary",
            "-" * 78,
            f"Total ticks:\t\t{tick_count}",
            f"Instructions fetched:\t{instructions_fetched}",
            f"Output:\t\t\t{output}",
            f"Halt reason:\t\t{halt_reason}",
        ]
    )


@dataclass(frozen=True)
class SimulationResult:
    output: str
    trace: str
    ticks: int
    instructions_fetched: int
    halt_reason: str


def simulate(
    binary_code: bytes,
    input_str: str = "",
    max_ticks: int = MAX_TICKS,
    trace_head: int = 0,
) -> SimulationResult:
    dp = DataPath(MEMORY_SIZE, input_str)
    dp.write_reg(DATA_STACK_REG, DATA_STACK_START)
    dp.write_reg(RETURN_STACK_REG, RETURN_STACK_START)
    load_binary_to_memory(dp, binary_code)

    cu = ControlUnit(dp)
    trace: list[str] = []
    halt_reason = "halted"
    instructions_fetched = 0
    while not cu.halted and cu.tick_count < max_ticks:
        micro_pc_before = cu.micro_pc
        micro_word = mc.MICROCODE_ROM[micro_pc_before]
        try:
            cu.tick()
        except EOFError:
            halt_reason = "input_stream_empty"
            trace.append(format_event_block(cu.tick_count, "INPUT_STREAM_EMPTY"))
            break
        if micro_pc_before == mc.UADDR_FETCH:
            instructions_fetched += 1
        if len(trace) < trace_head:
            trace.append(format_trace_line(cu, micro_pc_before, micro_word))

    if cu.tick_count >= max_ticks and not cu.halted:
        raise TimeoutError(f"Simulation stopped after {max_ticks} ticks")

    if trace_head > 0 and cu.tick_count > len(trace):
        trace.append(format_ellipsis_block())
    trace.append(
        format_summary_block(
            cu.tick_count, instructions_fetched, "".join(dp.output_buffer), halt_reason
        )
    )
    return SimulationResult(
        output="".join(dp.output_buffer),
        trace="\n\n".join(trace),
        ticks=cu.tick_count,
        instructions_fetched=instructions_fetched,
        halt_reason=halt_reason,
    )


def run_simulation(
    binary_code: bytes,
    input_str: str = "",
    max_ticks: int = MAX_TICKS,
) -> str:
    result = simulate(binary_code, input_str, max_ticks=max_ticks)
    logging.info("Execution finished in %d ticks.", result.ticks)
    return result.output


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CSA lab4 processor model.")
    parser.add_argument("target", help="binary machine-code file")
    parser.add_argument("input", help="input stream text file")
    parser.add_argument("--log", help="write representative tick trace to this file")
    parser.add_argument(
        "--trace-head", type=int, default=200, help="trace lines to keep"
    )
    parser.add_argument(
        "--max-ticks", type=int, default=MAX_TICKS, help="simulation tick limit"
    )
    parser.add_argument(
        "--debug", action="store_true", help="enable per-tick debug logging"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO, format="%(message)s"
    )
    binary = Path(args.target).read_bytes()
    input_data = Path(args.input).read_text(encoding="utf-8")
    result = simulate(
        binary, input_data, max_ticks=args.max_ticks, trace_head=args.trace_head
    )
    if args.log is not None:
        Path(args.log).write_text(result.trace + "\n", encoding="utf-8")
    print("Output:", result.output)


if __name__ == "__main__":
    main()
