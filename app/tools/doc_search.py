"""
app/tools/doc_search.py
Mock vector document search tool.

Simulates a semantic search over a technical knowledge base. In production,
replace `search_docs` with a real call to a vector DB (pgvector, Pinecone,
Weaviate, etc.) using embeddings of the ticket text.

The mock uses keyword matching to return contextually relevant documentation
snippets, making the search results realistic and varied by ticket content.
"""

import re
from typing import Any


# ---------------------------------------------------------------------------
# Knowledge base — 30 technical documentation entries across common topics
# ---------------------------------------------------------------------------
_KNOWLEDGE_BASE: list[dict[str, Any]] = [
    {
        "doc_id": "KB-001",
        "title": "How to Reset Your Password",
        "category": "Account Management",
        "keywords": ["password", "reset", "login", "access", "forgot", "locked"],
        "snippet": (
            "To reset your password: navigate to the login page and click 'Forgot Password'. "
            "Enter your registered email address. You will receive a reset link within 2 minutes. "
            "The link expires after 30 minutes. If you don't receive the email, check your spam folder "
            "or contact support with your account ID."
        ),
        "url": "https://docs.example.com/account/reset-password",
        "last_updated": "2026-09-01",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-002",
        "title": "Setting Up Two-Factor Authentication (2FA)",
        "category": "Security",
        "keywords": ["2fa", "two-factor", "authenticator", "otp", "verification", "security", "totp"],
        "snippet": (
            "Enable 2FA under Settings > Security > Two-Factor Authentication. "
            "We support Google Authenticator, Authy, and SMS codes. To disable 2FA, you must "
            "verify your identity via email. If you've lost access to your authenticator app, "
            "use a backup recovery code from your initial setup, or contact support with proof of identity."
        ),
        "url": "https://docs.example.com/security/2fa-setup",
        "last_updated": "2026-08-15",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-003",
        "title": "API Rate Limits and Quotas",
        "category": "API",
        "keywords": ["rate limit", "429", "quota", "throttle", "api", "requests", "too many"],
        "snippet": (
            "Free tier: 100 requests/minute, 10,000/day. Pro: 1,000/min, 500,000/day. "
            "Enterprise: custom limits. When you exceed the limit, you receive a 429 status with a "
            "Retry-After header indicating seconds to wait. Implement exponential backoff starting at 1s. "
            "Rate limit counters reset on a rolling 60-second window, not at the top of the minute."
        ),
        "url": "https://docs.example.com/api/rate-limits",
        "last_updated": "2026-09-20",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-004",
        "title": "Webhook Configuration and Troubleshooting",
        "category": "Integrations",
        "keywords": ["webhook", "endpoint", "payload", "event", "callback", "notification", "http"],
        "snippet": (
            "Configure webhooks at Dashboard > Settings > Webhooks. Enter your HTTPS endpoint URL "
            "and select the events to subscribe to. We send a POST request with JSON payload and include "
            "an X-Signature-256 header for verification. Webhooks time out after 10 seconds; we retry "
            "up to 5 times with exponential backoff. Check the webhook delivery log for failed attempts."
        ),
        "url": "https://docs.example.com/integrations/webhooks",
        "last_updated": "2026-07-10",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-005",
        "title": "Exporting Your Data",
        "category": "Data Management",
        "keywords": ["export", "download", "data", "csv", "backup", "gdpr", "portability"],
        "snippet": (
            "Export your data from Settings > Data > Export. Choose format: CSV, JSON, or XML. "
            "Large exports (>100,000 records) are processed asynchronously and sent to your email "
            "as a download link valid for 48 hours. Data exports include all records created within "
            "your selected date range. Exports comply with GDPR Article 20 data portability requirements."
        ),
        "url": "https://docs.example.com/data/export",
        "last_updated": "2026-06-22",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-006",
        "title": "Understanding Error Codes",
        "category": "API",
        "keywords": ["error", "500", "400", "401", "403", "404", "error code", "status code"],
        "snippet": (
            "Common error codes: 400 Bad Request — malformed input, check your JSON schema. "
            "401 Unauthorized — API key missing or expired, regenerate in Settings > API. "
            "403 Forbidden — your plan doesn't include this endpoint. "
            "404 Not Found — resource doesn't exist or was deleted. "
            "500 Internal Server Error — retry with backoff; if persistent, check status.example.com."
        ),
        "url": "https://docs.example.com/api/error-codes",
        "last_updated": "2026-09-05",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-007",
        "title": "Team Member Permissions and Roles",
        "category": "Team Management",
        "keywords": ["permission", "role", "team", "member", "admin", "access", "invite", "user management"],
        "snippet": (
            "Roles: Owner (full access), Admin (manage members, billing), Member (use features), "
            "Viewer (read-only). Invite members via Settings > Team > Invite. "
            "Role changes take effect immediately. Owners cannot be removed without transferring ownership. "
            "SSO-provisioned accounts inherit roles from your identity provider's group mappings."
        ),
        "url": "https://docs.example.com/team/roles-permissions",
        "last_updated": "2026-08-30",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-008",
        "title": "SSO and SAML Configuration",
        "category": "Security",
        "keywords": ["sso", "saml", "okta", "azure", "active directory", "single sign-on", "idp"],
        "snippet": (
            "SAML 2.0 SSO is available on Enterprise plans. Configure at Settings > Security > SSO. "
            "Provide your IdP metadata URL or upload the XML file. The ACS URL is "
            "https://app.example.com/auth/saml/callback. Supported providers: Okta, Azure AD, "
            "Google Workspace, OneLogin. Test your SAML config before enforcing SSO to avoid lockout."
        ),
        "url": "https://docs.example.com/security/sso-saml",
        "last_updated": "2026-09-15",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-009",
        "title": "Mobile App Login Issues",
        "category": "Mobile",
        "keywords": ["mobile", "app", "ios", "android", "crash", "login", "phone", "tablet"],
        "snippet": (
            "If the mobile app fails to load or login: force-close the app and reopen. "
            "Ensure you're on app version 4.2.0 or higher (check App Store / Play Store for updates). "
            "Clear app cache: Settings > Apps > Example App > Clear Cache. "
            "If login loops, try signing out and back in. Enable biometric login only after a "
            "successful password login at least once."
        ),
        "url": "https://docs.example.com/mobile/troubleshooting",
        "last_updated": "2026-10-01",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-010",
        "title": "Integrating with Slack",
        "category": "Integrations",
        "keywords": ["slack", "notification", "channel", "integration", "alert", "message"],
        "snippet": (
            "Connect Slack at Integrations > Slack > Add to Slack. Authorise the app to post to your "
            "chosen channels. Configure notification rules to send alerts for specific events. "
            "Each workspace can connect one Slack workspace. To disconnect: Integrations > Slack > Remove. "
            "Slack DM notifications require the user to individually install the app in Slack."
        ),
        "url": "https://docs.example.com/integrations/slack",
        "last_updated": "2026-07-25",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-011",
        "title": "File Upload Limits and Supported Formats",
        "category": "Features",
        "keywords": ["upload", "file", "size", "format", "attachment", "limit", "mb", "gb"],
        "snippet": (
            "Maximum file size: Free 25MB, Pro 500MB, Enterprise 5GB per file. "
            "Supported formats: PDF, DOCX, XLSX, PNG, JPG, GIF, MP4, ZIP, CSV. "
            "Bulk uploads accept up to 50 files at once. Files are scanned for malware on upload. "
            "Storage quota: Free 5GB, Pro 100GB, Enterprise unlimited. "
            "Files deleted from the UI are permanently removed after 30 days."
        ),
        "url": "https://docs.example.com/features/file-uploads",
        "last_updated": "2026-08-05",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-012",
        "title": "API Authentication with API Keys",
        "category": "API",
        "keywords": ["api key", "authentication", "token", "bearer", "header", "authorization"],
        "snippet": (
            "Generate API keys at Settings > API > New Key. Keys are shown once on creation — copy and "
            "store securely. Include in requests as: Authorization: Bearer YOUR_API_KEY. "
            "Keys can be scoped to specific endpoints (read-only, write, admin). "
            "Rotate keys without downtime: generate a new key, update your service, then revoke the old one. "
            "Keys do not expire unless you set a custom expiry date."
        ),
        "url": "https://docs.example.com/api/authentication",
        "last_updated": "2026-09-10",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-013",
        "title": "Dashboard Not Loading or Blank Screen",
        "category": "Troubleshooting",
        "keywords": ["dashboard", "blank", "loading", "spinner", "white screen", "slow", "frozen"],
        "snippet": (
            "If the dashboard shows a blank or frozen screen: hard-refresh with Ctrl+Shift+R (Windows) "
            "or Cmd+Shift+R (Mac). Disable browser extensions that may block scripts. "
            "Try an incognito window to rule out cached data. Supported browsers: Chrome 110+, "
            "Firefox 110+, Safari 16+, Edge 110+. Check status.example.com for ongoing incidents. "
            "Clear cookies and local storage if the issue persists after a browser update."
        ),
        "url": "https://docs.example.com/troubleshooting/dashboard",
        "last_updated": "2026-09-28",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-014",
        "title": "Automations and Workflow Triggers",
        "category": "Features",
        "keywords": ["automation", "trigger", "workflow", "rule", "action", "condition", "scheduled"],
        "snippet": (
            "Create automations at Features > Automations > New Rule. Define a trigger event "
            "(record created, field changed, schedule), add conditions, and set actions "
            "(send email, update field, call webhook, notify team). "
            "Free: 5 automations, Pro: 100, Enterprise: unlimited. "
            "Automation run history is retained for 90 days. Use dry-run mode to test without triggering actions."
        ),
        "url": "https://docs.example.com/features/automations",
        "last_updated": "2026-08-18",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-015",
        "title": "How to Cancel or Pause Your Subscription",
        "category": "Billing",
        "keywords": ["cancel", "pause", "subscription", "downgrade", "terminate", "end plan"],
        "snippet": (
            "Cancel at Settings > Billing > Cancel Subscription. Cancellation takes effect at the end "
            "of your current billing period. Your data is retained for 60 days post-cancellation. "
            "To pause (available on Pro+): Settings > Billing > Pause — pauses billing for 1–3 months, "
            "access is restricted but data is preserved. You can reactivate at any time."
        ),
        "url": "https://docs.example.com/billing/cancel-pause",
        "last_updated": "2026-07-30",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-016",
        "title": "Slow Performance and Timeout Errors",
        "category": "Troubleshooting",
        "keywords": ["slow", "timeout", "performance", "latency", "504", "gateway", "response time"],
        "snippet": (
            "If requests time out (504 Gateway Timeout): check your payload size — large requests "
            "should use the async batch endpoint. For dashboard slowness, reduce date range filters. "
            "Network latency: use the nearest regional API endpoint (US: api.example.com, "
            "EU: eu.api.example.com, APAC: ap.api.example.com). "
            "Server-side issues are posted at status.example.com within 5 minutes of detection."
        ),
        "url": "https://docs.example.com/troubleshooting/performance",
        "last_updated": "2026-10-02",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-017",
        "title": "Custom Domain Setup",
        "category": "Configuration",
        "keywords": ["custom domain", "dns", "cname", "ssl", "domain", "subdomain", "certificate"],
        "snippet": (
            "Set a custom domain at Settings > Branding > Custom Domain. Add a CNAME record pointing "
            "your subdomain to custom.example.com. DNS propagation takes 5–48 hours. "
            "SSL certificates are provisioned automatically via Let's Encrypt within 1 hour of DNS validation. "
            "Wildcard subdomains are supported on Enterprise plans only."
        ),
        "url": "https://docs.example.com/configuration/custom-domain",
        "last_updated": "2026-06-15",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-018",
        "title": "Email Delivery and SMTP Configuration",
        "category": "Configuration",
        "keywords": ["email", "smtp", "delivery", "bounce", "spam", "sendgrid", "ses", "mail"],
        "snippet": (
            "Configure outbound email at Settings > Notifications > Email. Use our shared sender "
            "(noreply@example.com) or provide your own SMTP credentials. "
            "To improve deliverability, add SPF and DKIM records to your domain DNS. "
            "Bounced emails are automatically suppressed after 3 failures. "
            "View email delivery logs at Settings > Notifications > Delivery Log."
        ),
        "url": "https://docs.example.com/configuration/email-smtp",
        "last_updated": "2026-08-22",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-019",
        "title": "Account Deletion and Data Removal",
        "category": "Account Management",
        "keywords": ["delete account", "remove", "gdpr", "right to erasure", "close account", "terminate"],
        "snippet": (
            "To delete your account: Settings > Account > Delete Account. You must be the account Owner. "
            "All data is permanently deleted within 30 days per GDPR Article 17. "
            "Active subscriptions must be cancelled before deletion. "
            "Download your data export before deleting. Team accounts require all members to be removed first. "
            "Account deletion cannot be undone."
        ),
        "url": "https://docs.example.com/account/delete-account",
        "last_updated": "2026-09-01",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-020",
        "title": "Integration with Zapier and Make (Formerly Integromat)",
        "category": "Integrations",
        "keywords": ["zapier", "make", "integromat", "automation", "no-code", "connect", "trigger"],
        "snippet": (
            "Official Zapier app available at zapier.com/apps/example. Supports triggers: new record, "
            "record updated, status changed. Actions: create record, update record, send notification. "
            "Make integration: search 'Example App' in Make's app library. "
            "For custom integrations, use our REST API with your API key. "
            "Webhooks provide real-time events as an alternative to polling."
        ),
        "url": "https://docs.example.com/integrations/zapier-make",
        "last_updated": "2026-07-15",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-021",
        "title": "Bulk Import via CSV",
        "category": "Data Management",
        "keywords": ["import", "csv", "bulk", "upload", "migrate", "batch", "spreadsheet"],
        "snippet": (
            "Import records via Dashboard > Data > Import > CSV. Download the template to ensure "
            "correct column headers. Maximum 50,000 rows per file. Duplicate detection is based on "
            "the unique identifier column — duplicates are skipped by default or can overwrite existing "
            "records. Import history and error reports are available for 30 days after each import."
        ),
        "url": "https://docs.example.com/data/bulk-import",
        "last_updated": "2026-08-10",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-022",
        "title": "Notifications Not Being Received",
        "category": "Troubleshooting",
        "keywords": ["notification", "email", "alert", "not receiving", "missing", "no email"],
        "snippet": (
            "If you're not receiving notifications: check Settings > Notifications to ensure they're "
            "enabled for your account. Check your email spam/junk folder and whitelist noreply@example.com. "
            "For browser notifications, ensure permissions are granted in your browser settings. "
            "Push notifications on mobile require Background App Refresh to be enabled. "
            "Test delivery with the 'Send Test Notification' button in notification settings."
        ),
        "url": "https://docs.example.com/troubleshooting/notifications",
        "last_updated": "2026-09-18",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-023",
        "title": "Audit Log and Activity History",
        "category": "Security",
        "keywords": ["audit", "log", "activity", "history", "who", "changes", "track"],
        "snippet": (
            "Access the audit log at Settings > Security > Audit Log (Enterprise plan). "
            "Logs include: login attempts, settings changes, data exports, API key usage, member role changes. "
            "Filter by user, action type, IP address, or date range. "
            "Audit logs are retained for 1 year. Export to CSV for compliance reporting. "
            "Real-time SIEM export via webhook is available on request."
        ),
        "url": "https://docs.example.com/security/audit-log",
        "last_updated": "2026-09-25",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-024",
        "title": "Offline Mode and Data Sync",
        "category": "Mobile",
        "keywords": ["offline", "sync", "connection", "no internet", "mobile", "cache"],
        "snippet": (
            "The mobile app supports offline mode for viewing and editing previously loaded records. "
            "Changes made offline are queued and sync automatically when connectivity is restored. "
            "Conflict resolution: server version wins for concurrent edits. "
            "Offline mode stores up to 500MB of data locally. "
            "Force sync: pull down on the main list to trigger an immediate sync."
        ),
        "url": "https://docs.example.com/mobile/offline-mode",
        "last_updated": "2026-08-28",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-025",
        "title": "Migrating from Another Platform",
        "category": "Data Management",
        "keywords": ["migrate", "migration", "import", "transfer", "move", "switch", "onboarding"],
        "snippet": (
            "For full platform migrations, use our Migration Wizard at Settings > Data > Migrate. "
            "Supported sources: Salesforce, HubSpot, Airtable, Notion, Monday.com, CSV. "
            "The wizard maps columns, previews data, and runs a test import before the full migration. "
            "Large migrations (>1M records) are processed overnight. "
            "Our migration team offers free assisted migrations for Enterprise customers."
        ),
        "url": "https://docs.example.com/data/migration",
        "last_updated": "2026-07-05",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-026",
        "title": "IP Allowlisting and Network Security",
        "category": "Security",
        "keywords": ["ip", "allowlist", "whitelist", "firewall", "network", "vpn", "restrict"],
        "snippet": (
            "Restrict access to specific IP ranges at Settings > Security > IP Allowlist (Enterprise). "
            "Enter individual IPs or CIDR ranges (e.g. 192.168.1.0/24). "
            "Add your office IP and any VPN exit IPs before enabling — accounts locked out require "
            "identity verification to regain access. "
            "Allowlist changes take effect within 60 seconds. "
            "API requests follow the same IP allowlist as web access."
        ),
        "url": "https://docs.example.com/security/ip-allowlist",
        "last_updated": "2026-08-12",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-027",
        "title": "Custom Fields and Data Types",
        "category": "Features",
        "keywords": ["custom field", "field type", "schema", "column", "attribute", "data model"],
        "snippet": (
            "Add custom fields at Settings > Data Model > Add Field. "
            "Supported types: Text, Number, Date, Boolean, Select (single/multi), URL, Email, Phone, "
            "File, Relation (link to another table), Formula, Rollup. "
            "Fields can be marked required, unique, or read-only. "
            "Changing a field type is non-destructive — existing data is preserved as-is. "
            "Formula fields support basic math, string functions, and date arithmetic."
        ),
        "url": "https://docs.example.com/features/custom-fields",
        "last_updated": "2026-09-12",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-028",
        "title": "Session Timeout and Auto-Logout",
        "category": "Security",
        "keywords": ["session", "logout", "timeout", "idle", "expire", "kicked out", "signed out"],
        "snippet": (
            "Sessions expire after 8 hours of inactivity by default. Admins can configure this "
            "at Settings > Security > Session Policy (30 min to 30 days). "
            "Active browser sessions refresh the timer on each interaction. "
            "'Remember me' extends sessions to 30 days on trusted devices. "
            "If you're being logged out unexpectedly, check for conflicting browser extensions "
            "or ensure cookies are not blocked by privacy settings."
        ),
        "url": "https://docs.example.com/security/session-management",
        "last_updated": "2026-09-08",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-029",
        "title": "Reporting and Dashboard Builder",
        "category": "Features",
        "keywords": ["report", "chart", "dashboard", "analytics", "graph", "visualize", "metrics"],
        "snippet": (
            "Build custom reports at Analytics > Reports > New Report. "
            "Chart types: bar, line, pie, scatter, table, funnel, heatmap. "
            "Filter by date range, field value, or team member. "
            "Schedule reports to be emailed weekly or monthly. "
            "Dashboard builder: drag-and-drop up to 20 widgets per dashboard. "
            "Share dashboards publicly with a view-only link or embed via iframe."
        ),
        "url": "https://docs.example.com/features/reporting",
        "last_updated": "2026-08-20",
        "relevance_score": 0.0,
    },
    {
        "doc_id": "KB-030",
        "title": "API Pagination and Large Result Sets",
        "category": "API",
        "keywords": ["pagination", "cursor", "page", "limit", "offset", "large", "results", "list"],
        "snippet": (
            "All list endpoints support cursor-based pagination. "
            "Pass ?limit=100&cursor=<next_cursor> from the previous response. "
            "Maximum page size is 1,000 records. For bulk exports, use the async export endpoint "
            "which processes in the background and notifies via webhook when ready. "
            "The total count is returned in the X-Total-Count response header. "
            "Avoid offset-based pagination on large datasets — use cursor pagination to prevent drift."
        ),
        "url": "https://docs.example.com/api/pagination",
        "last_updated": "2026-09-22",
        "relevance_score": 0.0,
    },
]


def _compute_relevance(ticket_text: str, keywords: list[str]) -> float:
    """Score a doc entry by keyword match count against the ticket text."""
    text_lower = ticket_text.lower()
    matched = sum(1 for kw in keywords if kw.lower() in text_lower)
    return round(matched / max(len(keywords), 1), 4)


async def search_docs(ticket_text: str, top_k: int = 3) -> list[dict[str, Any]]:
    """
    Mock vector document search.

    Scores each knowledge base entry against the ticket text using keyword
    overlap (simulates cosine similarity from a real vector store).
    Returns the top_k most relevant entries.

    Args:
        ticket_text: The raw customer support ticket text.
        top_k: Number of results to return (default 3).

    Returns:
        List of documentation dicts sorted by relevance score descending.
    """
    scored: list[dict[str, Any]] = []
    for entry in _KNOWLEDGE_BASE:
        score = _compute_relevance(ticket_text, entry["keywords"])
        doc = entry.copy()
        doc["relevance_score"] = score
        scored.append(doc)

    scored.sort(key=lambda d: d["relevance_score"], reverse=True)

    # Always return at least top_k results; fall back to first top_k if all zero
    results = scored[:top_k]

    # Strip internal keyword list from the response (not needed by the LLM node)
    for r in results:
        r.pop("keywords", None)

    return results
