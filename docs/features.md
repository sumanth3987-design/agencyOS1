# Complete Feature Inventory — AgencyOS

## 1. Client Management & CRM
- Customer profile with business type, website, target audience, and goals.
- Multi-business support under a single customer account.
- Secure, one-time magic token login links for zero-password access.
- Guided three-step project intake for brand context, delivery targets, and custom scope.

## 2. Website Milestone Delivery Engine
- 3-Phase Milestone Gates (50% Advance -> 25% Wireframe/Design -> 25% Launch).
- Deliverable submission tracking with live URL, preview URL, and completion notes.
- Client Sign-Off Gate with strict 2-revision limit.
- Signed SOW PDF downloads from the customer project workspace after electronic acceptance.
- 1-Click Paid Scope Add-on Generator (monetizing revisions 3+).
- Project activity audit timeline recording project creation, SOW signatures, payments, deliverable submissions, revision requests, and milestone approvals with actor and timestamp.

## 3. SEO Retainer Operations
- 30-Day recurring sprint cycle.
- 4-Part Deliverable Checklist (Technical, On-Page, Content, Reporting).
- Day-28 automatic client progress summary.
- Loss-aversion retention warnings before cycle pauses.

## 4. Indian Financials & Invoicing
- SAC Code 998311 tax categorization.
- Automatic tax breakdown: CGST (9%) + SGST (9%) for intra-state; IGST (18%) for inter-state.
- Razorpay Orders & Payment Links API integration.
- Webhook HMAC signature verification and payment idempotency.
- Starter Retainer e-Mandate / UPI AutoPay: Razorpay monthly plan, 12 scheduled cycles at ₹14,999, beginning on the first day of the next month.
- `subscription.charged` webhook validation and idempotent renewal reconciliation, with a paid payment record and a separate monthly invoice for each captured charge.
- Dynamic UPI QR Code rendering on invoices for direct bank settlement.
- Automated payment and deliverable review reminder emails with admin-triggered sends.

### Recurring subscription setup
- Apply the database migrations before enabling subscriptions. The Starter Retainer plan ID is created once and stored in the database; project checkout is restricted to the owning customer (or an admin) and to the exact `Starter Retainer` package.
- Configure Razorpay to send `subscription.charged`, `subscription.authenticated`, `subscription.activated`, `subscription.pending`, `subscription.halted`, `subscription.cancelled`, `subscription.completed`, `subscription.paused`, `subscription.resumed`, and `subscription.updated` events to the public `/subscription/webhook` endpoint. Set `RAZORPAY_WEBHOOK_SECRET` to the webhook secret configured in the Razorpay dashboard. The existing `/webhook` endpoint also accepts these events.
- Renewal payments and invoices are linked to the local subscription and gateway payment ID. Re-delivered webhook events are deduplicated and each invoice can be opened from the client invoice ledger.
- Subscription creation reserves a local project record before calling Razorpay; ambiguous gateway timeouts are blocked from automatic retries to avoid creating duplicate mandates and require reconciliation before another attempt.

### Scheduled reminder delivery
- Configure `APP_BASE_URL` to the trusted public application origin and configure the existing `MAIL_BACKEND`/SMTP settings.
- Schedule `flask --app app:create_app send-reminders` with the deployment's task scheduler (for example, Windows Task Scheduler or cron) to run every 15 minutes. The command sends payment reminders after 24 hours and review reminders after 48 hours, recording successful sends to prevent repeat automatic reminders.
- Apply the database migrations before deploying this feature.

## 5. Fund Flow & Talent Payout Architecture
- 100% upfront agency collection via Razorpay into primary agency account.
- Milestone escrow release: Talent payouts marked `Ready to Pay` only upon client deliverable approval.
- Specialist banking profile: `upi_id`, `bank_account_number`, `bank_ifsc`, and `pan_number`.
- Admin payout tracking: `payout_status`, `payout_utr`, and `payout_paid_at`.
- Admin specialist roster meritocracy metrics use completed assigned sprints, captured customer reviews, revision delivery records, and explicit milestone deadlines. On-time rate and star rating display as unavailable until there is supporting data; performance badges are awarded only when their evidence-based thresholds are met.

## 6. Corbel-Style Executive Workspace
- Studio Dark aesthetic inspired by Linear and Corbel Supply Co.
- Executive KPI tiles with period-over-period delta chips.
- 7-Stage visual pipeline tracker (`Pending Assignment` to `Completed`).
- Expandable project details rows and instant keyboard search (`/` shortcut).
- Admin-managed global agency name, optional logo, primary color, and WhatsApp support number, reflected across client/specialist portals and invoice issuer identity.
- Existing installations should apply the additive agency settings Alembic migration; absent settings rows continue to render the AgencyOS defaults.

## 7. AI Internal Copilot (Google Gemini)
- Instant SOW & Proposal Generator based on client bullet points.
- Automated Monthly SEO Client Performance Email Drafter.
- Free AI Website & Local SEO Audit Lead Scout.

## 8. Statutory & Legal Compliance
- India DPDP Act (2023) compliant privacy policy.
- Razorpay mandatory legal pages (`/privacy`, `/terms`, `/refund-policy`, `/contact`).
- Form consent notices on lead capture endpoints.