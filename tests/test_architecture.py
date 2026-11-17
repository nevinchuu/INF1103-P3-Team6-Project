"""The layers stay apart (rules 7 and 8 of the D1 design): each layer has its own folder in src/, the managers never
import each other, only IO-side code imports all three, print() lives only in io_manager.py, and web_app.py names the
managers only in from_environment."""

import ast
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")

MANAGER_MODULES = {"ai_manager", "logic_manager", "data_manager"}
MANAGER_CLASSES = {"AIManager", "LogicManager", "DataManager"}
# Each manager's layer: the manager and every class below it
AI_LAYER = ["ai_manager", "ai_provider", "caching_ai_provider", "gemini_provider"]
LOGIC_LAYER = ["logic_manager", "tailoring_guard", "career_simulator", "ranking_rules", "match_rule",
               "education_rule", "experience_rule", "seniority_rule", "roadmap_planner", "greedy_planner",
               "salary_weighted_planner", "skill_name"]
DATA_LAYER = ["data_manager", "storage", "postgres_storage", "job_portal", "data_store_error"]
# The only modules allowed to import a manager
IO_SIDE = {"io_manager", "input_validator", "search_service", "tailoring_service", "resume_tailor", "word_resume_tailor",
           "pdf_resume_tailor", "web_app"}
# IO-side classes that never touch a manager
IO_SIDE_PLAIN = {"resume_files", "docx_editor", "task_registry"}
# The folder in src/ that holds each layer (web_app.py stays in src/ itself, next to templates/ and static/)
FOLDERS = {"ai_layer": AI_LAYER, "logic_layer": LOGIC_LAYER, "data_layer": DATA_LAYER,
           "io_layer": sorted((IO_SIDE | IO_SIDE_PLAIN) - {"web_app"})}
# Every source file by module name, e.g. "ai_manager" -> ".../src/ai_layer/ai_manager.py"
SOURCES = {os.path.basename(p)[:-3]: p for p in glob.glob(os.path.join(SRC, "**", "*.py"), recursive=True)}


def imports(module: str) -> set[str]:
    """Module names imported anywhere in the file (also under TYPE_CHECKING or inside functions), without their folder:
    "from ai_layer.ai_manager import AIManager" and "from ai_layer import ai_manager" both give "ai_manager"."""
    tree = ast.parse(open(SOURCES[module], encoding="utf-8").read())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[-1] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module in FOLDERS:
                found |= {alias.name for alias in node.names}
            else:
                found.add(node.module.split(".")[-1])
    return found


def test_the_three_layers_list_every_source_file_once():
    files = set(SOURCES)
    layers = AI_LAYER + LOGIC_LAYER + DATA_LAYER
    assert len(layers) == len(set(layers))
    assert files == set(layers) | IO_SIDE | IO_SIDE_PLAIN, files ^ (set(layers) | IO_SIDE | IO_SIDE_PLAIN)


def test_each_layer_is_in_its_own_folder():
    for folder, modules in FOLDERS.items():
        for module in modules:
            assert SOURCES[module] == os.path.join(SRC, folder, module + ".py"), f"{module}.py is not in src/{folder}/"
    assert SOURCES["web_app"] == os.path.join(SRC, "web_app.py")


def test_the_managers_never_import_each_other():
    for layer, forbidden in ((AI_LAYER, MANAGER_MODULES - {"ai_manager"}),
                             (LOGIC_LAYER, MANAGER_MODULES - {"logic_manager"}),
                             (DATA_LAYER, MANAGER_MODULES - {"data_manager"})):
        for module in layer:
            assert not imports(module) & forbidden, f"{module}.py imports another manager: {imports(module) & forbidden}"


def test_only_io_side_code_imports_a_manager():
    for module in SOURCES:
        used = imports(module) & MANAGER_MODULES
        if module in AI_LAYER + LOGIC_LAYER + DATA_LAYER:
            # a class below a manager may name its own manager (CareerSimulator takes a LogicManager per call)
            own = {"ai_manager": AI_LAYER, "logic_manager": LOGIC_LAYER, "data_manager": DATA_LAYER}
            used -= {m for m, layer in own.items() if module in layer}
            assert not used, f"{module}.py imports {used}"
        elif used:
            assert module in IO_SIDE, f"{module}.py imports {used} but is not IO-side code"


def test_print_is_only_used_in_the_io_manager():
    for path in SOURCES.values():
        calls = [n for n in ast.walk(ast.parse(open(path, encoding="utf-8").read()))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print"]
        if os.path.basename(path) != "io_manager.py":
            assert not calls, f"{os.path.basename(path)} calls print()"


def test_the_web_app_names_the_managers_only_in_from_environment():
    path = os.path.join(SRC, "web_app.py")
    source = open(path, encoding="utf-8").read()
    tree = ast.parse(source)
    web_app = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WebApp")
    for method in (n for n in web_app.body if isinstance(n, ast.FunctionDef)):
        text = ast.get_source_segment(source, method)
        mentioned = {name for name in MANAGER_CLASSES if re.search(rf"\b{name}\b", text)}
        if method.name == "from_environment":
            assert mentioned == MANAGER_CLASSES
        else:
            assert not mentioned, f"WebApp.{method.name} names {mentioned}"
    lines = [line for line in source.splitlines() if re.search(r"\b(AIManager|LogicManager|DataManager)\b", line)
             and not line.lstrip().startswith(("from ", "import ", "#", '"""'))]
    inside = ast.get_source_segment(source, next(m for m in web_app.body if getattr(m, "name", "") == "from_environment"))
    outside = [line for line in lines if line.strip() not in inside]
    assert not outside, f"managers named outside from_environment: {outside}"
