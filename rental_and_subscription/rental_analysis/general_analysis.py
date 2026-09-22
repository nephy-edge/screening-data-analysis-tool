def describe(df):
    """This tab has never been built out into a real business analysis - it
    was a raw dataframe-shape debug stub (previously {"shape": df.shape,
    "columns": list(df.columns)}), which rendered as an unreadable
    comma-joined column-name dump in the UI once the MetricTable row-label
    bug was fixed (the label bug had been hiding that it was ever a
    meaningful "value"). Kept intentionally minimal - two plain, readable
    counts - rather than inventing new business metrics with no source-of-
    truth Excel sheet to match, unlike every other tab in this engine."""
    return {
        "Rows": len(df),
        "Columns": len(df.columns),
    }
