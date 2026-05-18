# Лабораторная работа №4 — golden tests

Вариант:

```text
forth | risc | neum | mc | tick | binary | stream | mem | cstr | alg1
```

## Структура проекта

```text
golden/          # YAML golden tests
examples/        # программы на Forth-подобном языке
isa.py           # описание ISA и бинарного кодирования инструкций
machine.py       # тактовая модель процессора с microcode ROM
translator.py    # транслятор .frt -> .bin + .hex
test_golden.py   # pytest golden tests
README.md
.github/workflows/ci.yml  # GitHub Actions: ruff format, ruff check, mypy, pytest
```

## Реализованные тестовые программы

- `hello.frt` — печать `Hello, world!`, демонстрация `cstr` и хранения строки в памяти данных.
- `cat.frt` — печать данных, подаваемых через stream-ввод, до окончания входного потока.
- `hello_user_name.frt` — запрос имени пользователя, чтение stream-ввода и вывод приветствия.
- `sort.frt` — сортировка трёх входных символов-цифр через память и сравнения.
- `double_precision.frt` — демонстрация числа больше 32 бит: `2^32 = 4294967296`.
- `euler4.frt` — алгоритм варианта `alg1`: максимальный палиндром-произведение двух трёхзначных чисел.
- `features.frt` — процедуры Forth и execution token: `' square execute`.

## Формат golden-файлов

Каждый файл `golden/*.yml` содержит:

```yaml
in_source: |-
  исходный код программы
in_input: |-
  входной stream
out_stdout: |-
  итоговый вывод процессора
out_log: |-
  репрезентативный журнал микротактов
out_code_log: |-
  hex dump машинного кода с мнемониками
```

Для варианта `stream` используется поле `in_input`, а не `in_schedule`, потому что ввод поступает как поток символов, а не как расписание trap-прерываний.


## CI / lint / formatter

Проверки запускаются через GitHub Actions в `.github/workflows/ci.yml`:

```bash
ruff format --check .
ruff check .
mypy isa.py machine.py translator.py test_golden.py --ignore-missing-imports
pytest -v
```

Локально запускать эти команды необязательно, но удобно перед push-ем.

## Запуск автоматических тестов

```bash
python -m pytest -v
```

## Обновление golden-файлов

```bash
UPDATE_GOLDEN=1 python -m pytest -v
```

Обновлять эталоны стоит только после осознанных изменений транслятора, ISA или модели процессора.

## Ручная трансляция и запуск

Пример для `cat.frt`:

```bash
python translator.py examples/cat.frt target.bin
printf 'abcXYZ' > input.txt
python machine.py target.bin input.txt
```

После трансляции рядом с `target.bin` появится файл `target.bin.hex` с человекочитаемым листингом машинного кода.
