"""Creates/replaces
MAESTRO_DEV_ANA.SALMON_INSTALLMENT_LOANS_SC.SALMON_INSTALLMENT_LOANS_FLAT_V2
from flatten_installment_loans.sql. Run once, and again any time that .sql
file changes.

Run:
    .venv\\Scripts\\python.exe -m snowflake_lending.create_flat_view
"""

from pathlib import Path

from .connection import build_session

SQL_FILE = Path(__file__).resolve().parent / "flatten_installment_loans.sql"


def create_flat_view() -> None:
    sql = SQL_FILE.read_text()
    session = build_session()
    try:
        session.sql(sql).collect()
        print(
            "Created/replaced "
            "MAESTRO_DEV_ANA.SALMON_INSTALLMENT_LOANS_SC.SALMON_INSTALLMENT_LOANS_FLAT_V2"
        )
    finally:
        session.close()


if __name__ == "__main__":
    create_flat_view()
