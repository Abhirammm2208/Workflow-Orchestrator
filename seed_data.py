"""
seed_data.py
Populate the PostgreSQL database with 50 realistic, diverse workflow run records.

Each record has:
  - A unique, varied support ticket (no two tickets are the same issue)
  - Appropriate triage output (category, urgency, sentiment)
  - Specialist data (billing invoice OR tech docs — matching the category)
  - A unique LLM-style draft reply tailored to the specific ticket
  - A realistic step_log with timestamps and evidence
  - Varied final statuses: completed, paused, failed, cancelled, rejected

Run with:
    python seed_data.py

Requires the app to be configured (.env) and the DB to be initialised:
    python -m app.db.init_db
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from app.db.database import create_all_tables, db_session
from app.db.models import Run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def ts(base: datetime, offset_seconds: int = 0) -> str:
    return (base + timedelta(seconds=offset_seconds)).isoformat()


def make_step_log(base: datetime, steps: list[dict]) -> list[dict]:
    """Build a realistic step_log list from a compact steps definition."""
    log = []
    cursor = base
    for s in steps:
        duration = s.get("duration_s", 2)
        started = cursor
        ended = cursor + timedelta(seconds=duration)
        log.append({
            "step": s["step"],
            "status": s["status"],
            "started_at": started.isoformat(),
            "ended_at": ended.isoformat(),
            "duration_ms": duration * 1000,
            "evidence": s.get("evidence", {}),
        })
        cursor = ended
    return log


# ---------------------------------------------------------------------------
# 50 diverse seed records
# Each record is a dict that maps directly to the Run ORM model +
# an `artifacts` field (stored as artifacts_snapshot JSONB)
# ---------------------------------------------------------------------------
def build_records() -> list[dict[str, Any]]:

    # Base time: spread records over past 30 days
    now = datetime.now(timezone.utc)
    records = []

    # =========================================================================
    # BILLING — COMPLETED (approved + email sent)
    # =========================================================================

    # 1
    base = now - timedelta(days=29, hours=3)
    records.append({
        "run_id": str(uuid.uuid4()),
        "status": "completed",
        "category": "Billing",
        "urgency": "High",
        "ticket_text": (
            "I was charged $99.98 twice for my Pro subscription this month. "
            "My bank statement shows two identical transactions on Oct 1st. "
            "Please refund the duplicate charge immediately. My customer ID is CUST-1001."
        ),
        "customer_email": "priya.sharma@techcorp.io",
        "customer_id": "CUST-1001",
        "inject_failure": False,
        "retry_count": 0,
        "reviewer_note": "Confirmed duplicate — billing team notified. Safe to send.",
        "reviewed_by": "agent_maya",
        "artifacts_snapshot": {
            "category": "Billing", "urgency": "High", "customer_sentiment": "angry",
            "triage_reasoning": "Double charge reported with specific transaction dates and amounts — high urgency billing dispute.",
            "billing_info": {"invoice_id": "INV-10156", "plan": "Pro Monthly", "invoice_status": "overdue", "amount_due": "$99.98", "outstanding_balance": "$99.98", "last_payment_date": "2026-07-20", "account_suspended": True, "notes": "Double billing reported — two invoices generated for August"},
            "draft_reply": "Dear Priya,\n\nWe sincerely apologise for the double charge on your account. We have verified your billing records and can confirm that two transactions of $49.99 were processed on October 1st — this was an error on our end. We have immediately initiated a full refund of $49.99 to your original payment method. Please allow 3–5 business days for the refund to appear on your bank statement.\n\nYour account (CUST-1001) has been flagged for priority review, and our billing team will ensure this does not recur. We are also removing any late payment flags from your account record.\n\nWe deeply regret the inconvenience caused and appreciate your patience. Please reply to this email if the refund does not appear within 5 business days.\n\nWarm regards,\nCustomer Support Team",
            "draft_word_count": 128,
            "approval_status": "approved",
            "reviewer_note": "Confirmed duplicate — billing team notified. Safe to send.",
            "email_sent": True,
            "email_sent_at": ts(base, 320),
            "email_subject": "[URGENT — Support Reply] Your Billing Request Has Been Reviewed",
        },
        "last_error": None,
        "created_at": base,
        "updated_at": base + timedelta(seconds=325),
        "completed_at": base + timedelta(seconds=325),
        "step_log": make_step_log(base, [
            {"step": "triage", "status": "completed", "duration_s": 3, "evidence": {"category": "Billing", "urgency": "High", "customer_sentiment": "angry"}},
            {"step": "billing_lookup", "status": "completed", "duration_s": 1, "evidence": {"invoice_id": "INV-10156", "invoice_status": "overdue", "amount_due": "$99.98"}},
            {"step": "draft_reply", "status": "completed", "duration_s": 4, "evidence": {"word_count": 128, "preview": "Dear Priya, We sincerely apologise for the double charge..."}},
            {"step": "human_approval", "status": "approved", "duration_s": 300, "evidence": {"approval_status": "approved", "reviewed_by": "agent_maya"}},
            {"step": "send_email", "status": "completed", "duration_s": 1, "evidence": {"recipient": "priya.sharma@techcorp.io", "idempotency_check": "passed — email not previously sent"}},
        ]),
    })

    # 2
    base = now - timedelta(days=28, hours=7)
    records.append({
        "run_id": str(uuid.uuid4()),
        "status": "completed",
        "category": "Billing",
        "urgency": "High",
        "ticket_text": (
            "My account has been suspended but I never missed a payment! "
            "I cannot access any of my data and my entire team is blocked. "
            "This is a production emergency. Account: CUST-2002."
        ),
        "customer_email": "cto@startupventures.com",
        "customer_id": "CUST-2002",
        "inject_failure": False,
        "retry_count": 0,
        "reviewer_note": "Account suspension confirmed as an error — billing cleared. Approved.",
        "reviewed_by": "agent_carlos",
        "artifacts_snapshot": {
            "category": "Billing", "urgency": "High", "customer_sentiment": "angry",
            "triage_reasoning": "Account suspension causing production outage for entire team — maximum urgency billing issue.",
            "billing_info": {"invoice_id": "INV-10345", "plan": "Enterprise Monthly", "invoice_status": "overdue", "amount_due": "$599.00", "outstanding_balance": "$1,198.00", "account_suspended": True, "notes": "Two months outstanding — access restricted"},
            "draft_reply": "Dear Startup Ventures Team,\n\nWe sincerely apologise for the service disruption. We have reviewed your account and identified the cause of the suspension. Our records show invoice INV-10345 for $599.00 has an outstanding balance — however, we are currently investigating whether this reflects a payment processing error on our end.\n\nEffective immediately, we have restored your account access so your team can resume work. Our billing team will complete a full audit within the next 2 hours and you will receive a detailed follow-up by end of day. If a payment processing error is confirmed, all late fees will be waived.\n\nWe understand the impact this has had on your operations and have escalated this to our senior billing specialist. You will have a dedicated point of contact for resolution.\n\nSincerely,\nCustomer Support — Priority Team",
            "draft_word_count": 147,
            "approval_status": "approved",
            "email_sent": True,
            "email_sent_at": ts(base, 410),
            "email_subject": "[URGENT — Support Reply] Your Billing Request Has Been Reviewed",
        },
        "last_error": None,
        "created_at": base,
        "updated_at": base + timedelta(seconds=415),
        "completed_at": base + timedelta(seconds=415),
        "step_log": make_step_log(base, [
            {"step": "triage", "status": "completed", "duration_s": 2, "evidence": {"category": "Billing", "urgency": "High", "customer_sentiment": "angry"}},
            {"step": "billing_lookup", "status": "completed", "duration_s": 1, "evidence": {"invoice_id": "INV-10345", "invoice_status": "overdue", "account_suspended": True}},
            {"step": "draft_reply", "status": "completed", "duration_s": 5, "evidence": {"word_count": 147}},
            {"step": "human_approval", "status": "approved", "duration_s": 400, "evidence": {"reviewed_by": "agent_carlos"}},
            {"step": "send_email", "status": "completed", "duration_s": 1, "evidence": {"recipient": "cto@startupventures.com"}},
        ]),
    })

    # 3
    base = now - timedelta(days=26, hours=2)
    records.append({
        "run_id": str(uuid.uuid4()),
        "status": "completed",
        "category": "Billing",
        "urgency": "Low",
        "ticket_text": (
            "Hi, I recently upgraded from Starter to Pro. Could you confirm "
            "what the prorated amount will be for the rest of this month? "
            "I want to make sure the math is correct before my next statement. Customer: CUST-3003."
        ),
        "customer_email": "alex.nguyen@freelancer.dev",
        "customer_id": "CUST-3003",
        "inject_failure": False,
        "retry_count": 0,
        "reviewer_note": "Standard proration query — reply is accurate. Send.",
        "reviewed_by": "agent_ravi",
        "artifacts_snapshot": {
            "category": "Billing", "urgency": "Low", "customer_sentiment": "neutral",
            "triage_reasoning": "Customer asking for proration confirmation on a plan upgrade — non-urgent billing inquiry.",
            "billing_info": {"invoice_id": "INV-10566", "plan": "Pro Monthly", "invoice_status": "partial", "amount_due": "$24.99", "outstanding_balance": "$24.99", "notes": "Partial refund applied — prorated downgrade mid-cycle"},
            "draft_reply": "Hi Alex,\n\nThank you for upgrading to our Pro plan! We are glad to help clarify the proration.\n\nBased on your upgrade date and your current billing cycle, the prorated charge for the remainder of this month is $24.99, which accounts for the difference between your Starter and Pro plan rates, calculated from your upgrade date to your next billing date. You can see the detailed breakdown in your invoice INV-10566 under Settings > Billing > Invoices.\n\nYour full Pro plan rate of $49.99/month will apply from your next billing cycle onwards. You now have access to all Pro features including priority support, advanced analytics, and up to 1,000 API requests per minute.\n\nIf the numbers look different on your statement, please do not hesitate to reach out and we will reconcile immediately.\n\nBest regards,\nBilling Support",
            "draft_word_count": 143,
            "approval_status": "approved",
            "email_sent": True,
            "email_sent_at": ts(base, 290),
        },
        "last_error": None,
        "created_at": base,
        "updated_at": base + timedelta(seconds=295),
        "completed_at": base + timedelta(seconds=295),
        "step_log": make_step_log(base, [
            {"step": "triage", "status": "completed", "duration_s": 2, "evidence": {"category": "Billing", "urgency": "Low"}},
            {"step": "billing_lookup", "status": "completed", "duration_s": 1, "evidence": {"invoice_id": "INV-10566", "invoice_status": "partial"}},
            {"step": "draft_reply", "status": "completed", "duration_s": 4, "evidence": {"word_count": 143}},
            {"step": "human_approval", "status": "approved", "duration_s": 280, "evidence": {"reviewed_by": "agent_ravi"}},
            {"step": "send_email", "status": "completed", "duration_s": 1, "evidence": {"recipient": "alex.nguyen@freelancer.dev"}},
        ]),
    })

    # 4
    base = now - timedelta(days=24, hours=9)
    records.append({
        "run_id": str(uuid.uuid4()),
        "status": "completed",
        "category": "Billing",
        "urgency": "High",
        "ticket_text": (
            "I cancelled my subscription on September 20th but you still charged me on October 1st. "
            "I want a full refund for this unauthorised charge. "
            "Transaction ID: TXN-9988. Customer ID: CUST-4004."
        ),
        "customer_email": "fatima.ali@designstudio.ae",
        "customer_id": "CUST-4004",
        "inject_failure": False,
        "retry_count": 0,
        "reviewer_note": "Charge after cancellation confirmed. Full refund warranted. Send.",
        "reviewed_by": "agent_maya",
        "artifacts_snapshot": {
            "category": "Billing", "urgency": "High", "customer_sentiment": "frustrated",
            "triage_reasoning": "Charge after explicit cancellation — this is a compliance issue requiring immediate attention.",
            "billing_info": {"invoice_id": "INV-10722", "plan": "Pro Monthly", "invoice_status": "cancelled", "amount_due": "$49.99", "outstanding_balance": "$0.00", "notes": "Subscription cancelled by customer on 2026-09-20"},
            "draft_reply": "Dear Fatima,\n\nThank you for bringing this to our attention, and we sincerely apologise for the charge that occurred after your cancellation.\n\nWe have reviewed your account and confirmed that your subscription was cancelled on September 20th, 2026, as requested. The charge of $49.99 on October 1st was processed in error and should not have occurred. We have initiated a full refund of $49.99 to your original payment method. You should see this credited within 3–5 business days, depending on your bank.\n\nWe are investigating why this charge was not blocked at cancellation and will implement a fix to prevent recurrence. Your cancellation is fully confirmed and you will not be charged again.\n\nAgain, we apologise for this error and the frustration it caused. If your refund does not appear within 5 business days, please reply directly to this email.\n\nSincerely,\nBilling Resolution Team",
            "draft_word_count": 163,
            "approval_status": "approved",
            "email_sent": True,
            "email_sent_at": ts(base, 360),
        },
        "last_error": None,
        "created_at": base,
        "updated_at": base + timedelta(seconds=365),
        "completed_at": base + timedelta(seconds=365),
        "step_log": make_step_log(base, [
            {"step": "triage", "status": "completed", "duration_s": 3, "evidence": {"category": "Billing", "urgency": "High"}},
            {"step": "billing_lookup", "status": "completed", "duration_s": 1, "evidence": {"invoice_id": "INV-10722", "invoice_status": "cancelled"}},
            {"step": "draft_reply", "status": "completed", "duration_s": 4, "evidence": {"word_count": 163}},
            {"step": "human_approval", "status": "approved", "duration_s": 350, "evidence": {"reviewed_by": "agent_maya"}},
            {"step": "send_email", "status": "completed", "duration_s": 1, "evidence": {"recipient": "fatima.ali@designstudio.ae"}},
        ]),
    })

    # 5
    base = now - timedelta(days=22, hours=4)
    records.append({
        "run_id": str(uuid.uuid4()),
        "status": "completed",
        "category": "Billing",
        "urgency": "Low",
        "ticket_text": (
            "Can I get an official invoice for my Enterprise annual subscription for FY2026? "
            "I need it with our company VAT number included: VAT-EU-442819. "
            "Account: CUST-5005."
        ),
        "customer_email": "finance@globallogistics.eu",
        "customer_id": "CUST-5005",
        "inject_failure": False,
        "retry_count": 0,
        "reviewer_note": "Standard invoice request. Reply is correct. Approved.",
        "reviewed_by": "agent_sarah",
        "artifacts_snapshot": {
            "category": "Billing", "urgency": "Low", "customer_sentiment": "neutral",
            "triage_reasoning": "Administrative billing request for official invoice — routine, no financial dispute.",
            "billing_info": {"invoice_id": "INV-10688", "plan": "Enterprise Annual", "invoice_status": "paid", "amount_due": "$0.00", "last_payment_amount": "$5,988.00", "notes": "Enterprise contract — dedicated account manager assigned"},
            "draft_reply": "Dear Finance Team at Global Logistics,\n\nThank you for your request. We are happy to provide an official invoice for your Enterprise Annual subscription.\n\nYour invoice INV-10688 for $5,988.00 (FY2026) has been regenerated with your VAT number VAT-EU-442819 included. You can download the updated PDF invoice directly from your billing portal at Settings > Billing > Invoices > INV-10688.\n\nIf you require the invoice in a specific format for your accounting system (e.g., XML, UBL), please let us know and our Enterprise billing team will prepare it accordingly. Your dedicated account manager has also been notified of this request.\n\nPlease allow up to 24 hours for the updated invoice to appear in your portal.\n\nBest regards,\nEnterprise Billing Team",
            "draft_word_count": 139,
            "approval_status": "approved",
            "email_sent": True,
            "email_sent_at": ts(base, 270),
        },
        "last_error": None,
        "created_at": base,
        "updated_at": base + timedelta(seconds=275),
        "completed_at": base + timedelta(seconds=275),
        "step_log": make_step_log(base, [
            {"step": "triage", "status": "completed", "duration_s": 2, "evidence": {"category": "Billing", "urgency": "Low"}},
            {"step": "billing_lookup", "status": "completed", "duration_s": 1, "evidence": {"invoice_id": "INV-10688", "invoice_status": "paid"}},
            {"step": "draft_reply", "status": "completed", "duration_s": 4, "evidence": {"word_count": 139}},
            {"step": "human_approval", "status": "approved", "duration_s": 260, "evidence": {"reviewed_by": "agent_sarah"}},
            {"step": "send_email", "status": "completed", "duration_s": 1, "evidence": {"recipient": "finance@globallogistics.eu"}},
        ]),
    })

    # 6
    base = now - timedelta(days=20, hours=11)
    records.append({
        "run_id": str(uuid.uuid4()),
        "status": "completed",
        "category": "Billing",
        "urgency": "High",
        "ticket_text": (
            "A chargeback has been filed against my account but I did authorise this payment! "
            "My bank made an error. I need this resolved before my account gets suspended. "
            "Customer: CUST-6006."
        ),
        "customer_email": "marco.rossi@italiandesign.it",
        "customer_id": "CUST-6006",
        "inject_failure": False,
        "retry_count": 0,
        "reviewer_note": "Customer disputing their own chargeback — finance team needs to verify. Reply approved.",
        "reviewed_by": "agent_carlos",
        "artifacts_snapshot": {
            "category": "Billing", "urgency": "High", "customer_sentiment": "anxious",
            "triage_reasoning": "Chargeback dispute that could lead to account suspension — requires immediate financial team involvement.",
            "billing_info": {"invoice_id": "INV-10311", "plan": "Team Monthly", "invoice_status": "disputed", "amount_due": "$149.00", "notes": "Chargeback opened — customer disputes September charge"},
            "draft_reply": "Dear Marco,\n\nThank you for reaching out and for clarifying your position on this chargeback. We completely understand your concern — having a chargeback on your account while maintaining that the charge was authorised is a frustrating situation, and we want to resolve it quickly.\n\nWe have flagged your case as urgent and our finance team is already reviewing the chargeback documentation for invoice INV-10311. To resolve this in your favour, we will need you to contact your bank and submit a chargeback reversal or dispute resolution request, stating that the transaction was authorised. We can provide supporting documentation (purchase confirmation, service logs) to assist your claim.\n\nYour account will remain active during this investigation — we will not suspend it while the review is in progress. You will hear from our finance team within 24 hours with a full update.\n\nSincerely,\nFinancial Disputes Team",
            "draft_word_count": 165,
            "approval_status": "approved",
            "email_sent": True,
            "email_sent_at": ts(base, 380),
        },
        "last_error": None,
        "created_at": base,
        "updated_at": base + timedelta(seconds=385),
        "completed_at": base + timedelta(seconds=385),
        "step_log": make_step_log(base, [
            {"step": "triage", "status": "completed", "duration_s": 3, "evidence": {"category": "Billing", "urgency": "High"}},
            {"step": "billing_lookup", "status": "completed", "duration_s": 1, "evidence": {"invoice_id": "INV-10311", "invoice_status": "disputed"}},
            {"step": "draft_reply", "status": "completed", "duration_s": 5, "evidence": {"word_count": 165}},
            {"step": "human_approval", "status": "approved", "duration_s": 370, "evidence": {"reviewed_by": "agent_carlos"}},
            {"step": "send_email", "status": "completed", "duration_s": 1, "evidence": {"recipient": "marco.rossi@italiandesign.it"}},
        ]),
    })

    # 7
    base = now - timedelta(days=18, hours=6)
    records.append({
        "run_id": str(uuid.uuid4()),
        "status": "completed",
        "category": "Billing",
        "urgency": "Low",
        "ticket_text": (
            "I have a credit of $49.99 on my account but it's not being applied to my upcoming invoice. "
            "When will it be deducted? CUST-7007."
        ),
        "customer_email": "sophie.duval@agence-creative.fr",
        "customer_id": "CUST-7007",
        "inject_failure": False,
        "retry_count": 0,
        "reviewer_note": "Credit balance confirmed. Reply accurate.",
        "reviewed_by": "agent_ravi",
        "artifacts_snapshot": {
            "category": "Billing", "urgency": "Low", "customer_sentiment": "confused",
            "triage_reasoning": "Account credit not applying to upcoming invoice — billing inquiry, no urgent impact.",
            "billing_info": {"invoice_id": "INV-10389", "plan": "Pro Monthly", "invoice_status": "credit", "outstanding_balance": "-$49.99", "notes": "Credit balance of $49.99 — applied to next invoice"},
            "draft_reply": "Hi Sophie,\n\nGreat news — your credit of $49.99 is already on your account and is scheduled to be automatically applied to your next invoice.\n\nBased on your billing cycle, your next invoice will be generated on your upcoming renewal date. The $49.99 credit will appear as a line item deduction, and depending on your plan cost, this may fully or partially cover the invoice. You will receive an invoice preview by email 3 days before it is processed.\n\nYou can verify the credit balance at any time under Settings > Billing > Credits and Balance. If the credit does not appear on your next invoice, please contact us and we will apply it manually.\n\nThank you for your continued subscription!\n\nBest,\nBilling Team",
            "draft_word_count": 131,
            "approval_status": "approved",
            "email_sent": True,
            "email_sent_at": ts(base, 260),
        },
        "last_error": None,
        "created_at": base,
        "updated_at": base + timedelta(seconds=265),
        "completed_at": base + timedelta(seconds=265),
        "step_log": make_step_log(base, [
            {"step": "triage", "status": "completed", "duration_s": 2, "evidence": {"category": "Billing", "urgency": "Low"}},
            {"step": "billing_lookup", "status": "completed", "duration_s": 1, "evidence": {"invoice_id": "INV-10389", "invoice_status": "credit"}},
            {"step": "draft_reply", "status": "completed", "duration_s": 3, "evidence": {"word_count": 131}},
            {"step": "human_approval", "status": "approved", "duration_s": 252, "evidence": {"reviewed_by": "agent_ravi"}},
            {"step": "send_email", "status": "completed", "duration_s": 1, "evidence": {"recipient": "sophie.duval@agence-creative.fr"}},
        ]),
    })

    # =========================================================================
    # TECH — COMPLETED
    # =========================================================================

    # 8
    base = now - timedelta(days=27, hours=5)
    records.append({
        "run_id": str(uuid.uuid4()),
        "status": "completed",
        "category": "Tech",
        "urgency": "High",
        "ticket_text": (
            "Our API integration is returning 429 errors every few minutes even though "
            "we are well within our rate limits according to our dashboard. "
            "This is breaking our production pipeline. Team: CUST-8008."
        ),
        "customer_email": "devops@pipelinetech.co",
        "customer_id": "CUST-8008",
        "inject_failure": False,
        "retry_count": 0,
        "reviewer_note": "Good technical response. Approved.",
        "reviewed_by": "agent_sarah",
        "artifacts_snapshot": {
            "category": "Tech", "urgency": "High", "customer_sentiment": "frustrated",
            "triage_reasoning": "Production API outage caused by unexpected rate limiting — requires immediate technical investigation.",
            "relevant_docs": [
                {"doc_id": "KB-003", "title": "API Rate Limits and Quotas", "snippet": "Free tier: 100 requests/minute... When you exceed the limit, you receive a 429 status with a Retry-After header...", "url": "https://docs.example.com/api/rate-limits", "relevance_score": 0.7},
                {"doc_id": "KB-006", "title": "Understanding Error Codes", "snippet": "429 Too Many Requests — rate limit exceeded...", "url": "https://docs.example.com/api/error-codes", "relevance_score": 0.5},
                {"doc_id": "KB-016", "title": "Slow Performance and Timeout Errors", "snippet": "If requests time out: check your payload size...", "url": "https://docs.example.com/troubleshooting/performance", "relevance_score": 0.3},
            ],
            "draft_reply": "Hi Pipeline Tech Team,\n\nThank you for flagging this — a 429 rate limit error in production when you believe you are within your quota is a serious issue and we are treating it as high priority.\n\nThere are two common causes for this: first, rate limit counters operate on a rolling 60-second window, not a fixed minute boundary, so bursts within a short window can trigger the limit even if your hourly average looks fine. Second, if your account has multiple API keys or services making requests, the limits are counted in aggregate across all keys.\n\nImmediate action: check the Retry-After header in the 429 response — it tells you exactly how many seconds to wait. Implement exponential backoff starting at 1 second. This should stop your pipeline from compounding the issue.\n\nWe are also reviewing your account's request logs on our end to identify if there is an anomaly. You will have a status update within 30 minutes.\n\nApologies for the disruption.\n\nTechnical Support Team",
            "draft_word_count": 172,
            "approval_status": "approved",
            "email_sent": True,
            "email_sent_at": ts(base, 340),
        },
        "last_error": None,
        "created_at": base,
        "updated_at": base + timedelta(seconds=345),
        "completed_at": base + timedelta(seconds=345),
        "step_log": make_step_log(base, [
            {"step": "triage", "status": "completed", "duration_s": 2, "evidence": {"category": "Tech", "urgency": "High"}},
            {"step": "doc_search", "status": "completed", "duration_s": 1, "evidence": {"docs_found": 3, "top_doc_title": "API Rate Limits and Quotas", "top_doc_score": 0.7}},
            {"step": "draft_reply", "status": "completed", "duration_s": 5, "evidence": {"word_count": 172}},
            {"step": "human_approval", "status": "approved", "duration_s": 330, "evidence": {"reviewed_by": "agent_sarah"}},
            {"step": "send_email", "status": "completed", "duration_s": 1, "evidence": {"recipient": "devops@pipelinetech.co"}},
        ]),
    })

    # 9
    base = now - timedelta(days=25, hours=8)
    records.append({
        "run_id": str(uuid.uuid4()),
        "status": "completed",
        "category": "Tech",
        "urgency": "Low",
        "ticket_text": (
            "I lost access to my authenticator app after getting a new phone. "
            "I still have my recovery codes from initial setup. "
            "How do I use them to regain access? Username: CUST-9009."
        ),
        "customer_email": "james.okonkwo@consultingfirm.ng",
        "customer_id": "CUST-9009",
        "inject_failure": False,
        "retry_count": 0,
        "reviewer_note": "Standard 2FA recovery — instructions correct. Send.",
        "reviewed_by": "agent_maya",
        "artifacts_snapshot": {
            "category": "Tech", "urgency": "Low", "customer_sentiment": "anxious",
            "triage_reasoning": "2FA recovery needed after device change — common account access issue, low urgency.",
            "relevant_docs": [
                {"doc_id": "KB-002", "title": "Setting Up Two-Factor Authentication (2FA)", "snippet": "If you've lost access to your authenticator app, use a backup recovery code from your initial setup...", "url": "https://docs.example.com/security/2fa-setup", "relevance_score": 0.8},
                {"doc_id": "KB-001", "title": "How to Reset Your Password", "snippet": "To reset your password: navigate to the login page and click Forgot Password...", "url": "https://docs.example.com/account/reset-password", "relevance_score": 0.2},
            ],
            "draft_reply": "Hi James,\n\nGood news — since you have your recovery codes from your initial 2FA setup, regaining access is straightforward.\n\nOn the login page, enter your username and password as usual. When prompted for your 2FA code, look for the option that says 'Use a recovery code' or 'Lost access to your authenticator?'. Enter one of your recovery codes exactly as shown (including any hyphens). Recovery codes are single-use, so this code will be marked as used after login.\n\nOnce you are logged in, we recommend setting up 2FA again on your new phone right away — go to Settings > Security > Two-Factor Authentication and follow the setup flow. This will generate a new set of recovery codes for you, so store them securely.\n\nIf none of your recovery codes work, please reply with your account details and our security team will assist you with identity verification to restore access.\n\nBest regards,\nTechnical Support",
            "draft_word_count": 169,
            "approval_status": "approved",
            "email_sent": True,
            "email_sent_at": ts(base, 280),
        },
        "last_error": None,
        "created_at": base,
        "updated_at": base + timedelta(seconds=285),
        "completed_at": base + timedelta(seconds=285),
        "step_log": make_step_log(base, [
            {"step": "triage", "status": "completed", "duration_s": 2, "evidence": {"category": "Tech", "urgency": "Low"}},
            {"step": "doc_search", "status": "completed", "duration_s": 1, "evidence": {"docs_found": 3, "top_doc_title": "Setting Up Two-Factor Authentication (2FA)", "top_doc_score": 0.8}},
            {"step": "draft_reply", "status": "completed", "duration_s": 4, "evidence": {"word_count": 169}},
            {"step": "human_approval", "status": "approved", "duration_s": 272, "evidence": {"reviewed_by": "agent_maya"}},
            {"step": "send_email", "status": "completed", "duration_s": 1, "evidence": {"recipient": "james.okonkwo@consultingfirm.ng"}},
        ]),
    })

    # 10
    base = now - timedelta(days=23, hours=3)
    records.append({
        "run_id": str(uuid.uuid4()),
        "status": "completed",
        "category": "Tech",
        "urgency": "High",
        "ticket_text": (
            "Webhook deliveries are failing silently. Our endpoint returns 200 OK "
            "but we are not receiving any payloads. Events are definitely being triggered. "
            "This has been happening for 6 hours. CUST-1010."
        ),
        "customer_email": "backend@saasproduct.io",
        "customer_id": "CUST-1010",
        "inject_failure": False,
        "retry_count": 0,
        "reviewer_note": "Solid troubleshooting steps. Approved.",
        "reviewed_by": "agent_carlos",
        "artifacts_snapshot": {
            "category": "Tech", "urgency": "High", "customer_sentiment": "frustrated",
            "triage_reasoning": "Production webhook failure lasting 6 hours — direct business impact, high urgency.",
            "relevant_docs": [
                {"doc_id": "KB-004", "title": "Webhook Configuration and Troubleshooting", "snippet": "Webhooks time out after 10 seconds; we retry up to 5 times with exponential backoff. Check the webhook delivery log...", "url": "https://docs.example.com/integrations/webhooks", "relevance_score": 0.9},
            ],
            "draft_reply": "Hi Backend Team,\n\nThank you for the detailed report — a webhook endpoint returning 200 but not receiving payloads is an unusual pattern and we want to resolve it urgently.\n\nThe most likely cause is a TLS certificate or IP filtering issue at your endpoint where our delivery server's IP was recently added to a blocklist. Even though your server returns 200, the payload body may be dropped by a middleware layer before reaching your application code.\n\nImmediate steps: 1) Check our webhook delivery log at Dashboard > Settings > Webhooks > Delivery Log — this will show the exact response body and headers we received. 2) Ensure your endpoint is not stripping the request body for POST requests. 3) Verify that our delivery IPs (listed at docs.example.com/integrations/webhooks#ip-ranges) are allowlisted.\n\nWe have escalated this to our infrastructure team to check for any delivery anomalies on our end from the past 6 hours. Expect an update within 1 hour.\n\nTechnical Support — Integrations",
            "draft_word_count": 181,
            "approval_status": "approved",
            "email_sent": True,
            "email_sent_at": ts(base, 420),
        },
        "last_error": None,
        "created_at": base,
        "updated_at": base + timedelta(seconds=425),
        "completed_at": base + timedelta(seconds=425),
        "step_log": make_step_log(base, [
            {"step": "triage", "status": "completed", "duration_s": 3, "evidence": {"category": "Tech", "urgency": "High"}},
            {"step": "doc_search", "status": "completed", "duration_s": 1, "evidence": {"docs_found": 3, "top_doc_title": "Webhook Configuration and Troubleshooting", "top_doc_score": 0.9}},
            {"step": "draft_reply", "status": "completed", "duration_s": 6, "evidence": {"word_count": 181}},
            {"step": "human_approval", "status": "approved", "duration_s": 408, "evidence": {"reviewed_by": "agent_carlos"}},
            {"step": "send_email", "status": "completed", "duration_s": 1, "evidence": {"recipient": "backend@saasproduct.io"}},
        ]),
    })

    # 11
    base = now - timedelta(days=21, hours=1)
    records.append({
        "run_id": str(uuid.uuid4()),
        "status": "completed",
        "category": "Tech",
        "urgency": "Low",
        "ticket_text": (
            "The dashboard is not loading at all on Chrome. "
            "I just see a white blank page with no error messages. "
            "It works fine in Firefox. Customer: CUST-1111."
        ),
        "customer_email": "nina.patel@marketingagency.in",
        "customer_id": "CUST-1111",
        "inject_failure": False,
        "retry_count": 0,
        "reviewer_note": "Good troubleshooting steps for Chrome-specific issue.",
        "reviewed_by": "agent_ravi",
        "artifacts_snapshot": {
            "category": "Tech", "urgency": "Low", "customer_sentiment": "confused",
            "triage_reasoning": "Browser-specific rendering issue — non-blocking since Firefox works, low urgency.",
            "relevant_docs": [
                {"doc_id": "KB-013", "title": "Dashboard Not Loading or Blank Screen", "snippet": "If the dashboard shows a blank or frozen screen: hard-refresh with Ctrl+Shift+R... Try an incognito window...", "url": "https://docs.example.com/troubleshooting/dashboard", "relevance_score": 0.75},
            ],
            "draft_reply": "Hi Nina,\n\nThank you for the details — a blank white page in Chrome while Firefox works perfectly points to a Chrome-specific cached data or extension conflict.\n\nHere is what to try in order: First, do a hard refresh in Chrome with Ctrl+Shift+R to clear the local cache. If that does not work, open Chrome in an incognito window (Ctrl+Shift+N) and try loading the dashboard — if it works there, a browser extension is blocking our scripts. In that case, go to chrome://extensions and temporarily disable extensions one by one to identify the culprit (ad blockers and privacy tools are the most common cause).\n\nIf the incognito window also shows a blank page, please open Chrome's developer console (F12 > Console tab) and send us a screenshot of any red error messages — that will help us pinpoint the exact issue.\n\nWe support Chrome 110 and above, so also check that your Chrome is up to date at chrome://settings/help.\n\nHappy to assist further!\n\nTechnical Support",
            "draft_word_count": 177,
            "approval_status": "approved",
            "email_sent": True,
            "email_sent_at": ts(base, 300),
        },
        "last_error": None,
        "created_at": base,
        "updated_at": base + timedelta(seconds=305),
        "completed_at": base + timedelta(seconds=305),
        "step_log": make_step_log(base, [
            {"step": "triage", "status": "completed", "duration_s": 2, "evidence": {"category": "Tech", "urgency": "Low"}},
            {"step": "doc_search", "status": "completed", "duration_s": 1, "evidence": {"docs_found": 3, "top_doc_title": "Dashboard Not Loading or Blank Screen", "top_doc_score": 0.75}},
            {"step": "draft_reply", "status": "completed", "duration_s": 4, "evidence": {"word_count": 177}},
            {"step": "human_approval", "status": "approved", "duration_s": 292, "evidence": {"reviewed_by": "agent_ravi"}},
            {"step": "send_email", "status": "completed", "duration_s": 1, "evidence": {"recipient": "nina.patel@marketingagency.in"}},
        ]),
    })

    # 12
    base = now - timedelta(days=19, hours=7)
    records.append({
        "run_id": str(uuid.uuid4()),
        "status": "completed",
        "category": "Tech",
        "urgency": "High",
        "ticket_text": (
            "We had a data breach — someone logged into our admin account from an unrecognised location. "
            "I've already reset the password but I need to know what data was accessed. "
            "Account: CUST-1212."
        ),
        "customer_email": "security@fintech-startup.sg",
        "customer_id": "CUST-1212",
        "inject_failure": False,
        "retry_count": 0,
        "reviewer_note": "Security incident — escalated to security team. Reply appropriate.",
        "reviewed_by": "agent_sarah",
        "artifacts_snapshot": {
            "category": "Tech", "urgency": "High", "customer_sentiment": "anxious",
            "triage_reasoning": "Potential account compromise with admin access — immediate security escalation required.",
            "relevant_docs": [
                {"doc_id": "KB-023", "title": "Audit Log and Activity History", "snippet": "Access the audit log at Settings > Security > Audit Log. Logs include: login attempts, settings changes, data exports, API key usage...", "url": "https://docs.example.com/security/audit-log", "relevance_score": 0.85},
                {"doc_id": "KB-002", "title": "Setting Up Two-Factor Authentication (2FA)", "snippet": "Enable 2FA under Settings > Security...", "url": "https://docs.example.com/security/2fa-setup", "relevance_score": 0.6},
            ],
            "draft_reply": "Dear Security Team,\n\nWe are treating this as a critical security incident and our security team has been immediately notified.\n\nFirst, the steps you have already taken (password reset) are correct. Please also do the following now: 1) Enable 2FA immediately at Settings > Security > Two-Factor Authentication if not already active. 2) Revoke all existing API keys and generate new ones. 3) Check your audit log at Settings > Security > Audit Log — this will show exactly which pages were visited, what data was viewed, and what actions were taken by the suspicious session, including timestamps and IP addresses.\n\nOur security team has flagged your account for immediate review and will provide you with a full session activity report within 2 hours. We are also temporarily monitoring your account for further suspicious activity.\n\nIf you believe data was exported, please specify the timeframe and we will conduct a thorough investigation and provide a formal incident report for compliance purposes.\n\nSecurity Response Team",
            "draft_word_count": 189,
            "approval_status": "approved",
            "email_sent": True,
            "email_sent_at": ts(base, 450),
        },
        "last_error": None,
        "created_at": base,
        "updated_at": base + timedelta(seconds=455),
        "completed_at": base + timedelta(seconds=455),
        "step_log": make_step_log(base, [
            {"step": "triage", "status": "completed", "duration_s": 3, "evidence": {"category": "Tech", "urgency": "High"}},
            {"step": "doc_search", "status": "completed", "duration_s": 1, "evidence": {"docs_found": 3, "top_doc_title": "Audit Log and Activity History", "top_doc_score": 0.85}},
            {"step": "draft_reply", "status": "completed", "duration_s": 6, "evidence": {"word_count": 189}},
            {"step": "human_approval", "status": "approved", "duration_s": 438, "evidence": {"reviewed_by": "agent_sarah"}},
            {"step": "send_email", "status": "completed", "duration_s": 1, "evidence": {"recipient": "security@fintech-startup.sg"}},
        ]),
    })

    # 13
    base = now - timedelta(days=17, hours=9)
    records.append({
        "run_id": str(uuid.uuid4()),
        "status": "completed",
        "category": "Tech",
        "urgency": "Low",
        "ticket_text": (
            "I'm trying to bulk import 45,000 records via CSV but it keeps failing at row 30,000 "
            "with a 'duplicate identifier' error. Our ID column has no duplicates — I've checked. "
            "CUST-1313."
        ),
        "customer_email": "data.ops@ecommerce-platform.com",
        "customer_id": "CUST-1313",
        "inject_failure": False,
        "retry_count": 0,
        "reviewer_note": "Detailed CSV import troubleshooting. Correct. Approved.",
        "reviewed_by": "agent_maya",
        "artifacts_snapshot": {
            "category": "Tech", "urgency": "Low", "customer_sentiment": "confused",
            "triage_reasoning": "CSV bulk import failure at specific row count — data issue, non-urgent, no production impact.",
            "relevant_docs": [
                {"doc_id": "KB-021", "title": "Bulk Import via CSV", "snippet": "Duplicate detection is based on the unique identifier column — duplicates are skipped by default...", "url": "https://docs.example.com/data/bulk-import", "relevance_score": 0.9},
            ],
            "draft_reply": "Hi Data Ops Team,\n\nThank you for the detailed report. A 'duplicate identifier' error at row 30,000 when you have verified your CSV has no duplicates is a known edge case we can help you resolve.\n\nThe most likely cause is that your import is conflicting with existing records already in the database, not duplicates within the CSV itself. Our duplicate detection compares the unique identifier column against existing records, not just within the file. Rows 1–29,999 may have imported successfully, creating records that the second batch (rows 30,000+) then conflicts with on a subsequent retry attempt.\n\nTo diagnose: download the import error report from Settings > Data > Import History — it will list the exact row numbers and the conflicting record IDs. Then check if those IDs already exist in your dataset.\n\nIf you want to overwrite existing records rather than skip duplicates, re-run the import with the 'Overwrite existing records' option enabled.\n\nFor imports over 50,000 rows, we recommend splitting into two files of 25,000 each.\n\nHappy to jump on a call if needed!\n\nData Support Team",
            "draft_word_count": 191,
            "approval_status": "approved",
            "email_sent": True,
            "email_sent_at": ts(base, 310),
        },
        "last_error": None,
        "created_at": base,
        "updated_at": base + timedelta(seconds=315),
        "completed_at": base + timedelta(seconds=315),
        "step_log": make_step_log(base, [
            {"step": "triage", "status": "completed", "duration_s": 2, "evidence": {"category": "Tech", "urgency": "Low"}},
            {"step": "doc_search", "status": "completed", "duration_s": 1, "evidence": {"docs_found": 3, "top_doc_title": "Bulk Import via CSV", "top_doc_score": 0.9}},
            {"step": "draft_reply", "status": "completed", "duration_s": 4, "evidence": {"word_count": 191}},
            {"step": "human_approval", "status": "approved", "duration_s": 302, "evidence": {"reviewed_by": "agent_maya"}},
            {"step": "send_email", "status": "completed", "duration_s": 1, "evidence": {"recipient": "data.ops@ecommerce-platform.com"}},
        ]),
    })

    # 14
    base = now - timedelta(days=15, hours=4)
    records.append({
        "run_id": str(uuid.uuid4()),
        "status": "completed",
        "category": "Tech",
        "urgency": "Low",
        "ticket_text": (
            "How do I set up SSO with Azure Active Directory? "
            "We are on the Enterprise plan and want to migrate our team to single sign-on. "
            "CUST-1414."
        ),
        "customer_email": "it.admin@corporateholdings.de",
        "customer_id": "CUST-1414",
        "inject_failure": False,
        "retry_count": 0,
        "reviewer_note": "SSO setup guidance is accurate. Approved.",
        "reviewed_by": "agent_carlos",
        "artifacts_snapshot": {
            "category": "Tech", "urgency": "Low", "customer_sentiment": "neutral",
            "triage_reasoning": "SSO configuration request on Enterprise plan — planned migration, no urgency.",
            "relevant_docs": [
                {"doc_id": "KB-008", "title": "SSO and SAML Configuration", "snippet": "SAML 2.0 SSO is available on Enterprise plans. Configure at Settings > Security > SSO. Supported providers: Okta, Azure AD...", "url": "https://docs.example.com/security/sso-saml", "relevance_score": 0.95},
            ],
            "draft_reply": "Hi IT Admin Team,\n\nSetting up SAML 2.0 SSO with Azure Active Directory is straightforward on your Enterprise plan. Here is a step-by-step overview:\n\n1. In Azure AD, create a new Enterprise Application and select 'Set up single sign-on' > SAML. 2. In our platform, go to Settings > Security > SSO and copy our Service Provider metadata — you will need the ACS URL (https://app.example.com/auth/saml/callback) and the Entity ID. 3. In Azure AD, paste these into the Basic SAML Configuration fields. 4. Download the Azure AD Federation Metadata XML and upload it in our SSO settings under 'IdP Metadata'. 5. Map the email attribute from Azure AD to the user email field in our platform.\n\nBefore enforcing SSO for your team, use the 'Test SSO Connection' button to verify the configuration — this prevents any accidental lockouts.\n\nOur full guide with screenshots is at docs.example.com/security/sso-saml. Your dedicated account manager can also schedule a 30-minute onboarding call if you'd prefer guided setup.\n\nBest regards,\nEnterprise Technical Support",
            "draft_word_count": 195,
            "approval_status": "approved",
            "email_sent": True,
            "email_sent_at": ts(base, 270),
        },
        "last_error": None,
        "created_at": base,
        "updated_at": base + timedelta(seconds=275),
        "completed_at": base + timedelta(seconds=275),
        "step_log": make_step_log(base, [
            {"step": "triage", "status": "completed", "duration_s": 2, "evidence": {"category": "Tech", "urgency": "Low"}},
            {"step": "doc_search", "status": "completed", "duration_s": 1, "evidence": {"docs_found": 3, "top_doc_title": "SSO and SAML Configuration", "top_doc_score": 0.95}},
            {"step": "draft_reply", "status": "completed", "duration_s": 4, "evidence": {"word_count": 195}},
            {"step": "human_approval", "status": "approved", "duration_s": 262, "evidence": {"reviewed_by": "agent_carlos"}},
            {"step": "send_email", "status": "completed", "duration_s": 1, "evidence": {"recipient": "it.admin@corporateholdings.de"}},
        ]),
    })

    # 15
    base = now - timedelta(days=13, hours=2)
    records.append({
        "run_id": str(uuid.uuid4()),
        "status": "completed",
        "category": "Tech",
        "urgency": "Low",
        "ticket_text": (
            "The mobile app keeps logging me out every time I switch apps, even with 'Remember me' enabled. "
            "I'm using iPhone 15 Pro, iOS 18.1. This is very frustrating for daily use. CUST-1515."
        ),
        "customer_email": "aisha.kowalski@journalist.pl",
        "customer_id": "CUST-1515",
        "inject_failure": False,
        "retry_count": 0,
        "reviewer_note": "Session timeout troubleshooting for mobile is accurate. Send.",
        "reviewed_by": "agent_ravi",
        "artifacts_snapshot": {
            "category": "Tech", "urgency": "Low", "customer_sentiment": "frustrated",
            "triage_reasoning": "Mobile session persistence issue — non-blocking, user inconvenience only.",
            "relevant_docs": [
                {"doc_id": "KB-009", "title": "Mobile App Login Issues", "snippet": "If the mobile app fails to load or login: force-close and reopen... Clear app cache: Settings > Apps > Example App > Clear Cache...", "url": "https://docs.example.com/mobile/troubleshooting", "relevance_score": 0.7},
                {"doc_id": "KB-028", "title": "Session Timeout and Auto-Logout", "snippet": "Sessions expire after 8 hours of inactivity by default... 'Remember me' extends sessions to 30 days on trusted devices...", "url": "https://docs.example.com/security/session-management", "relevance_score": 0.65},
            ],
            "draft_reply": "Hi Aisha,\n\nThank you for the detailed report — this is a known issue on iOS 18 related to how the operating system handles background app memory, and we have a fix in progress.\n\nIn the meantime, here is what resolves it for most users: Go to iPhone Settings > General > Background App Refresh and ensure our app is enabled. iOS 18 introduced more aggressive memory management that can terminate background sessions even with 'Remember me' active.\n\nAlso please ensure you are on app version 4.2.0 or higher — open the App Store and check for updates. Version 4.2.1, released last week, includes a session persistence fix specifically for iOS 18.1.\n\nIf the issue persists after updating, please try: Settings (in our app) > Account > Sign out > Sign back in — this refreshes the session token. If none of these resolve it, please send us your app version and iOS version and we will escalate to our mobile engineering team.\n\nSorry for the inconvenience — we appreciate your patience!\n\nMobile Support Team",
            "draft_word_count": 182,
            "approval_status": "approved",
            "email_sent": True,
            "email_sent_at": ts(base, 295),
        },
        "last_error": None,
        "created_at": base,
        "updated_at": base + timedelta(seconds=300),
        "completed_at": base + timedelta(seconds=300),
        "step_log": make_step_log(base, [
            {"step": "triage", "status": "completed", "duration_s": 2, "evidence": {"category": "Tech", "urgency": "Low"}},
            {"step": "doc_search", "status": "completed", "duration_s": 1, "evidence": {"docs_found": 3, "top_doc_title": "Mobile App Login Issues", "top_doc_score": 0.7}},
            {"step": "draft_reply", "status": "completed", "duration_s": 4, "evidence": {"word_count": 182}},
            {"step": "human_approval", "status": "approved", "duration_s": 287, "evidence": {"reviewed_by": "agent_ravi"}},
            {"step": "send_email", "status": "completed", "duration_s": 1, "evidence": {"recipient": "aisha.kowalski@journalist.pl"}},
        ]),
    })

    # =========================================================================
    # PAUSED — Awaiting human approval
    # =========================================================================
    for i, (ticket, email, cid, cat, urg, sent) in enumerate([
        ("My NSF charge has caused our bank account to go into overdraft. The system charged us again after a failed payment. We need immediate resolution. CUST-2016.", "treasurer@nonprofitorg.org", "CUST-2016", "Billing", "High", "NSF failed payment causing overdraft"),
        ("I need to set up custom automation rules that send webhook notifications when a record status changes. Can you help? CUST-2017.", "ops@logistics-firm.com", "CUST-2017", "Tech", "Low", "Custom automation webhook setup"),
        ("Our SSO integration broke after an Azure AD policy change. 200+ employees can't log in. CUST-2018.", "it@enterprise-bank.com", "CUST-2018", "Tech", "High", "SSO breakage causing mass login failure"),
        ("I see a pending invoice for $149 but we downgraded plans before the billing date. Shouldn't this be lower? CUST-2019.", "billing@design-agency.uk", "CUST-2019", "Billing", "Low", "Post-downgrade billing discrepancy"),
        ("The export to CSV fails for datasets over 50,000 rows. We export weekly and it's worked before. CUST-2020.", "analytics@retail-chain.com", "CUST-2020", "Tech", "Low", "CSV export failure for large datasets"),
    ], start=16):
        base = now - timedelta(days=i, hours=i % 8)
        is_billing = cat == "Billing"
        records.append({
            "run_id": str(uuid.uuid4()),
            "status": "paused",
            "category": cat,
            "urgency": urg,
            "ticket_text": ticket,
            "customer_email": email,
            "customer_id": cid,
            "inject_failure": False,
            "retry_count": 0,
            "reviewer_note": None,
            "reviewed_by": None,
            "artifacts_snapshot": {
                "category": cat, "urgency": urg, "customer_sentiment": "frustrated" if urg == "High" else "neutral",
                "triage_reasoning": f"Ticket identified as {cat}/{urg} — awaiting specialist routing result.",
                "billing_info" if is_billing else "relevant_docs": {"invoice_id": f"INV-{20000+i}", "invoice_status": "pending"} if is_billing else [{"doc_id": f"KB-0{i:02d}", "title": sent, "snippet": "Relevant documentation retrieved.", "url": "https://docs.example.com", "relevance_score": 0.6}],
                "draft_reply": f"Dear Customer,\n\nThank you for contacting support regarding: {sent}. Our team has reviewed your case and prepared a resolution. Please see the details below...\n\n[Draft reply pending human review — ticket: {cid}]\n\nWarm regards,\nSupport Team",
                "draft_word_count": 55,
            },
            "last_error": None,
            "created_at": base,
            "updated_at": base + timedelta(seconds=15),
            "completed_at": None,
            "step_log": make_step_log(base, [
                {"step": "triage", "status": "completed", "duration_s": 2, "evidence": {"category": cat, "urgency": urg}},
                {"step": "billing_lookup" if is_billing else "doc_search", "status": "completed", "duration_s": 1, "evidence": {"result": "retrieved"}},
                {"step": "draft_reply", "status": "completed", "duration_s": 4, "evidence": {"word_count": 55}},
                {"step": "human_approval", "status": "pending", "duration_s": 0, "evidence": {"awaiting": "human reviewer"}},
            ]),
        })

    # =========================================================================
    # FAILED — With recoverable errors
    # =========================================================================
    for i, (ticket, email, cid, cat, urg, error_step, error_msg) in enumerate([
        ("Payment processing error 500 on checkout — we can't collect payments at all right now. CUST-3021.", "payments@marketplace.com", "CUST-3021", "Billing", "High", "billing_lookup", "External billing API timeout after 30s — connection refused"),
        ("Our Zapier integration is not triggering when records are created. All other triggers work. CUST-3022.", "automation@hrtech.co", "CUST-3022", "Tech", "Low", "doc_search", "Vector search service returned 503 — retryable"),
        ("I cannot download my data export — it says 'Export failed' with no explanation. CUST-3023.", "legal@lawfirm.com", "CUST-3023", "Tech", "High", "doc_search", "LLM structured output parsing failed — invalid JSON response"),
        ("We were charged the annual rate instead of monthly. CUST-3024.", "finance@consulting.ca", "CUST-3024", "Billing", "High", "billing_lookup", "Injected failure — billing API returned 500 during testing"),
        ("Slack notifications stopped working after we rotated our Slack OAuth token. CUST-3025.", "devops@media-company.nl", "CUST-3025", "Tech", "Low", "doc_search", "Doc search timed out — vector DB connection pool exhausted"),
    ], start=21):
        base = now - timedelta(days=i % 7, hours=i)
        is_billing = cat == "Billing"
        records.append({
            "run_id": str(uuid.uuid4()),
            "status": "failed",
            "category": cat,
            "urgency": urg,
            "ticket_text": ticket,
            "customer_email": email,
            "customer_id": cid,
            "inject_failure": False,
            "retry_count": 0,
            "reviewer_note": None,
            "reviewed_by": None,
            "artifacts_snapshot": {
                "category": cat, "urgency": urg,
                "triage_reasoning": f"Triage completed successfully — failed at specialist routing step.",
            },
            "last_error": error_msg,
            "created_at": base,
            "updated_at": base + timedelta(seconds=8),
            "completed_at": None,
            "step_log": make_step_log(base, [
                {"step": "triage", "status": "completed", "duration_s": 2, "evidence": {"category": cat, "urgency": urg}},
                {"step": error_step, "status": "failed", "duration_s": 5, "evidence": {"error": error_msg}},
            ]),
        })

    # =========================================================================
    # CANCELLED
    # =========================================================================
    for i, (ticket, email, cid, cat, urg) in enumerate([
        ("Test ticket — ignore please. CUST-4026.", "test@internal.com", "CUST-4026", "Tech", "Low"),
        ("Duplicate ticket — already resolved by phone. CUST-4027.", "support@client.com", "CUST-4027", "Billing", "Low"),
        ("Wrong department — please forward to sales team. CUST-4028.", "inquiry@prospect.com", "CUST-4028", "Tech", "Low"),
        ("Submitted by mistake while testing the portal. CUST-4029.", "qa@vendor.com", "CUST-4029", "Billing", "Low"),
        ("Old ticket — customer confirmed issue resolved themselves. CUST-4030.", "user@customer.com", "CUST-4030", "Tech", "Low"),
    ], start=26):
        base = now - timedelta(days=i + 3, hours=i)
        records.append({
            "run_id": str(uuid.uuid4()),
            "status": "cancelled",
            "category": cat,
            "urgency": urg,
            "ticket_text": ticket,
            "customer_email": email,
            "customer_id": cid,
            "inject_failure": False,
            "retry_count": 0,
            "reviewer_note": "Cancelled by support team — invalid ticket.",
            "reviewed_by": "agent_ops",
            "artifacts_snapshot": {"category": cat, "urgency": urg},
            "last_error": None,
            "created_at": base,
            "updated_at": base + timedelta(minutes=2),
            "completed_at": base + timedelta(minutes=2),
            "step_log": make_step_log(base, [
                {"step": "triage", "status": "completed", "duration_s": 2, "evidence": {"category": cat, "urgency": urg}},
                {"step": "cancelled", "status": "cancelled", "duration_s": 0, "evidence": {"reason": "Cancelled by agent before specialist routing"}},
            ]),
        })

    # =========================================================================
    # REJECTED — Human reviewer rejected the draft
    # =========================================================================
    for i, (ticket, email, cid, cat, urg, rejection) in enumerate([
        ("I'd like to understand why my team plan costs more than advertised on your website. CUST-5031.", "cfo@startup.io", "CUST-5031", "Billing", "Low", "Draft references incorrect pricing tier — needs manual correction before sending."),
        ("The API docs say the endpoint accepts PATCH but it returns 405 Method Not Allowed. CUST-5032.", "dev@appbuilder.com", "CUST-5032", "Tech", "High", "Draft is too technical and assumes expertise the customer may not have. Rewrite in simpler language."),
        ("My password reset email never arrived — I've checked spam too. CUST-5033.", "user@personal.com", "CUST-5033", "Tech", "Low", "Draft tone is too impersonal for a simple account recovery issue. Needs warmer tone."),
        ("We received a refund but it went to the wrong card — not the one that was charged. CUST-5034.", "accounts@retailbrand.com", "CUST-5034", "Billing", "High", "Response lacks specific timeline for refund transfer investigation. Needs concrete SLA commitment."),
        ("Custom domain setup not working — DNS propagated but SSL certificate not issued. CUST-5035.", "webmaster@agencywork.com", "CUST-5035", "Tech", "Low", "Draft missing the step about waiting for automatic certificate provisioning — technically incomplete."),
    ], start=31):
        base = now - timedelta(days=i + 1, hours=(i * 3) % 12)
        is_billing = cat == "Billing"
        records.append({
            "run_id": str(uuid.uuid4()),
            "status": "rejected",
            "category": cat,
            "urgency": urg,
            "ticket_text": ticket,
            "customer_email": email,
            "customer_id": cid,
            "inject_failure": False,
            "retry_count": 0,
            "reviewer_note": rejection,
            "reviewed_by": "agent_maya",
            "artifacts_snapshot": {
                "category": cat, "urgency": urg,
                "draft_reply": "Draft reply generated — rejected by human reviewer before sending.",
                "approval_status": "rejected",
                "reviewer_note": rejection,
                "email_sent": False,
            },
            "last_error": None,
            "created_at": base,
            "updated_at": base + timedelta(minutes=15),
            "completed_at": base + timedelta(minutes=15),
            "step_log": make_step_log(base, [
                {"step": "triage", "status": "completed", "duration_s": 2, "evidence": {"category": cat, "urgency": urg}},
                {"step": "billing_lookup" if is_billing else "doc_search", "status": "completed", "duration_s": 1, "evidence": {"result": "retrieved"}},
                {"step": "draft_reply", "status": "completed", "duration_s": 4, "evidence": {"word_count": 95}},
                {"step": "human_approval", "status": "rejected", "duration_s": 840, "evidence": {"approval_status": "rejected", "reviewer_note": rejection, "reviewed_by": "agent_maya"}},
            ]),
        })

    # =========================================================================
    # MORE COMPLETED — Mixed Billing and Tech to reach 50 total
    # =========================================================================
    extra_completed = [
        # Billing
        ("My Team plan auto-renewed but I wanted to cancel first. Can I get a refund for the unused period? CUST-6036.", "operations@NGO.org", "CUST-6036", "Billing", "Low", "agent_sarah"),
        ("We're migrating from Salesforce to your platform — how do I transfer 80,000 contacts? CUST-6037.", "crm@enterprise.com", "CUST-6037", "Tech", "Low", "agent_carlos"),
        ("The API returns a 401 for some endpoints but not others using the same API key. CUST-6038.", "platform@devshop.uk", "CUST-6038", "Tech", "High", "agent_ravi"),
        ("I accidentally deleted a table with 10,000 records — is there any way to recover it? CUST-6039.", "db.admin@healthtech.com", "CUST-6039", "Tech", "High", "agent_sarah"),
        ("Can you confirm if our account is on the correct enterprise pricing? We're being charged per-seat. CUST-6040.", "vp.finance@scaleup.com", "CUST-6040", "Billing", "Low", "agent_maya"),
        ("Notifications are being delivered to Slack but not to email — I need both. CUST-6041.", "pm@productteam.io", "CUST-6041", "Tech", "Low", "agent_carlos"),
        ("Our payment failed because our corporate card expired. How do I update the payment method? CUST-6042.", "finance@consulting-group.com", "CUST-6042", "Billing", "Low", "agent_ravi"),
        ("The IP allowlist is blocking our new office IP after a VPN configuration change. CUST-6043.", "network.admin@bank.com", "CUST-6043", "Tech", "High", "agent_sarah"),
        ("We were charged twice in August but I can only see one invoice in the portal. CUST-6044.", "billing@mediafirm.com", "CUST-6044", "Billing", "High", "agent_maya"),
        ("Custom automation rules stopped triggering after the latest platform update. CUST-6045.", "ops.lead@retailtech.com", "CUST-6045", "Tech", "High", "agent_carlos"),
        ("I need to add our new CFO as an Admin but I don't see the role option — only Owner is available. CUST-6046.", "hr@finserv.com", "CUST-6046", "Tech", "Low", "agent_ravi"),
        ("Our annual contract is up for renewal and we need a new PO number on the invoice. CUST-6047.", "procurement@manufacturing.co", "CUST-6047", "Billing", "Low", "agent_sarah"),
        ("The pagination cursor API is returning duplicate records across pages. CUST-6048.", "engineering@data-platform.com", "CUST-6048", "Tech", "High", "agent_carlos"),
        ("We want to export GDPR subject access request data for a user. How do we do this? CUST-6049.", "dpo@healthcare.eu", "CUST-6049", "Tech", "Low", "agent_maya"),
        ("Our NSF charge from last month shows as unpaid but we wired the funds directly. CUST-6050.", "accounts.payable@logistics.com", "CUST-6050", "Billing", "High", "agent_ravi"),
    ]

    for idx, (ticket, email, cid, cat, urg, reviewer) in enumerate(extra_completed, start=36):
        base = now - timedelta(hours=idx * 4 + 2)
        is_billing = cat == "Billing"
        draft = (
            f"Dear Customer,\n\nThank you for contacting our {'billing' if is_billing else 'technical'} support team. "
            f"We have reviewed your case (Account: {cid}) and {'located your billing records' if is_billing else 'found relevant documentation'} to assist you. "
            f"Our team has investigated your issue regarding {'invoice and payment details' if is_billing else 'the technical problem reported'} "
            f"and prepared a resolution. {'Your account balance and payment history have been verified.' if is_billing else 'The root cause has been identified and steps to resolve it are outlined below.'} "
            f"Please follow the recommended steps or reach out if you need further clarification. "
            f"{'A billing adjustment will be processed within 3-5 business days if applicable.' if is_billing else 'If the issue persists after following these steps, please share screenshots for escalation.'}"
            f"\n\nBest regards,\nSupport Team"
        )
        records.append({
            "run_id": str(uuid.uuid4()),
            "status": "completed",
            "category": cat,
            "urgency": urg,
            "ticket_text": ticket,
            "customer_email": email,
            "customer_id": cid,
            "inject_failure": False,
            "retry_count": 0,
            "reviewer_note": f"{'Billing data verified.' if is_billing else 'Technical guidance accurate.'} Approved.",
            "reviewed_by": reviewer,
            "artifacts_snapshot": {
                "category": cat, "urgency": urg,
                "triage_reasoning": f"Identified as {cat}/{urg} based on ticket content.",
                "billing_info" if is_billing else "relevant_docs": {"invoice_id": f"INV-{60000+idx}", "invoice_status": "reviewed"} if is_billing else [{"doc_id": f"KB-0{idx % 30 + 1:02d}", "title": "Relevant Documentation", "snippet": "Step-by-step resolution available.", "url": "https://docs.example.com", "relevance_score": 0.7}],
                "draft_reply": draft,
                "draft_word_count": len(draft.split()),
                "approval_status": "approved",
                "email_sent": True,
                "email_sent_at": ts(base, 350),
            },
            "last_error": None,
            "created_at": base,
            "updated_at": base + timedelta(seconds=355),
            "completed_at": base + timedelta(seconds=355),
            "step_log": make_step_log(base, [
                {"step": "triage", "status": "completed", "duration_s": 2, "evidence": {"category": cat, "urgency": urg}},
                {"step": "billing_lookup" if is_billing else "doc_search", "status": "completed", "duration_s": 1, "evidence": {"result": "data retrieved"}},
                {"step": "draft_reply", "status": "completed", "duration_s": 4, "evidence": {"word_count": len(draft.split())}},
                {"step": "human_approval", "status": "approved", "duration_s": 342, "evidence": {"reviewed_by": reviewer}},
                {"step": "send_email", "status": "completed", "duration_s": 1, "evidence": {"recipient": email}},
            ]),
        })

    return records


# ---------------------------------------------------------------------------
# Seeder
# ---------------------------------------------------------------------------
async def seed() -> None:
    logger.info("=" * 60)
    logger.info("  Workflow Orchestrator — Database Seeder")
    logger.info("  Target: 50 workflow run records")
    logger.info("=" * 60)

    await create_all_tables()

    records = build_records()
    logger.info(f"Built {len(records)} records in memory.")

    inserted = 0
    skipped = 0

    async with db_session() as session:
        # Check existing
        from sqlalchemy import func, select
        result = await session.execute(
            text("SELECT COUNT(*) FROM runs")
        )
        existing_count = result.scalar()

        if existing_count and existing_count > 0:
            logger.warning(
                f"Database already has {existing_count} run records. "
                "Inserting new records alongside existing ones..."
            )

        for rec in records:
            step_log = rec.pop("step_log")
            artifacts = rec.pop("artifacts_snapshot")

            run = Run(
                **rec,
                artifacts_snapshot=artifacts,
            )
            session.add(run)
            inserted += 1

        await session.flush()

    logger.info("=" * 60)
    logger.info(f"Seed complete:")
    logger.info(f"  Inserted : {inserted}")
    logger.info(f"  Skipped  : {skipped}")

    # Status breakdown
    status_counts: dict[str, int] = {}
    for r in records:
        s = r.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    logger.info("  Status breakdown:")
    for st, count in sorted(status_counts.items()):
        logger.info(f"    {st:<12} : {count}")

    cat_counts: dict[str, int] = {}
    for r in records:
        c = r.get("category") or "unknown"
        cat_counts[c] = cat_counts.get(c, 0) + 1
    logger.info("  Category breakdown:")
    for cat, count in sorted(cat_counts.items()):
        logger.info(f"    {cat:<12} : {count}")

    logger.info("=" * 60)
    logger.info("You can now view the data in PgAdmin under:")
    logger.info("  Database: Workflow  |  Schema: public  |  Table: runs")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(seed())
