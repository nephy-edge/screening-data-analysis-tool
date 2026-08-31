def filter_cohorts(cohorts, min_loans_per_cohort):
    return cohorts[cohorts["new_leases"] >= min_loans_per_cohort].copy()
