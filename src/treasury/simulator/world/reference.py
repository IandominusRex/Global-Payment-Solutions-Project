"""Static reference data: ISO 20022-style reason codes and purpose codes."""

# category drives the failure Pareto (analysis 4); is_fixable_at_source drives the "so what".
FAILURE_REASONS = [
    {"reason_code": "AC01", "description": "Incorrect account number", "category": "data", "is_fixable_at_source": True},
    {"reason_code": "AC04", "description": "Closed account number", "category": "data", "is_fixable_at_source": True},
    {"reason_code": "BE04", "description": "Missing creditor address", "category": "data", "is_fixable_at_source": True},
    {"reason_code": "RR03", "description": "Missing creditor name or address (regulatory)", "category": "data", "is_fixable_at_source": True},
    {"reason_code": "RC01", "description": "Bank identifier incorrect", "category": "data", "is_fixable_at_source": True},
    {"reason_code": "AM04", "description": "Insufficient funds", "category": "funds", "is_fixable_at_source": False},
    {"reason_code": "AM02", "description": "Amount exceeds allowed maximum", "category": "funds", "is_fixable_at_source": True},
    {"reason_code": "AG01", "description": "Transaction forbidden", "category": "compliance", "is_fixable_at_source": False},
    {"reason_code": "RR04", "description": "Regulatory reason", "category": "compliance", "is_fixable_at_source": False},
    {"reason_code": "FF01", "description": "Invalid file format", "category": "technical", "is_fixable_at_source": True},
    {"reason_code": "MS03", "description": "Reason not specified", "category": "technical", "is_fixable_at_source": False},
    {"reason_code": "CUST", "description": "Requested by customer (return)", "category": "other", "is_fixable_at_source": False},
]

PURPOSE_CODES = [
    {"purpose_code": "SUPP", "description": "Supplier payment"},
    {"purpose_code": "SALA", "description": "Salary payment"},
    {"purpose_code": "TAXS", "description": "Tax payment"},
    {"purpose_code": "INTC", "description": "Intra-company payment"},
    {"purpose_code": "CASH", "description": "Cash management transfer"},
    {"purpose_code": "GDDS", "description": "Purchase or sale of goods"},
    {"purpose_code": "SCVE", "description": "Purchase or sale of services"},
    {"purpose_code": "TREA", "description": "Treasury payment"},
]
