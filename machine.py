from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

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


class ArSource(Enum):
    PC = auto()
    RD = auto()
    RS1 = auto()
    RSP = auto()


class DrSource(Enum):
    MEM = auto()
    PC = auto()
    RS1 = auto()


class WbSource(Enum):
    IMM = auto()
    DR = auto()
    ALU = auto()


class RegTarget(Enum):
    RD = auto()
    FIXED = auto()


class AluASource(Enum):
    RS1 = auto()
    RSP = auto()


class AluBSource(Enum):
    RS2 = auto()
    ONE = auto()
    NEG_ONE = auto()


class PcSource(Enum):
    INC = auto()
    IMM = auto()
    DR = auto()


class PcCondition(Enum):
    ALWAYS = auto()
    RS1_ZERO = auto()


class NextMicro(Enum):
    SEQ = auto()
    FETCH = auto()
    DECODED = auto()


@dataclass(frozen=True)
class MicroInstruction:
    """One control-store word.

    The fields are datapath control signals, not Python callbacks.  The control
    unit only interprets these fields and therefore stays close to the CU
    scheme: microprogram ROM -> MIR -> signals -> datapath.
    """

    name: str
    ar_src: ArSource | None = None
    ar_latch: bool = False
    dr_src: DrSource | None = None
    dr_latch: bool = False
    ir_latch: bool = False
    mem_read: bool = False
    mem_write: bool = False
    reg_target: RegTarget | None = None
    fixed_reg: Reg | None = None
    reg_write: bool = False
    wb_src: WbSource | None = None
    alu_a_src: AluASource | None = None
    alu_b_src: AluBSource | None = None
    alu_op: Opcode | None = None
    pc_src: PcSource | None = None
    pc_latch: bool = False
    pc_condition: PcCondition = PcCondition.ALWAYS
    halt: bool = False
    next_micro: NextMicro = NextMicro.SEQ

    def active_signals(self) -> str:
        signals: list[str] = []
        if self.ar_latch:
            signals.append(
                f"ar_sel={self.ar_src.name.lower()}" if self.ar_src else "ar_latch"
            )
        if self.dr_latch:
            signals.append(
                f"dr_sel={self.dr_src.name.lower()}" if self.dr_src else "dr_latch"
            )
        if self.ir_latch:
            signals.append("ir_latch")
        if self.mem_read:
            signals.append("mem_read")
        if self.mem_write:
            signals.append("mem_write")
        if self.reg_write:
            target = self.fixed_reg.name if self.fixed_reg is not None else "rd"
            source = self.wb_src.name.lower() if self.wb_src is not None else "?"
            signals.append(f"reg_write={target}<-{source}")
        if self.alu_op is not None:
            signals.append(f"alu_op={self.alu_op.name.lower()}")
        if self.pc_latch:
            src = self.pc_src.name.lower() if self.pc_src is not None else "?"
            if self.pc_condition == PcCondition.RS1_ZERO:
                signals.append(f"pc_sel={src}_if_zero")
            else:
                signals.append(f"pc_sel={src}")
        if self.halt:
            signals.append("halt")
        if self.next_micro != NextMicro.SEQ:
            signals.append(f"next={self.next_micro.name.lower()}")
        return ",".join(signals) or "-"


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
        if reg != Reg.R0:
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
        visible = [Reg.R1, Reg.R2, Reg.R3, Reg.SP, Reg.RSP]
        register_text = " ".join(f"{reg.name}={self.read_reg(reg)}" for reg in visible)
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
        self.micro_pc = 0
        self.mir: MicroInstruction | None = None
        self.current_microprogram: tuple[MicroInstruction, ...] = FETCH_MICROPROGRAM

    @property
    def pc(self) -> int:
        return self.dp.pc

    @property
    def ir(self) -> int:
        return self.dp.ir

    def tick(self) -> None:
        if self.halted:
            return
        micro = self.current_microprogram[self.micro_pc]
        self.mir = micro
        self.tick_count += 1
        self._execute_microinstruction(micro)
        logging.debug(
            "tick=%06d mpc=%02d micro=%s signals=%s ir=%08X instr=%s %s out=%r",
            self.tick_count,
            self.micro_pc,
            micro.name,
            micro.active_signals(),
            to_unsigned32(self.dp.ir),
            self._safe_instruction_text(),
            self.dp.dump_registers(),
            "".join(self.dp.output_buffer),
        )
        self._advance_microprogram(micro)

    def _execute_microinstruction(self, micro: MicroInstruction) -> None:
        ar_next = self._select_ar(micro.ar_src) if micro.ar_latch else None
        dr_next = self._select_dr(micro.dr_src) if micro.dr_latch else None
        reg_next = self._select_wb(micro.wb_src) if micro.reg_write else None
        pc_next = (
            self._select_pc(micro.pc_src) if self._should_latch_pc(micro) else None
        )

        if micro.mem_write:
            self.dp.write_mem(self.dp.ar, self.dp.dr)
        if ar_next is not None:
            self.dp.ar = ar_next
        if dr_next is not None:
            self.dp.dr = dr_next
        if micro.ir_latch:
            self.dp.ir = to_unsigned32(self.dp.dr)
        if micro.reg_write:
            target = self._select_reg_target(micro)
            if reg_next is None:
                raise ValueError(f"Missing write-back source in {micro.name}")
            self.dp.write_reg(target, reg_next)
        if pc_next is not None:
            self.dp.pc = pc_next & 0xFFF
        if micro.halt:
            self.halted = True

    def _advance_microprogram(self, micro: MicroInstruction) -> None:
        if self.halted:
            return
        if micro.next_micro == NextMicro.DECODED:
            self._decode_ir()
            self.current_microprogram = MICROCODE_ROM[self.op]
            self.micro_pc = 0
            return
        if micro.next_micro == NextMicro.FETCH:
            self.current_microprogram = FETCH_MICROPROGRAM
            self.micro_pc = 0
            return
        self.micro_pc += 1
        if self.micro_pc >= len(self.current_microprogram):
            self.current_microprogram = FETCH_MICROPROGRAM
            self.micro_pc = 0

    def _decode_ir(self) -> None:
        self.op, self.rd, self.rs1, self.rs2, self.imm = decode_instruction(self.dp.ir)

    def _safe_instruction_text(self) -> str:
        try:
            return format_instruction(self.dp.ir)
        except ValueError:
            return f".word {self.dp.ir}"

    def _select_ar(self, source: ArSource | None) -> int:
        if source == ArSource.PC:
            return self.dp.pc
        if source == ArSource.RD:
            return self.dp.read_reg(self.rd)
        if source == ArSource.RS1:
            return self.dp.read_reg(self.rs1)
        if source == ArSource.RSP:
            return self.dp.read_reg(Reg.RSP)
        raise ValueError(f"Unsupported AR source: {source}")

    def _select_dr(self, source: DrSource | None) -> int:
        if source == DrSource.MEM:
            return self.dp.read_mem(self.dp.ar)
        if source == DrSource.PC:
            return self.dp.pc
        if source == DrSource.RS1:
            return self.dp.read_reg(self.rs1)
        raise ValueError(f"Unsupported DR source: {source}")

    def _select_wb(self, source: WbSource | None) -> int:
        if source == WbSource.IMM:
            return self.imm
        if source == WbSource.DR:
            return self.dp.dr
        if source == WbSource.ALU:
            alu_op = self._select_alu_op()
            return self.dp.alu(alu_op, self._select_alu_a(), self._select_alu_b())
        raise ValueError(f"Unsupported write-back source: {source}")

    def _select_alu_op(self) -> Opcode:
        micro = self.mir
        if micro is None:
            raise RuntimeError("MIR is empty")
        return micro.alu_op or self.op

    def _select_alu_a(self) -> int:
        micro = self.mir
        if micro is None:
            raise RuntimeError("MIR is empty")
        if micro.alu_a_src == AluASource.RS1:
            return self.dp.read_reg(self.rs1)
        if micro.alu_a_src == AluASource.RSP:
            return self.dp.read_reg(Reg.RSP)
        raise ValueError(f"Unsupported ALU A source: {micro.alu_a_src}")

    def _select_alu_b(self) -> int:
        micro = self.mir
        if micro is None:
            raise RuntimeError("MIR is empty")
        if micro.alu_b_src == AluBSource.RS2:
            return self.dp.read_reg(self.rs2)
        if micro.alu_b_src == AluBSource.ONE:
            return 1
        if micro.alu_b_src == AluBSource.NEG_ONE:
            return -1
        raise ValueError(f"Unsupported ALU B source: {micro.alu_b_src}")

    def _select_reg_target(self, micro: MicroInstruction) -> Reg:
        if micro.reg_target == RegTarget.RD:
            return self.rd
        if micro.reg_target == RegTarget.FIXED and micro.fixed_reg is not None:
            return micro.fixed_reg
        raise ValueError(f"Unsupported register target in {micro.name}")

    def _should_latch_pc(self, micro: MicroInstruction) -> bool:
        if not micro.pc_latch:
            return False
        if micro.pc_condition == PcCondition.ALWAYS:
            return True
        if micro.pc_condition == PcCondition.RS1_ZERO:
            return self.dp.read_reg(self.rs1) == 0
        raise ValueError(f"Unsupported PC condition: {micro.pc_condition}")

    def _select_pc(self, source: PcSource | None) -> int:
        if source == PcSource.INC:
            return self.dp.pc + 1
        if source == PcSource.IMM:
            return self.imm
        if source == PcSource.DR:
            return self.dp.dr
        raise ValueError(f"Unsupported PC source: {source}")


FETCH_MICROPROGRAM = (
    MicroInstruction(
        "fetch_ir_from_mem_pc",
        ar_src=ArSource.PC,
        ar_latch=True,
    ),
    MicroInstruction(
        "fetch_memory_word_to_dr",
        dr_src=DrSource.MEM,
        dr_latch=True,
        mem_read=True,
    ),
    MicroInstruction(
        "latch_ir_increment_pc_decode",
        ir_latch=True,
        pc_src=PcSource.INC,
        pc_latch=True,
        next_micro=NextMicro.DECODED,
    ),
)

ALU_MICROPROGRAM = (
    MicroInstruction(
        "alu_rs1_rs2_to_rd",
        reg_target=RegTarget.RD,
        reg_write=True,
        wb_src=WbSource.ALU,
        alu_a_src=AluASource.RS1,
        alu_b_src=AluBSource.RS2,
    ),
)

MICROCODE_ROM: dict[Opcode, tuple[MicroInstruction, ...]] = {
    Opcode.NOP: (MicroInstruction("nop"),),
    Opcode.ADD: ALU_MICROPROGRAM,
    Opcode.SUB: ALU_MICROPROGRAM,
    Opcode.MUL: ALU_MICROPROGRAM,
    Opcode.DIV: ALU_MICROPROGRAM,
    Opcode.MOD: ALU_MICROPROGRAM,
    Opcode.CMP_EQ: ALU_MICROPROGRAM,
    Opcode.CMP_GT: ALU_MICROPROGRAM,
    Opcode.CMP_LT: ALU_MICROPROGRAM,
    Opcode.LDI: (
        MicroInstruction(
            "write_imm_to_rd",
            reg_target=RegTarget.RD,
            reg_write=True,
            wb_src=WbSource.IMM,
        ),
    ),
    Opcode.LD: (
        MicroInstruction("load_address_rs1_to_ar", ar_src=ArSource.RS1, ar_latch=True),
        MicroInstruction(
            "load_memory_word_to_dr",
            dr_src=DrSource.MEM,
            dr_latch=True,
            mem_read=True,
        ),
        MicroInstruction(
            "load_dr_to_rd",
            reg_target=RegTarget.RD,
            reg_write=True,
            wb_src=WbSource.DR,
        ),
    ),
    Opcode.ST: (
        MicroInstruction("store_address_rd_to_ar", ar_src=ArSource.RD, ar_latch=True),
        MicroInstruction("store_rs1_to_dr", dr_src=DrSource.RS1, dr_latch=True),
        MicroInstruction("store_dr_to_memory", mem_write=True),
    ),
    Opcode.JMP: (MicroInstruction("pc_from_imm", pc_src=PcSource.IMM, pc_latch=True),),
    Opcode.JZ: (
        MicroInstruction(
            "pc_from_imm_if_rs1_zero",
            pc_src=PcSource.IMM,
            pc_latch=True,
            pc_condition=PcCondition.RS1_ZERO,
        ),
    ),
    Opcode.CALL: (
        MicroInstruction("call_address_rsp_to_ar", ar_src=ArSource.RSP, ar_latch=True),
        MicroInstruction("call_return_pc_to_dr", dr_src=DrSource.PC, dr_latch=True),
        MicroInstruction("call_store_return_pc", mem_write=True),
        MicroInstruction(
            "call_increment_rsp_and_jump_imm",
            reg_target=RegTarget.FIXED,
            fixed_reg=Reg.RSP,
            reg_write=True,
            wb_src=WbSource.ALU,
            alu_a_src=AluASource.RSP,
            alu_b_src=AluBSource.ONE,
            alu_op=Opcode.ADD,
            pc_src=PcSource.IMM,
            pc_latch=True,
        ),
    ),
    Opcode.CALLR: (
        MicroInstruction("callr_address_rsp_to_ar", ar_src=ArSource.RSP, ar_latch=True),
        MicroInstruction("callr_return_pc_to_dr", dr_src=DrSource.PC, dr_latch=True),
        MicroInstruction("callr_store_return_pc", mem_write=True),
        MicroInstruction(
            "callr_increment_rsp",
            reg_target=RegTarget.FIXED,
            fixed_reg=Reg.RSP,
            reg_write=True,
            wb_src=WbSource.ALU,
            alu_a_src=AluASource.RSP,
            alu_b_src=AluBSource.ONE,
            alu_op=Opcode.ADD,
        ),
        MicroInstruction("callr_target_rs1_to_dr", dr_src=DrSource.RS1, dr_latch=True),
        MicroInstruction("callr_pc_from_dr", pc_src=PcSource.DR, pc_latch=True),
    ),
    Opcode.RET: (
        MicroInstruction(
            "ret_decrement_rsp",
            reg_target=RegTarget.FIXED,
            fixed_reg=Reg.RSP,
            reg_write=True,
            wb_src=WbSource.ALU,
            alu_a_src=AluASource.RSP,
            alu_b_src=AluBSource.NEG_ONE,
            alu_op=Opcode.ADD,
        ),
        MicroInstruction("ret_address_rsp_to_ar", ar_src=ArSource.RSP, ar_latch=True),
        MicroInstruction(
            "ret_memory_word_to_dr",
            dr_src=DrSource.MEM,
            dr_latch=True,
            mem_read=True,
        ),
        MicroInstruction("ret_pc_from_dr", pc_src=PcSource.DR, pc_latch=True),
    ),
    Opcode.HLT: (MicroInstruction("halt", halt=True),),
}


def load_binary_to_memory(dp: DataPath, binary_code: bytes) -> None:
    words = binary_to_words(binary_code)
    if len(words) > len(dp.memory):
        raise ValueError("Program does not fit into memory")
    for addr, word in enumerate(words):
        dp.memory[addr] = to_unsigned32(word)


def format_trace_line(cu: ControlUnit, micro_pc: int, micro: MicroInstruction) -> str:
    return (
        f"Tick: {cu.tick_count:06d} | "
        f"uPC: {micro_pc:02d} | "
        f"PC: {cu.pc:04X} | "
        f"IR: {cu.ir:08X} | "
        f"Micro: {micro.name} | "
        f"Signals: {micro.active_signals()} | "
        f"Exec: {format_instruction(cu.ir)} | "
        f"Regs: {cu.dp.dump_registers()} | "
        f"Out: {''.join(cu.dp.output_buffer)!r}"
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
    dp.write_reg(Reg.SP, DATA_STACK_START)
    dp.write_reg(Reg.RSP, RETURN_STACK_START)
    load_binary_to_memory(dp, binary_code)

    cu = ControlUnit(dp)
    trace: list[str] = []
    halt_reason = "halted"
    instructions_fetched = 0
    while not cu.halted and cu.tick_count < max_ticks:
        micro_pc_before = cu.micro_pc
        micro = cu.current_microprogram[micro_pc_before]
        try:
            cu.tick()
        except EOFError:
            halt_reason = "input_stream_empty"
            trace.append(f"Tick: {cu.tick_count:06d} | Event: INPUT_STREAM_EMPTY")
            break
        if micro.name == "fetch_ir_from_mem_pc":
            instructions_fetched += 1
        if len(trace) < trace_head:
            trace.append(format_trace_line(cu, micro_pc_before, micro))

    if cu.tick_count >= max_ticks and not cu.halted:
        raise TimeoutError(f"Simulation stopped after {max_ticks} ticks")

    if trace_head > 0 and cu.tick_count > len(
        [line for line in trace if line.startswith("Tick:")]
    ):
        trace.append("...")
    trace.append(f"Total Ticks: {cu.tick_count}")
    trace.append(f"Instructions Fetched: {instructions_fetched}")
    trace.append(f"Output: {''.join(dp.output_buffer)}")
    trace.append(f"Halt Reason: {halt_reason}")
    return SimulationResult(
        output="".join(dp.output_buffer),
        trace="\n".join(trace),
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
