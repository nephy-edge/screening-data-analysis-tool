"""Read a few literal values out of app.py/scripts/ai_client.py's source
without importing them.

app.py is a top-level Streamlit script (calls st.set_page_config() and other
UI code at module scope), so `import app` executes the whole page and blows
up outside a real Streamlit run - it is not safely importable for tests. The
handful of AI-mapping constants tests need (FIELD_DESCRIPTIONS, the lending
model's field list) are plain literals, so we parse them out of the AST
instead of duplicating them by hand - a hand-copied duplicate is exactly how
the rental fields silently ended up with no descriptions in the first place
(fixed 2026-09-11, see docs/progress/CHANGES_2026-09-11.md).
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_PY = REPO_ROOT / "app.py"
AI_CLIENT_PY = REPO_ROOT / "scripts" / "ai_client.py"


def _parse_module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def get_field_descriptions() -> dict:
    """The FIELD_DESCRIPTIONS dict literal from scripts/ai_client.py."""
    tree = _parse_module(AI_CLIENT_PY)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "FIELD_DESCRIPTIONS" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(
        "FIELD_DESCRIPTIONS assignment not found in scripts/ai_client.py - did it get renamed?"
    )


def get_lending_input_columns() -> list[tuple[str, bool]]:
    """LENDING_CONFIG['input_columns'] - a literal list of (field, required) pairs."""
    tree = _parse_module(APP_PY)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "LENDING_CONFIG" for t in node.targets)
            and isinstance(node.value, ast.Call)
        ):
            for kw in node.value.keywords:
                if kw.arg == "input_columns":
                    return ast.literal_eval(kw.value)
    raise AssertionError("LENDING_CONFIG.input_columns not found in app.py - did it get renamed?")
