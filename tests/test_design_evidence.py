"""The requirement evidence tables in docs/DESIGN.md (OOP and SOLID) must stay true: every `Name` (`src/file.py:line`)
points at a line that mentions Name, and every `tests/file.py::name` test or fixture exists."""

import os
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(PROJECT_ROOT, "docs", "DESIGN.md"), encoding="utf-8") as design:
    DESIGN = design.read()


def lines_of(path: str) -> list[str]:
    with open(os.path.join(PROJECT_ROOT, path), encoding="utf-8") as file:
        return file.read().splitlines()


def test_every_code_reference_points_at_the_named_code():
    references = re.findall(r"`(\w+)`[^`|]*?\(`(src/[\w/]+\.py):(\d+)`\)", DESIGN)
    assert len(references) >= 30  # the tables are there
    for name, path, line in references:
        text = lines_of(path)[int(line) - 1]
        # Code, not a comment or docstring: the name is defined, called or assigned on that line
        as_code = re.search(rf"(class {name}\b|\b{name}\s*(\(|=))", text) and not text.strip().startswith(("#", '"'))
        assert as_code, f"DESIGN.md says {name} is at {path}:{line}, but that line is: {text.strip()!r}"


def test_every_test_named_in_the_design_doc_exists():
    named = re.findall(r"`(tests/[\w/]+\.py)::(\w+)`", DESIGN)
    assert len(named) >= 15
    for path, name in named:
        assert any(re.match(rf"\s*def {name}\(", line) for line in lines_of(path)), f"{path}::{name} not found"
