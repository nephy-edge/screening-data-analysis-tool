"""One-time (and re-run-on-change) deploy of RUN_LENDING_ANALYSIS into
Snowflake as a Snowpark Python stored procedure.

Run manually from the repo root:

    .venv\\Scripts\\python.exe -m snowflake_lending.deploy

Re-run this whenever scripts/template_analysis, column_mapping.py, or
proc_handler.py change - it replaces the procedure in place.
"""

from __future__ import annotations

import os
from pathlib import Path

import cloudpickle

from . import proc_handler as proc_handler_module
from .connection import build_session

# session.sproc.register() pickles the handler function by *reference*
# (module path + qualname) by default, since proc_handler is a real,
# importable module rather than something defined in __main__ - but that
# module itself isn't staged into the Snowflake sandbox (only
# scripts/template_analysis and column_mapping.py are, via `imports=`
# below), so the remote side fails with "No module named 'snowflake_lending'"
# when it tries to unpickle. Forcing by-value serialization embeds the
# function's actual bytecode in the registration instead of a module
# reference, sidestepping the need to stage this package too.
cloudpickle.register_pickle_by_value(proc_handler_module)
run_lending_analysis = proc_handler_module.run_lending_analysis

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = REPO_ROOT / "scripts" / "template_analysis"
COLUMN_MAPPING_FILE = Path(__file__).resolve().parent / "column_mapping.py"

PROC_NAME = "RUN_LENDING_ANALYSIS"


def deploy() -> None:
    if not ENGINE_DIR.is_dir():
        raise FileNotFoundError(f"Engine dir not found: {ENGINE_DIR}")

    session = build_session()
    stage = os.environ.get("SNOWFLAKE_STAGE", "LENDING_ANALYSIS_STAGE")
    session.sql(f"CREATE STAGE IF NOT EXISTS {stage}").collect()

    try:
        session.sproc.register(
            func=run_lending_analysis,
            name=PROC_NAME,
            is_permanent=True,
            stage_location=f"@{stage}",
            replace=True,
            # pyarrow: required internally by session.table(...).to_pandas()'s
            # Arrow-based fetch inside the sandbox, not directly imported by
            # this code - omitting it fails with a confusing "'pandas' is not
            # installed" error from the connector's pandas-optional-deps check.
            packages=["pandas", "numpy", "numpy_financial", "pyarrow"],
            imports=[str(ENGINE_DIR), str(COLUMN_MAPPING_FILE)],
        )
    except Exception as exc:
        # numpy_financial isn't guaranteed to be in every account's Snowflake
        # Anaconda channel. If registration fails specifically on that
        # package, bundle its (pure-Python) source dir via `imports=` instead
        # of declaring it as a channel package - see Snowpark's
        # session.sproc.register docs for the imports= pattern.
        raise RuntimeError(
            "Registration failed - if this is a numpy_financial package "
            "error, see the fallback noted in this except block."
        ) from exc

    print(f"Deployed {PROC_NAME} (stage: @{stage})")


if __name__ == "__main__":
    deploy()
