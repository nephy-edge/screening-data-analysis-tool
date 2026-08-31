import pandas as pd


def describe(df: pd.DataFrame):
    return {
        "shape": df.shape,
        "columns": list(df.columns),
    }
