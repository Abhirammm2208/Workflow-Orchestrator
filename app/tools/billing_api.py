"""
app/tools/billing_api.py
Mock Billing API tool.

Simulates an external billing microservice that returns invoice and payment
data for a given customer_id. In production, replace the body of
`fetch_billing_info` with a real HTTP call to your billing service.

The mock uses deterministic data keyed by customer_id suffix so that the
same customer always returns the same data — important for idempotency tests.
"""

import hashlib
from datetime import datetime, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Static billing data pool — 20 realistic billing scenarios
# Indexed by a hash of customer_id so lookups are deterministic
# ---------------------------------------------------------------------------
_BILLING_SCENARIOS: list[dict[str, Any]] = [
    {
        "invoice_id": "INV-10041",
        "plan": "Pro Monthly",
        "amount_due": "$49.99",
        "currency": "USD",
        "invoice_status": "overdue",
        "due_date": "2026-09-15",
        "last_payment_date": "2026-08-15",
        "last_payment_amount": "$49.99",
        "outstanding_balance": "$49.99",
        "payment_method": "Visa **** 4242",
        "auto_renewal": True,
        "account_suspended": False,
        "notes": "Payment failed — card expired",
    },
    {
        "invoice_id": "INV-10088",
        "plan": "Enterprise Annual",
        "amount_due": "$0.00",
        "currency": "USD",
        "invoice_status": "paid",
        "due_date": "2026-10-01",
        "last_payment_date": "2026-10-01",
        "last_payment_amount": "$2,388.00",
        "outstanding_balance": "$0.00",
        "payment_method": "ACH Bank Transfer",
        "auto_renewal": True,
        "account_suspended": False,
        "notes": "Annual contract — next renewal 2027-10-01",
    },
    {
        "invoice_id": "INV-10123",
        "plan": "Starter Monthly",
        "amount_due": "$9.99",
        "currency": "USD",
        "invoice_status": "pending",
        "due_date": "2026-10-10",
        "last_payment_date": "2026-09-10",
        "last_payment_amount": "$9.99",
        "outstanding_balance": "$9.99",
        "payment_method": "Mastercard **** 5555",
        "auto_renewal": True,
        "account_suspended": False,
        "notes": "Invoice generated, awaiting payment",
    },
    {
        "invoice_id": "INV-10156",
        "plan": "Pro Monthly",
        "amount_due": "$99.98",
        "currency": "USD",
        "invoice_status": "overdue",
        "due_date": "2026-08-20",
        "last_payment_date": "2026-07-20",
        "last_payment_amount": "$49.99",
        "outstanding_balance": "$99.98",
        "payment_method": "Visa **** 1234",
        "auto_renewal": False,
        "account_suspended": True,
        "notes": "Double billing reported — two invoices generated for August",
    },
    {
        "invoice_id": "INV-10199",
        "plan": "Team Monthly",
        "amount_due": "$0.00",
        "currency": "USD",
        "invoice_status": "refunded",
        "due_date": "2026-09-28",
        "last_payment_date": "2026-09-28",
        "last_payment_amount": "$149.00",
        "outstanding_balance": "$0.00",
        "payment_method": "PayPal",
        "auto_renewal": True,
        "account_suspended": False,
        "notes": "Refund processed on 2026-10-02 — duplicate charge confirmed",
    },
    {
        "invoice_id": "INV-10234",
        "plan": "Starter Monthly",
        "amount_due": "$9.99",
        "currency": "USD",
        "invoice_status": "failed",
        "due_date": "2026-10-05",
        "last_payment_date": "2026-09-05",
        "last_payment_amount": "$9.99",
        "outstanding_balance": "$9.99",
        "payment_method": "Visa **** 9876",
        "auto_renewal": True,
        "account_suspended": False,
        "notes": "Payment gateway timeout — retry scheduled",
    },
    {
        "invoice_id": "INV-10278",
        "plan": "Pro Annual",
        "amount_due": "$0.00",
        "currency": "USD",
        "invoice_status": "paid",
        "due_date": "2026-01-15",
        "last_payment_date": "2026-01-15",
        "last_payment_amount": "$479.88",
        "outstanding_balance": "$0.00",
        "payment_method": "Corporate Card **** 6600",
        "auto_renewal": True,
        "account_suspended": False,
        "notes": "Paid in full — valid until 2027-01-15",
    },
    {
        "invoice_id": "INV-10311",
        "plan": "Team Monthly",
        "amount_due": "$149.00",
        "currency": "USD",
        "invoice_status": "disputed",
        "due_date": "2026-09-30",
        "last_payment_date": "2026-08-30",
        "last_payment_amount": "$149.00",
        "outstanding_balance": "$149.00",
        "payment_method": "Mastercard **** 3311",
        "auto_renewal": False,
        "account_suspended": False,
        "notes": "Chargeback opened — customer disputes September charge",
    },
    {
        "invoice_id": "INV-10345",
        "plan": "Enterprise Monthly",
        "amount_due": "$599.00",
        "currency": "USD",
        "invoice_status": "overdue",
        "due_date": "2026-09-01",
        "last_payment_date": "2026-08-01",
        "last_payment_amount": "$599.00",
        "outstanding_balance": "$1,198.00",
        "payment_method": "ACH Bank Transfer",
        "auto_renewal": True,
        "account_suspended": True,
        "notes": "Two months outstanding — account access restricted",
    },
    {
        "invoice_id": "INV-10389",
        "plan": "Pro Monthly",
        "amount_due": "$0.00",
        "currency": "USD",
        "invoice_status": "credit",
        "due_date": "2026-10-15",
        "last_payment_date": "2026-10-01",
        "last_payment_amount": "$49.99",
        "outstanding_balance": "-$49.99",
        "payment_method": "Visa **** 7700",
        "auto_renewal": True,
        "account_suspended": False,
        "notes": "Credit balance of $49.99 — applied to next invoice",
    },
    {
        "invoice_id": "INV-10412",
        "plan": "Starter Annual",
        "amount_due": "$0.00",
        "currency": "USD",
        "invoice_status": "paid",
        "due_date": "2026-03-01",
        "last_payment_date": "2026-03-01",
        "last_payment_amount": "$95.88",
        "outstanding_balance": "$0.00",
        "payment_method": "Debit Card **** 2020",
        "auto_renewal": True,
        "account_suspended": False,
        "notes": "Annual plan active — renews 2027-03-01",
    },
    {
        "invoice_id": "INV-10455",
        "plan": "Pro Monthly",
        "amount_due": "$49.99",
        "currency": "USD",
        "invoice_status": "pending",
        "due_date": "2026-10-12",
        "last_payment_date": "2026-09-12",
        "last_payment_amount": "$49.99",
        "outstanding_balance": "$49.99",
        "payment_method": "Visa **** 8833",
        "auto_renewal": True,
        "account_suspended": False,
        "notes": "Awaiting automatic charge on due date",
    },
    {
        "invoice_id": "INV-10499",
        "plan": "Team Annual",
        "amount_due": "$0.00",
        "currency": "USD",
        "invoice_status": "paid",
        "due_date": "2026-06-01",
        "last_payment_date": "2026-06-01",
        "last_payment_amount": "$1,428.00",
        "outstanding_balance": "$0.00",
        "payment_method": "Corporate Card **** 4455",
        "auto_renewal": True,
        "account_suspended": False,
        "notes": "Paid — includes 20% annual discount",
    },
    {
        "invoice_id": "INV-10522",
        "plan": "Enterprise Monthly",
        "amount_due": "$599.00",
        "currency": "USD",
        "invoice_status": "failed",
        "due_date": "2026-10-01",
        "last_payment_date": "2026-09-01",
        "last_payment_amount": "$599.00",
        "outstanding_balance": "$599.00",
        "payment_method": "ACH Bank Transfer",
        "auto_renewal": True,
        "account_suspended": False,
        "notes": "Bank returned NSF — insufficient funds",
    },
    {
        "invoice_id": "INV-10566",
        "plan": "Pro Monthly",
        "amount_due": "$24.99",
        "currency": "USD",
        "invoice_status": "partial",
        "due_date": "2026-10-08",
        "last_payment_date": "2026-09-08",
        "last_payment_amount": "$25.00",
        "outstanding_balance": "$24.99",
        "payment_method": "Mastercard **** 1199",
        "auto_renewal": False,
        "account_suspended": False,
        "notes": "Partial refund applied — prorated downgrade mid-cycle",
    },
    {
        "invoice_id": "INV-10601",
        "plan": "Team Monthly",
        "amount_due": "$0.00",
        "currency": "USD",
        "invoice_status": "paid",
        "due_date": "2026-10-03",
        "last_payment_date": "2026-10-03",
        "last_payment_amount": "$149.00",
        "outstanding_balance": "$0.00",
        "payment_method": "Visa **** 5544",
        "auto_renewal": True,
        "account_suspended": False,
        "notes": "Current — no issues",
    },
    {
        "invoice_id": "INV-10644",
        "plan": "Starter Monthly",
        "amount_due": "$9.99",
        "currency": "USD",
        "invoice_status": "overdue",
        "due_date": "2026-08-05",
        "last_payment_date": "2026-07-05",
        "last_payment_amount": "$9.99",
        "outstanding_balance": "$19.98",
        "payment_method": "PayPal",
        "auto_renewal": True,
        "account_suspended": False,
        "notes": "Two months overdue — reminder sent",
    },
    {
        "invoice_id": "INV-10688",
        "plan": "Enterprise Annual",
        "amount_due": "$0.00",
        "currency": "USD",
        "invoice_status": "paid",
        "due_date": "2026-07-15",
        "last_payment_date": "2026-07-15",
        "last_payment_amount": "$5,988.00",
        "outstanding_balance": "$0.00",
        "payment_method": "Wire Transfer",
        "auto_renewal": True,
        "account_suspended": False,
        "notes": "Enterprise contract — dedicated account manager assigned",
    },
    {
        "invoice_id": "INV-10722",
        "plan": "Pro Monthly",
        "amount_due": "$49.99",
        "currency": "USD",
        "invoice_status": "cancelled",
        "due_date": "2026-09-25",
        "last_payment_date": "2026-08-25",
        "last_payment_amount": "$49.99",
        "outstanding_balance": "$0.00",
        "payment_method": "Visa **** 3377",
        "auto_renewal": False,
        "account_suspended": False,
        "notes": "Subscription cancelled by customer on 2026-09-20",
    },
    {
        "invoice_id": "INV-10755",
        "plan": "Team Monthly",
        "amount_due": "$149.00",
        "currency": "USD",
        "invoice_status": "overdue",
        "due_date": "2026-09-10",
        "last_payment_date": "2026-08-10",
        "last_payment_amount": "$149.00",
        "outstanding_balance": "$149.00",
        "payment_method": "Mastercard **** 6622",
        "auto_renewal": True,
        "account_suspended": False,
        "notes": "Payment link re-sent — awaiting customer action",
    },
]


def _billing_index(customer_id: str) -> int:
    """Deterministically map a customer_id to a billing scenario index."""
    digest = hashlib.md5(customer_id.encode()).hexdigest()
    return int(digest[:4], 16) % len(_BILLING_SCENARIOS)


async def fetch_billing_info(customer_id: str) -> dict[str, Any]:
    """
    Mock Billing API call.

    Returns deterministic invoice data based on customer_id so the same
    customer always returns the same billing record (safe to retry).

    Args:
        customer_id: The customer identifier from the support ticket.

    Returns:
        Dict containing invoice status, amounts, and account flags.
    """
    if not customer_id or customer_id.strip() == "":
        return {
            "error": "customer_id is required",
            "customer_id": customer_id,
            "lookup_status": "not_found",
        }

    idx = _billing_index(customer_id)
    scenario = _BILLING_SCENARIOS[idx].copy()
    scenario["customer_id"] = customer_id
    scenario["lookup_status"] = "found"
    scenario["retrieved_at"] = datetime.utcnow().isoformat() + "Z"
    return scenario
