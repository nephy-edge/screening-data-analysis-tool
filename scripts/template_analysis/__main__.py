import sys
from .run_all import run

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m template_analysis <loan_data.csv>")
        sys.exit(1)
    run(sys.argv[1])
