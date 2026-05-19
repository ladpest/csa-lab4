from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from isa import binary_to_hex_dump, code_to_binary  # noqa: E402
from machine import MAX_TICKS, simulate  # noqa: E402
from translator import translate  # noqa: E402


def parse_block_yaml(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    result: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []
    chomping_strip = True

    def flush() -> None:
        nonlocal current_key, current_lines, chomping_strip
        if current_key is None:
            return
        value = "\n".join(current_lines)
        if not chomping_strip:
            value += "\n"
        result[current_key] = value
        current_key = None
        current_lines = []
        chomping_strip = True

    for raw_line in text.splitlines():
        if raw_line and not raw_line.startswith(" ") and raw_line.endswith(("|-", "|")):
            flush()
            key, marker = raw_line.split(":", 1)
            current_key = key.strip()
            chomping_strip = marker.strip() == "|-"
            current_lines = []
            continue
        if current_key is None:
            if raw_line.strip():
                raise ValueError(f"Unsupported YAML line in {path}: {raw_line!r}")
            continue
        if raw_line.startswith("  "):
            current_lines.append(raw_line[2:])
        elif raw_line == "":
            current_lines.append("")
        else:
            raise ValueError(f"Expected indented block line in {path}: {raw_line!r}")
    flush()
    return result


def run_with_trace(
    binary: bytes, input_text: str, max_ticks: int, trace_head: int
) -> tuple[str, str]:
    result = simulate(
        binary,
        input_text,
        max_ticks=max_ticks,
        trace_head=trace_head,
    )
    return result.output, result.trace


def build_actual(case_path: Path) -> dict[str, str]:
    golden = parse_block_yaml(case_path)
    source = golden["in_source"]
    input_text = golden.get("in_input", "")
    max_ticks = 10_000_000 if case_path.stem == "euler4" else MAX_TICKS
    trace_head = 120 if case_path.stem == "euler4" else 100
    words, code_words = translate(source)
    binary = code_to_binary(words)
    out_code_log = binary_to_hex_dump(binary, code_words=code_words)
    out_stdout, out_log = run_with_trace(
        binary, input_text, max_ticks=max_ticks, trace_head=trace_head
    )
    return {
        "in_source": source,
        "in_input": input_text,
        "out_stdout": out_stdout,
        "out_log": out_log,
        "out_code_log": out_code_log,
    }


def dump_block_yaml(data: dict[str, str]) -> str:
    parts: list[str] = []
    for key in ["in_source", "in_input", "out_stdout", "out_log", "out_code_log"]:
        value = data[key]
        marker = "|" if value.endswith("\n") else "|-"
        body = value[:-1] if marker == "|" else value
        parts.append(f"{key}: {marker}")
        if body:
            parts.extend(f"  {line}" for line in body.split("\n"))
        else:
            parts.append("  ")
    return "\n".join(parts) + "\n"


CASES = sorted((ROOT / "golden").glob("*.yml"))


@pytest.mark.parametrize("case_path", CASES, ids=[path.stem for path in CASES])
def test_golden(case_path: Path) -> None:
    actual = build_actual(case_path)
    actual_text = dump_block_yaml(actual)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        case_path.write_text(actual_text, encoding="utf-8")
    expected_text = case_path.read_text(encoding="utf-8")
    assert actual_text == expected_text


def test_examples_match_golden_sources() -> None:
    for case_path in CASES:
        golden = parse_block_yaml(case_path)
        example_path = ROOT / "examples" / f"{case_path.stem}.frt"
        assert example_path.exists(), f"Missing example: {example_path}"
        assert example_path.read_text(encoding="utf-8") == golden["in_source"]


def run_source(source: str, input_text: str = "") -> str:
    words, _ = translate(source)
    return simulate(code_to_binary(words), input_text, max_ticks=MAX_TICKS).output


def test_large_integer_literal_is_loaded_from_data_pool() -> None:
    assert run_source("4096 .") == "4096 "


def test_machine_words_wrap_to_32_bits() -> None:
    assert run_source("-1 1 + .") == "0 "
