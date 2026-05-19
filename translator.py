from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from isa import (
    IMM_MAX,
    IMM_MIN,
    Opcode,
    Reg,
    binary_to_hex_dump,
    build_instruction,
    code_to_binary,
    fits_imm12,
    to_signed32,
)


@dataclass(frozen=True)
class StringLiteral:
    value: str


Token = str | StringLiteral


@dataclass
class Patch:
    index: int
    name: str


@dataclass
class DataPatch:
    index: int
    rd: Reg
    offset: int


class Compiler:
    def __init__(self, definitions: dict[str, list[Token]]) -> None:
        self.definitions = definitions
        self.code: list[int] = []
        self.data: list[int] = []
        self.proc_addrs: dict[str, int] = {}
        self.call_patches: list[Patch] = []
        self.xt_patches: list[Patch] = []
        self.data_patches: list[DataPatch] = []

    def compile_program(self, main_tokens: list[Token]) -> tuple[list[int], int]:
        self.compile_tokens(main_tokens, is_definition=False)
        self.code.append(build_instruction(Opcode.HLT))

        for name, body in self.definitions.items():
            self.proc_addrs[name] = len(self.code)
            self.compile_tokens(body, is_definition=True)
            self.code.append(build_instruction(Opcode.RET))

        self.patch_procedure_addresses()
        self.patch_data_addresses()
        code_words = len(self.code)
        return self.code + self.data, code_words

    def patch_procedure_addresses(self) -> None:
        for patch in self.call_patches:
            if patch.name not in self.proc_addrs:
                raise ValueError(f"Unknown procedure: {patch.name}")
            target = self.proc_addrs[patch.name]
            if not fits_imm12(target):
                raise ValueError(
                    f"Procedure address {target} is not encodable as imm12"
                )
            self.code[patch.index] = build_instruction(Opcode.CALL, imm=target)

        for patch in self.xt_patches:
            if patch.name not in self.proc_addrs:
                raise ValueError(f"Unknown execution token target: {patch.name}")
            target = self.proc_addrs[patch.name]
            if not fits_imm12(target):
                raise ValueError(
                    f"Execution token address {target} is not encodable as imm12"
                )
            self.code[patch.index] = build_instruction(Opcode.LDI, Reg.R1, imm=target)

    def patch_data_addresses(self) -> None:
        data_base = len(self.code)
        for patch in self.data_patches:
            address = data_base + patch.offset
            if not fits_imm12(address):
                raise ValueError(f"Data address {address} is not encodable as imm12")
            self.code[patch.index] = build_instruction(
                Opcode.LDI, patch.rd, imm=address
            )

    def compile_tokens(self, tokens: list[Token], is_definition: bool) -> None:
        control_stack: list[tuple[str, int]] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token == "'":
                if index + 1 >= len(tokens):
                    raise ValueError("Execution token must be followed by a word name")
                next_token = tokens[index + 1]
                if not isinstance(next_token, str):
                    raise ValueError("Execution token must be followed by a word name")
                self.compile_execution_token(next_token)
                index += 2
                continue
            if token == '."':
                if index + 1 >= len(tokens):
                    raise ValueError('." must be followed by a string literal')
                next_token = tokens[index + 1]
                if not isinstance(next_token, StringLiteral):
                    raise ValueError('." must be followed by a string literal')
                self.compile_string_literal(next_token.value)
                self.compile_puts()
                index += 2
                continue
            self.compile_word(token, control_stack)
            index += 1

        if control_stack:
            kind, _ = control_stack[-1]
            raise ValueError(f"Unclosed control structure: {kind}")
        if not is_definition and any(token == "ret" for token in tokens):
            raise ValueError("ret is only valid inside a procedure")

    def compile_word(self, token: Token, control_stack: list[tuple[str, int]]) -> None:
        if isinstance(token, StringLiteral):
            self.compile_string_literal(token.value)
            return

        if is_int(token):
            self.compile_int_literal(int(token))
            return

        if token in {"+", "-", "*", "/", "mod", "=", ">", "<"}:
            self.compile_binary_op(token)
            return

        if token == "dup":
            self.code.extend(pop_asm(Reg.R1))
            self.code.extend(push_asm(reg=Reg.R1))
            self.code.extend(push_asm(reg=Reg.R1))
            return

        if token == "drop":
            self.code.extend(pop_asm(Reg.R1))
            return

        if token == "swap":
            self.code.extend(pop_asm(Reg.R1))
            self.code.extend(pop_asm(Reg.R2))
            self.code.extend(push_asm(reg=Reg.R1))
            self.code.extend(push_asm(reg=Reg.R2))
            return

        if token == "over":
            self.code.extend(pop_asm(Reg.R1))
            self.code.extend(pop_asm(Reg.R2))
            self.code.extend(push_asm(reg=Reg.R2))
            self.code.extend(push_asm(reg=Reg.R1))
            self.code.extend(push_asm(reg=Reg.R2))
            return

        if token == "emit":
            self.code.extend(pop_asm(Reg.R1))
            self.code.extend(
                [
                    build_instruction(Opcode.LDI, Reg.R2, imm=-1),
                    build_instruction(Opcode.ST, Reg.R2, Reg.R1),
                ]
            )
            return

        if token == ".":
            self.code.extend(pop_asm(Reg.R1))
            self.code.extend(
                [
                    build_instruction(Opcode.LDI, Reg.R2, imm=-3),
                    build_instruction(Opcode.ST, Reg.R2, Reg.R1),
                ]
            )
            return

        if token == "key":
            self.code.extend(
                [
                    build_instruction(Opcode.LDI, Reg.R2, imm=-2),
                    build_instruction(Opcode.LD, Reg.R1, Reg.R2),
                ]
            )
            self.code.extend(push_asm(reg=Reg.R1))
            return

        if token == "@":
            self.code.extend(pop_asm(Reg.R1))
            self.code.append(build_instruction(Opcode.LD, Reg.R1, Reg.R1))
            self.code.extend(push_asm(reg=Reg.R1))
            return

        if token == "!":
            self.code.extend(pop_asm(Reg.R2))
            self.code.extend(pop_asm(Reg.R1))
            self.code.append(build_instruction(Opcode.ST, Reg.R2, Reg.R1))
            return

        if token == "if":
            self.code.extend(pop_asm(Reg.R1))
            control_stack.append(("if", len(self.code)))
            self.code.append(0)
            return

        if token == "else":
            kind, if_index = control_stack.pop()
            if kind != "if":
                raise ValueError("else without matching if")
            jmp_index = len(self.code)
            self.code.append(0)
            self.code[if_index] = build_instruction(
                Opcode.JZ, rs1=Reg.R1, imm=len(self.code)
            )
            control_stack.append(("else", jmp_index))
            return

        if token == "then":
            kind, index = control_stack.pop()
            if kind == "if":
                self.code[index] = build_instruction(
                    Opcode.JZ, rs1=Reg.R1, imm=len(self.code)
                )
            elif kind == "else":
                self.code[index] = build_instruction(Opcode.JMP, imm=len(self.code))
            else:
                raise ValueError("then without matching if/else")
            return

        if token == "begin":
            control_stack.append(("begin", len(self.code)))
            return

        if token == "until":
            kind, begin_index = control_stack.pop()
            if kind != "begin":
                raise ValueError("until without matching begin")
            self.code.extend(pop_asm(Reg.R1))
            self.code.append(build_instruction(Opcode.JZ, rs1=Reg.R1, imm=begin_index))
            return

        if token == "again":
            kind, begin_index = control_stack.pop()
            if kind != "begin":
                raise ValueError("again without matching begin")
            self.code.append(build_instruction(Opcode.JMP, imm=begin_index))
            return

        if token == "puts":
            self.compile_puts()
            return

        if token == "execute":
            self.code.extend(pop_asm(Reg.R1))
            self.code.append(build_instruction(Opcode.CALLR, rs1=Reg.R1))
            return

        if token == "ret":
            self.code.append(build_instruction(Opcode.RET))
            return

        if token in self.definitions:
            self.call_patches.append(Patch(len(self.code), token))
            self.code.append(0)
            return

        raise ValueError(f"Unknown word: {token}")

    def compile_int_literal(self, value: int) -> None:
        if fits_imm12(value):
            self.code.extend(push_asm(val=value))
            return

        # The ISA has only a 12-bit immediate field.  Larger integer literals
        # are allocated in the static data area and loaded by address, so they
        # are not silently truncated during instruction encoding.
        offset = len(self.data)
        self.data.append(to_signed32(value))
        self.data_patches.append(DataPatch(len(self.code), Reg.R1, offset))
        self.code.append(0)
        self.code.append(build_instruction(Opcode.LD, Reg.R1, Reg.R1))
        self.code.extend(push_asm(reg=Reg.R1))

    def compile_binary_op(self, token: str) -> None:
        opcodes = {
            "+": Opcode.ADD,
            "-": Opcode.SUB,
            "*": Opcode.MUL,
            "/": Opcode.DIV,
            "mod": Opcode.MOD,
            "=": Opcode.CMP_EQ,
            ">": Opcode.CMP_GT,
            "<": Opcode.CMP_LT,
        }
        self.code.extend(pop_asm(Reg.R2))
        self.code.extend(pop_asm(Reg.R1))
        self.code.append(build_instruction(opcodes[token], Reg.R1, Reg.R1, Reg.R2))
        self.code.extend(push_asm(reg=Reg.R1))

    def compile_execution_token(self, name: str) -> None:
        self.xt_patches.append(Patch(len(self.code), name))
        self.code.append(0)
        self.code.extend(push_asm(reg=Reg.R1))

    def compile_string_literal(self, value: str) -> None:
        offset = len(self.data)
        self.data.extend(ord(char) for char in value)
        self.data.append(0)
        self.data_patches.append(DataPatch(len(self.code), Reg.R1, offset))
        self.code.append(0)
        self.code.extend(push_asm(reg=Reg.R1))

    def compile_puts(self) -> None:
        self.code.extend(pop_asm(Reg.R1))
        loop_index = len(self.code)
        self.code.extend(
            [
                build_instruction(Opcode.LD, Reg.R2, Reg.R1),
                0,  # patched JZ R2, end
                build_instruction(Opcode.LDI, Reg.R3, imm=-1),
                build_instruction(Opcode.ST, Reg.R3, Reg.R2),
                build_instruction(Opcode.LDI, Reg.R3, imm=1),
                build_instruction(Opcode.ADD, Reg.R1, Reg.R1, Reg.R3),
                build_instruction(Opcode.JMP, imm=loop_index),
            ]
        )
        end_index = len(self.code)
        self.code[loop_index + 1] = build_instruction(
            Opcode.JZ, rs1=Reg.R2, imm=end_index
        )


def push_asm(val: int | None = None, reg: Reg | None = None) -> list[int]:
    instructions: list[int] = []
    if val is not None:
        if not fits_imm12(val):
            raise ValueError(
                f"Immediate literal {val} does not fit into [{IMM_MIN}, {IMM_MAX}]"
            )
        instructions.append(build_instruction(Opcode.LDI, Reg.R1, imm=val))
        reg = Reg.R1
    if reg is None:
        raise ValueError("push_asm requires either val or reg")
    instructions.extend(
        [
            build_instruction(Opcode.ST, Reg.SP, reg),
            build_instruction(Opcode.LDI, Reg.R3, imm=1),
            build_instruction(Opcode.ADD, Reg.SP, Reg.SP, Reg.R3),
        ]
    )
    return instructions


def pop_asm(reg: Reg) -> list[int]:
    return [
        build_instruction(Opcode.LDI, Reg.R3, imm=1),
        build_instruction(Opcode.SUB, Reg.SP, Reg.SP, Reg.R3),
        build_instruction(Opcode.LD, reg, Reg.SP),
    ]


def is_int(token: str) -> bool:
    return token.isdigit() or (token.startswith("-") and token[1:].isdigit())


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    while i < len(source):
        char = source[i]
        if char.isspace():
            i += 1
            continue
        if char == "\\":
            while i < len(source) and source[i] != "\n":
                i += 1
            continue
        if char == "(":
            i += 1
            while i < len(source) and source[i] != ")":
                i += 1
            i += 1
            continue
        if source.startswith('."', i):
            i += 2
            if i < len(source) and source[i] == " ":
                i += 1
            literal, i = read_string(source, i)
            tokens.append('."')
            tokens.append(StringLiteral(literal))
            continue
        if char == '"':
            literal, i = read_string(source, i + 1)
            tokens.append(StringLiteral(literal))
            continue
        start = i
        while i < len(source) and not source[i].isspace():
            if source[i] in {'"', "("}:
                break
            i += 1
        tokens.append(source[start:i])
    return tokens


def read_string(source: str, start: int) -> tuple[str, int]:
    result: list[str] = []
    i = start
    while i < len(source):
        char = source[i]
        if char == '"':
            return "".join(result), i + 1
        if char == "\\" and i + 1 < len(source):
            escapes = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
            i += 1
            result.append(escapes.get(source[i], source[i]))
        else:
            result.append(char)
        i += 1
    raise ValueError("Unterminated string literal")


def split_definitions(
    tokens: list[Token],
) -> tuple[list[Token], dict[str, list[Token]]]:
    main: list[Token] = []
    definitions: dict[str, list[Token]] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == ":":
            if index + 1 >= len(tokens):
                raise ValueError("Procedure definition requires a name")
            name = tokens[index + 1]
            if not isinstance(name, str):
                raise ValueError("Procedure definition requires a name")
            index += 2
            body: list[Token] = []
            while index < len(tokens) and tokens[index] != ";":
                body.append(tokens[index])
                index += 1
            if index >= len(tokens):
                raise ValueError(f"Procedure {name} is not closed with ';'")
            definitions[name] = body
            index += 1
        else:
            main.append(token)
            index += 1
    return main, definitions


def translate(source: str) -> tuple[list[int], int]:
    tokens = tokenize(source)
    main_tokens, definitions = split_definitions(tokens)
    return Compiler(definitions).compile_program(main_tokens)


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: translator.py <in.frt> <out.bin>")
        sys.exit(1)

    src_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    source = src_path.read_text(encoding="utf-8")
    machine_code, code_words = translate(source)
    binary = code_to_binary(machine_code)
    out_path.write_bytes(binary)
    out_path.with_suffix(out_path.suffix + ".hex").write_text(
        binary_to_hex_dump(binary, code_words=code_words),
        encoding="utf-8",
    )
    print("Translation finished.")


if __name__ == "__main__":
    main()
