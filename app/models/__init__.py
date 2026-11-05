"""AgencyOS Database Models & Repository Definitions.

Defines SQLAlchemy ORM models for users, multi-tenant businesses, professional profiles,
customer profiles with direct contact & GST context, gated projects, cryptographic payment ledgers,
invoice margin splits, two-stage escrow wallets, withdrawal requests, and activity audit trails.
"""

from datetime import datetime
import json
import re
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask_login import UserMixin, current_user
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager

IST = ZoneInfo("Asia/Kolkata")


def get_ist_now() -> datetime:
    return datetime.now(IST)


# =========================================================
# URL VALIDATION
# =========================================================

def is_valid_http_url(value: str | None) -> bool:
    """Validate that a supplied URL uses HTTP or HTTPS."""
    if not value:
        return False

    try:
        parsed = urlparse(value.strip())
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


# =========================================================
# PACKAGE PRICES & SERVICES CATALOGUE
# =========================================================

PACKAGE_PRICES = {
    "Basic Website": 48000,
    "Business Website": 78000,
    "Advanced Website": 125000,
    "SEO Starter": 24000,
    "SEO Growth": 48000,
    "SEO Advanced": 96000,
    "Starter Retainer": 14999,
}

services = [
    {
        "slug": "website-development",
        "name": "Website Design & Development",
        "description": "Professional websites for businesses, portfolios, startups and organizations.",
        "icon": "🌐",
        "packages": [
            {"id": "website-basic", "name": "Basic Website", "price": 48000},
            {"id": "website-business", "name": "Business Website", "price": 78000},
            {"id": "website-advanced", "name": "Advanced Website", "price": 125000},
        ],
    },
    {
        "slug": "seo",
        "name": "SEO",
        "description": "Search engine optimization to improve website visibility and organic traffic.",
        "icon": "📈",
        "packages": [
            {"id": "seo-starter", "name": "SEO Starter", "price": 24000},
            {"id": "seo-growth", "name": "SEO Growth", "price": 48000},
            {"id": "seo-advanced", "name": "SEO Advanced", "price": 96000},
            {
                "id": "seo-starter-retainer",
                "name": "Starter Retainer",
                "price": 14999,
                "billing_period": "month",
            },
        ],
    },
    {
        "slug": "custom-web-application",
        "name": "Custom Web Application",
        "description": "Bespoke SaaS platforms and internal tools.",
        "icon": "⚡",
        "packages": [
            {"id": "custom-app", "name": "Custom Web Application", "price": 125000}
        ],
    },
]


# =========================================================
# USER MODEL (WITH ADMIN KYC VERIFICATION)
# =========================================================

class User(UserMixin, db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    full_name = db.Column(db.String(120), nullable=True)
    email = db.Column(db.String(120), nullable=False, unique=True, index=True)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), default="customer", nullable=False)

    # Ignore Pylance override warning for Flask-Login compatibility
    is_active = db.Column(db.Boolean, default=True, nullable=False)  # type: ignore[override]

    # Admin KYC Verification Gate
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    verification_status = db.Column(db.String(30), default="Pending", nullable=False)  # Pending, Verified, Rejected
    verified_at = db.Column(db.DateTime, nullable=True)
    rejection_reason = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
    email_verified_at = db.Column(db.DateTime, nullable=True)
    session_version = db.Column(db.Integer, nullable=False, default=0)

    def set_password(self, password: str) -> None:
        """Hash and store the given plain-text password."""
        self.password = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Return True if the given plain-text password matches the stored hash."""
        return check_password_hash(self.password, password)

    # Relationships
    projects = db.relationship("Project", backref="customer", foreign_keys="Project.customer_id", lazy=True)
    assigned_projects = db.relationship(
        "Project", backref="assigned_professional", foreign_keys="Project.assigned_professional_id", lazy=True
    )
    professional_profile = db.relationship(
        "ProfessionalProfile",
        backref="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    customer_profile = db.relationship(
        "CustomerProfile",
        backref="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    businesses = db.relationship(
        "Business",
        back_populates="customer",
        cascade="all, delete-orphan",
        lazy=True,
    )
    withdrawal_requests = db.relationship(
        "WithdrawalRequest",
        back_populates="specialist",
        lazy=True,
        cascade="all, delete-orphan",
    )
    wallet_transactions = db.relationship(
        "WalletTransaction",
        back_populates="user",
        lazy=True,
        cascade="all, delete-orphan",
    )


# =========================================================
# CUSTOMER PROFILE MODEL (WITH DIRECT CONTACT & TAX CONTEXT)
# =========================================================

class CustomerProfile(db.Model):
    __tablename__ = "customer_profile"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False, index=True
    )
    # Direct Communication
    phone = db.Column(db.String(30), nullable=True)               # Primary WhatsApp
    secondary_phone = db.Column(db.String(30), nullable=True)     # Emergency direct phone
    preferred_channel = db.Column(db.String(50), nullable=True, default="WhatsApp") # WhatsApp / Phone / Meet
    available_hours = db.Column(db.String(100), nullable=True)    # e.g. 10:00 AM - 7:00 PM IST
    billing_email = db.Column(db.String(120), nullable=True)      # Dedicated accounts email

    # Legal & Billing Identity
    business_name = db.Column(db.String(150), nullable=True)      # Registered Entity Name
    business_type = db.Column(db.String(100), nullable=True)      # Industry / Sector
    website = db.Column(db.String(300), nullable=True)
    gstin = db.Column(db.String(20), nullable=True)               # 15-digit GSTIN / Tax ID
    billing_address = db.Column(db.Text, nullable=True)           # Registered Office Address
    pincode = db.Column(db.String(15), nullable=True)             # Postal Code
    location = db.Column(db.String(150), nullable=True)           # City & State (Place of Supply)

    # Signatory Coordinates
    signatory_name = db.Column(db.String(120), nullable=True)     # Authorized Signatory
    signatory_title = db.Column(db.String(100), nullable=True)    # Designation / Role

    # General Attributes
    social_links = db.Column(db.Text, nullable=True)
    business_description = db.Column(db.Text, nullable=True)
    target_audience = db.Column(db.Text, nullable=True)
    business_goals = db.Column(db.Text, nullable=True)
    preferred_budget = db.Column(db.String(100), nullable=True)
    profile_completed = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime, server_default=db.func.now(), onupdate=db.func.now(), nullable=False
    )


# =========================================================
# BUSINESS ENTITY MODEL
# =========================================================

class Business(db.Model):
    __tablename__ = "business"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    name = db.Column(db.String(200), nullable=False)
    business_type = db.Column(db.String(150), nullable=True)
    location = db.Column(db.String(250), nullable=True)
    website = db.Column(db.String(500), nullable=True)
    social_links = db.Column(db.Text, nullable=True)
    description = db.Column(db.Text, nullable=True)
    target_audience = db.Column(db.Text, nullable=True)
    goals = db.Column(db.Text, nullable=True)
    preferred_budget = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=get_ist_now, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=get_ist_now, onupdate=get_ist_now, nullable=False
    )

    customer = db.relationship("User", back_populates="businesses")
    projects = db.relationship("Project", back_populates="business", lazy=True)


# =========================================================
# USER TOKENS (PASSWORD RESET & VERIFICATION)
# =========================================================

class UserToken(db.Model):
    __tablename__ = "user_token"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    purpose = db.Column(db.String(40), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)

    __table_args__ = (
        db.Index("ix_user_token_user_purpose", "user_id", "purpose"),
    )

    user = db.relationship(
        "User",
        backref=db.backref("tokens", lazy=True, cascade="all, delete-orphan"),
    )


# =========================================================
# PROFESSIONAL PROFILE MODEL (WITH TWO-STAGE ESCROW WALLET)
# =========================================================

class ProfessionalProfile(db.Model):
    __tablename__ = "professional_profile"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False, index=True
    )
    expertise = db.Column(db.String(100), nullable=False, default="")
    experience = db.Column(db.String(50), nullable=False, default="")
    about = db.Column(db.Text, nullable=True)
    phone = db.Column(db.String(30), nullable=True)
    location = db.Column(db.String(150), nullable=True)
    skills = db.Column(db.Text, nullable=True) # Comma-separated or JSON list
    technologies = db.Column(db.Text, nullable=True)
    skill_level = db.Column(db.String(50), nullable=True)
    project_types = db.Column(db.Text, nullable=True)
    portfolio = db.Column(db.String(500), nullable=True)
    github = db.Column(db.String(300), nullable=True)
    linkedin = db.Column(db.String(300), nullable=True)
    education = db.Column(db.Text, nullable=True)
    certifications = db.Column(db.Text, nullable=True)
    work_experience = db.Column(db.Text, nullable=True)
    availability = db.Column(db.String(50), nullable=True)
    current_workload = db.Column(db.String(100), nullable=True)

    # Two-Stage Escrow Ledger
    pending_balance = db.Column(db.Numeric(12, 2), default=0.00, nullable=False)   # Locked in-flight balance
    available_balance = db.Column(db.Numeric(12, 2), default=0.00, nullable=False) # Confirmed, ready for withdrawal

    # Payout & Banking Coordinates
    upi_id = db.Column(db.String(100), nullable=True)
    bank_account_number = db.Column(db.String(50), nullable=True)
    bank_ifsc = db.Column(db.String(20), nullable=True)
    bank_beneficiary_name = db.Column(db.String(120), nullable=True)
    pan_number = db.Column(db.String(20), nullable=True)

    verification_status = db.Column(db.String(30), default="Pending", nullable=False)
    verification_notes = db.Column(db.Text, nullable=True)
    profile_completed = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime, server_default=db.func.now(), onupdate=db.func.now(), nullable=False
    )

    def get_skills_list(self) -> list[str]:
        """Return standardized list of clean lowercase skills."""
        if not self.skills:
            return []
        try:
            parsed = json.loads(self.skills)
            if isinstance(parsed, list):
                return [str(s).strip().lower() for s in parsed if str(s).strip()]
        except Exception:
            pass
        # Fallback to splitting commas or spaces
        tokens = re.split(r"[,|\n/]+", self.skills)
        return [t.strip().lower() for t in tokens if t.strip()]

    def get_performance_metrics(self) -> dict[str, Any]:
        assigned_projects = (
            db.session.query(Project)
            .join(
                ProfessionalProjectApplication,
                ProfessionalProjectApplication.project_id == Project.id,
            )
            .filter(
                ProfessionalProjectApplication.professional_id == self.user_id,
                ProfessionalProjectApplication.status == "Assigned",
            )
            .all()
        )
        projects_by_id = {project.id: project for project in assigned_projects}
        project_ids = set(projects_by_id)
        completed_ids = {
            project.id for project in assigned_projects if project.status == "Completed"
        }

        deliveries = (
            ProjectDelivery.query.filter(
                ProjectDelivery.professional_id == self.user_id,
                ProjectDelivery.project_id.in_(project_ids),
            )
            .order_by(
                ProjectDelivery.project_id,
                ProjectDelivery.submission_number,
                ProjectDelivery.id,
            )
            .all()
            if project_ids
            else []
        )
        latest_deliveries = {}
        revision_count = 0
        for delivery in deliveries:
            latest_deliveries[delivery.project_id] = delivery
            if delivery.status == "Revision":
                revision_count += 1

        reviews = (
            Review.query.filter(Review.project_id.in_(completed_ids)).all()
            if completed_ids
            else []
        )
        ratings = [
            review.rating
            for review in reviews
            if review.rating is not None and 1 <= review.rating <= 5
        ]

        deadlines = {}
        if completed_ids:
            milestones = Milestone.query.filter(
                Milestone.project_id.in_(completed_ids),
                Milestone.due_date.isnot(None),
            ).all()
            for milestone in milestones:
                current_deadline = deadlines.get(milestone.project_id)
                if current_deadline is None or milestone.due_date > current_deadline:
                    deadlines[milestone.project_id] = milestone.due_date

        measurable_sprints = [
            project_id
            for project_id in completed_ids
            if project_id in deadlines and latest_deliveries.get(project_id) is not None
        ]
        on_time_sprints = sum(
            latest_deliveries[project_id].submitted_at <= deadlines[project_id]
            for project_id in measurable_sprints
        )
        early_sprints = sum(
            latest_deliveries[project_id].submitted_at < deadlines[project_id]
            for project_id in measurable_sprints
        )
        completed_count = len(completed_ids)
        assigned_count = len(project_ids)

        return {
            "completed_sprints": completed_count,
            "active_sprints": sum(
                project.status in {"Assigned", "In Progress", "Submitted", "Revision"}
                for project in projects_by_id.values()
            ),
            "completion_rate": (
                completed_count / assigned_count * 100 if assigned_count else None
            ),
            "on_time_rate": (
                on_time_sprints / len(measurable_sprints) * 100
                if measurable_sprints
                else None
            ),
            "average_rating": sum(ratings) / len(ratings) if ratings else None,
            "revision_count": revision_count,
            "early_sprints": early_sprints,
        }

    def get_completed_sprints_count(self) -> int:
        return self.get_performance_metrics()["completed_sprints"]

    def get_performance_rating(self) -> float | None:
        return self.get_performance_metrics()["average_rating"]

    def get_on_time_delivery_rate(self) -> float | None:
        return self.get_performance_metrics()["on_time_rate"]

    def get_badges(self, metrics: dict[str, Any] | None = None) -> list[dict[str, str]]:
        metrics = metrics or self.get_performance_metrics()
        badges = []
        rating = metrics["average_rating"]
        if rating is not None and rating >= 4.8 and metrics["revision_count"] == 0:
            badges.append({
                "name": "Top Rated ⭐",
                "color": "badge-emerald",
            })
        if metrics["early_sprints"] > 0:
            badges.append({
                "name": "Speed Demon ⚡",
                "color": "badge-indigo",
            })
        if metrics["completion_rate"] is not None and metrics["completion_rate"] == 100:
            badges.append({
                "name": "Reliable 🎯",
                "color": "badge-emerald",
            })
        if self.verification_status == "Verified":
            badges.append({
                "name": "Verified Pro 🛡️",
                "color": "badge-emerald",
            })
        return badges


# =========================================================
# PROJECT MODEL (WITH SKILL-MATCHING & WORKSPACE LINKING)
# =========================================================

class Project(db.Model):
    __tablename__ = "project"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    business_id = db.Column(
        db.Integer, db.ForeignKey("business.id"), nullable=True, index=True
    )
    business = db.relationship("Business", back_populates="projects")
    service = db.Column(db.String(100), nullable=False)
    package = db.Column(db.String(100), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="INR")
    requirements = db.Column(db.Text, nullable=False)
    
    # Skill-Matching Tags (JSON string or comma-separated)
    required_skills = db.Column(db.Text, nullable=True)

    # Status State Machine:
    # 'Pending Assignment' (Open for Bids) -> 'Assigned' -> 'In Progress' -> 'Submitted' -> 'Completed'
    status = db.Column(db.String(50), default="Pending Assignment", nullable=False)
    
    assigned_professional_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=True, index=True
    )
    
    revision_comment = db.Column(db.Text, nullable=True)
    payment_reminder_sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)

    milestones = db.relationship(
        "Milestone",
        backref="project",
        cascade="all, delete-orphan",
        lazy=True,
    )

    def get_required_skills_list(self) -> list[str]:
        """Return standardized list of clean lowercase project skills."""
        if not self.required_skills:
            return []
        try:
            parsed = json.loads(self.required_skills)
            if isinstance(parsed, list):
                return [str(s).strip().lower() for s in parsed if str(s).strip()]
        except Exception:
            pass
        tokens = re.split(r"[,|\n/]+", self.required_skills)
        return [t.strip().lower() for t in tokens if t.strip()]


# =========================================================
# PAYMENT MODEL
# =========================================================

class Payment(db.Model):
    __tablename__ = "payment"
    __table_args__ = (
        db.UniqueConstraint("razorpay_order_id", name="uq_payment_razorpay_order_id"),
        db.Index(
            "uq_payment_razorpay_payment_id_partial",
            "razorpay_payment_id",
            unique=True,
            postgresql_where=db.text("razorpay_payment_id IS NOT NULL"),
            sqlite_where=db.text("razorpay_payment_id IS NOT NULL"),
        ),
        db.Index(
            "uq_payment_stripe_session_id_partial",
            "stripe_session_id",
            unique=True,
            postgresql_where=db.text("stripe_session_id IS NOT NULL"),
            sqlite_where=db.text("stripe_session_id IS NOT NULL"),
        ),
        db.Index(
            "uq_payment_project_customer_created",
            "project_id",
            "customer_id",
            unique=True,
            postgresql_where=db.text("status = 'Created' AND addon_id IS NULL"),
            sqlite_where=db.text("status = 'Created' AND addon_id IS NULL"),
        ),
        db.Index(
            "uq_payment_addon_created",
            "addon_id",
            unique=True,
            postgresql_where=db.text("status = 'Created' AND addon_id IS NOT NULL"),
            sqlite_where=db.text("status = 'Created' AND addon_id IS NOT NULL"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey("project.id"), nullable=False, index=True
    )
    customer_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    addon_id = db.Column(
        db.Integer, db.ForeignKey("project_add_ons.id", ondelete="CASCADE"), nullable=True, index=True
    )
    razorpay_order_id = db.Column(db.String(100), nullable=True)
    razorpay_payment_id = db.Column(db.String(100), nullable=True)
    gateway_payment_id = db.Column(db.String(100), unique=True, nullable=True, index=True)
    stripe_session_id = db.Column(db.String(100), nullable=True, index=True)
    gateway_type = db.Column(db.String(20), default="razorpay", nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(10), default="INR", nullable=False)
    status = db.Column(db.String(30), default="Created", nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
    paid_at = db.Column(db.DateTime, nullable=True)

    project = db.relationship("Project", backref="payments")
    customer = db.relationship("User", backref="payments", foreign_keys=[customer_id])
    add_on = db.relationship("ProjectAddOn", backref="payments")


# =========================================================
# MILESTONE MODEL (ESCROW FUND FLOW)
# =========================================================

class Milestone(db.Model):
    __tablename__ = "milestones"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(30), default="pending", nullable=False)
    fund_flow_type = db.Column(db.String(50), default="escrow_milestone", nullable=False)
    due_date = db.Column(db.DateTime, nullable=True)
    released_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)


# =========================================================
# PROFESSIONAL PROJECT APPLICATION (MATCH & BID)
# =========================================================

class ProfessionalProjectApplication(db.Model):
    __tablename__ = "professional_project_application"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey("project.id"), nullable=False, index=True
    )
    professional_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    proposal = db.Column(db.Text, nullable=True)
    proposed_rate = db.Column(db.Numeric(12, 2), nullable=True)
    estimated_days = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(30), default="Applied", nullable=False) # Applied, Assigned, Rejected
    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)

    project = db.relationship("Project", backref="applications")
    professional = db.relationship("User", backref="project_applications", foreign_keys=[professional_id])


# =========================================================
# PROJECT DELIVERY MODEL
# =========================================================

class ProjectDelivery(db.Model):
    __tablename__ = "project_delivery"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey("project.id"), nullable=False, index=True
    )
    professional_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    submission_number = db.Column(db.Integer, nullable=False, default=1)
    live_url = db.Column(db.String(500), nullable=True)
    source_url = db.Column(db.String(500), nullable=True)
    preview_url = db.Column(db.String(500), nullable=True)
    completion_notes = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(30), nullable=False, default="Submitted")
    review_comment = db.Column(db.Text, nullable=True)
    submitted_at = db.Column(db.DateTime, nullable=False, default=get_ist_now)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    review_reminder_sent_at = db.Column(db.DateTime, nullable=True)

    project = db.relationship(
        "Project",
        backref=db.backref(
            "deliveries",
            lazy=True,
            order_by="ProjectDelivery.submission_number",
        ),
    )
    professional = db.relationship("User", backref="project_deliveries", foreign_keys=[professional_id])


# =========================================================
# CHAT & MESSAGING
# =========================================================

class ChatMessage(db.Model):
    __tablename__ = "chat_message"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    attachment_url = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    project = db.relationship(
        "Project",
        backref=db.backref("chat_messages", lazy=True, order_by="ChatMessage.created_at.asc()"),
    )
    sender = db.relationship("User", backref="sent_messages")


# =========================================================
# NOTIFICATIONS ENGINE
# =========================================================

class Notification(db.Model):
    __tablename__ = "notification"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    link = db.Column(db.String(500), nullable=True)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    user = db.relationship(
        "User",
        backref=db.backref("notifications", lazy=True, order_by="Notification.created_at.desc()"),
        foreign_keys=[user_id],
    )


# =========================================================
# TWO-STAGE WITHDRAWAL & WALLET REDEMPTION MODEL
# =========================================================

class WithdrawalRequest(db.Model):
    __tablename__ = "withdrawal_requests"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(10), default="INR", nullable=False)
    
    payout_mode = db.Column(db.String(30), default="UPI", nullable=False) # 'UPI' or 'BANK_TRANSFER'
    upi_id = db.Column(db.String(100), nullable=True)
    bank_account_number = db.Column(db.String(50), nullable=True)
    bank_ifsc = db.Column(db.String(20), nullable=True)
    bank_beneficiary_name = db.Column(db.String(120), nullable=True)

    status = db.Column(db.String(30), default="Pending", nullable=False) # Pending, Approved, Processed, Rejected
    utr_reference = db.Column(db.String(100), nullable=True)
    admin_notes = db.Column(db.Text, nullable=True)

    requested_at = db.Column(db.DateTime, default=get_ist_now, nullable=False)
    processed_at = db.Column(db.DateTime, nullable=True)

    specialist = db.relationship("User", back_populates="withdrawal_requests")


class WalletTransaction(db.Model):
    """Audit ledger for all wallet balance shifts (escrow lock, unlock, withdrawal)."""
    __tablename__ = "wallet_transactions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(10), default="INR", nullable=False)
    
    # Types: ESCROW_LOCKED, ESCROW_RELEASED, WITHDRAWAL_DEBIT, WITHDRAWAL_REVERSED
    transaction_type = db.Column(db.String(40), nullable=False)
    reference_type = db.Column(db.String(40), nullable=True) # PROJECT, WITHDRAWAL
    reference_id = db.Column(db.Integer, nullable=True)
    description = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    user = db.relationship("User", back_populates="wallet_transactions")


# =========================================================
# INVOICE & PROFIT MARGIN TRACKING
# =========================================================

class Invoice(db.Model):
    __tablename__ = "invoice"

    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(100), unique=True, nullable=False)
    razorpay_order_id = db.Column(db.String(100), unique=True, nullable=True, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    gross_amount = db.Column(db.Numeric(12, 2), nullable=False)
    talent_payout = db.Column(db.Numeric(12, 2), nullable=False)
    agency_profit = db.Column(db.Numeric(12, 2), nullable=False)
    tax_amount = db.Column(db.Numeric(12, 2), default=0, nullable=False)
    currency = db.Column(db.String(3), default="INR", nullable=False)
    status = db.Column(db.String(30), default="Paid", nullable=False)

    payout_status = db.Column(db.String(30), default="Pending Approval", nullable=False)
    payout_utr = db.Column(db.String(100), nullable=True)
    payout_paid_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    project = db.relationship(
        "Project",
        backref=db.backref("invoice_records", lazy=True, order_by="Invoice.id.desc()"),
    )
    customer = db.relationship("User", backref="invoices", foreign_keys=[customer_id])


class AgencySetting(db.Model):
    __tablename__ = "agency_settings"

    id = db.Column(db.Integer, primary_key=True, default=1)
    agency_name = db.Column(db.String(120), nullable=False, default="AgencyOS")
    primary_color = db.Column(db.String(7), nullable=False, default="#09090b")
    logo_url = db.Column(db.String(500), nullable=True)
    whatsapp_support_number = db.Column(db.String(16), nullable=False, default="+919876543210")
    updated_at = db.Column(db.DateTime, nullable=False, default=get_ist_now, onupdate=get_ist_now)


class RazorpaySubscriptionPlan(db.Model):
    __tablename__ = "razorpay_subscription_plan"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), nullable=False, unique=True)
    razorpay_plan_id = db.Column(db.String(100), nullable=True, unique=True)
    name = db.Column(db.String(150), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(10), nullable=False, default="INR")
    created_at = db.Column(db.DateTime, nullable=False, default=get_ist_now)


class RazorpaySubscription(db.Model):
    __tablename__ = "razorpay_subscription"
    __table_args__ = (
        db.Index(
            "uq_razorpay_subscription_project_open",
            "project_id",
            unique=True,
            postgresql_where=db.text(
                "status IN ('initializing', 'created', 'authenticated', 'active', 'pending', 'halted', 'paused', 'resumed')"
            ),
            sqlite_where=db.text(
                "status IN ('initializing', 'created', 'authenticated', 'active', 'pending', 'halted', 'paused', 'resumed')"
            ),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id = db.Column(
        db.Integer, db.ForeignKey("razorpay_subscription_plan.id"), nullable=False
    )
    razorpay_subscription_id = db.Column(db.String(100), nullable=True, unique=True)
    amount = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(10), nullable=False, default="INR")
    status = db.Column(db.String(30), nullable=False, default="created")
    start_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=get_ist_now)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=get_ist_now, onupdate=get_ist_now
    )

    project = db.relationship("Project", backref=db.backref("retainer_subscriptions", lazy=True))
    customer = db.relationship("User", backref=db.backref("retainer_subscriptions", lazy=True), foreign_keys=[customer_id])
    plan = db.relationship("RazorpaySubscriptionPlan")
    charges = db.relationship("SubscriptionCharge", back_populates="subscription", lazy=True)


class SubscriptionCharge(db.Model):
    __tablename__ = "subscription_charge"

    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(
        db.Integer,
        db.ForeignKey("razorpay_subscription.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payment_id = db.Column(
        db.Integer,
        db.ForeignKey("payment.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    invoice_id = db.Column(
        db.Integer,
        db.ForeignKey("invoice.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    razorpay_payment_id = db.Column(db.String(100), nullable=False, unique=True)
    event_id = db.Column(db.String(100), nullable=True, unique=True)
    amount = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(10), nullable=False, default="INR")
    charged_at = db.Column(db.DateTime, nullable=False, default=get_ist_now)

    subscription = db.relationship("RazorpaySubscription", back_populates="charges")
    payment = db.relationship("Payment")
    invoice = db.relationship("Invoice")


class Review(db.Model):
    __tablename__ = "reviews"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey(Project.id, ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id = db.Column(
        db.Integer, db.ForeignKey(User.id, ondelete="CASCADE"), nullable=False, index=True
    )
    rating = db.Column(db.Integer, default=5, nullable=False)
    tags = db.Column(db.String(255), nullable=True)
    feedback = db.Column(db.Text, nullable=False)
    is_public = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    project = db.relationship("Project", backref=db.backref("review", uselist=False), foreign_keys=[project_id])
    customer = db.relationship("User", backref="reviews", foreign_keys=[customer_id])


class ProjectAddOn(db.Model):
    __tablename__ = "project_add_ons"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_id = db.Column(
        db.Integer, db.ForeignKey("invoice.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    title = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    status = db.Column(db.String(30), default="Pending Payment", nullable=False)
    created_at = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    project = db.relationship("Project", backref=db.backref("add_ons", cascade="all, delete-orphan", lazy=True))
    invoice = db.relationship("Invoice", backref=db.backref("project_add_on", uselist=False))


class ActivityLog(db.Model):
    __tablename__ = "activity_logs"
    __table_args__ = (
        db.Index("ix_activity_logs_project_created_at", "project_id", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(30), default="info", nullable=False)
    created_at = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    project = db.relationship("Project", backref=db.backref("activity_logs", cascade="all, delete-orphan", lazy=True))
    actor = db.relationship("User", backref="activity_logs", foreign_keys=[user_id])

    @property
    def event_type(self) -> str:
        """Backwards compatibility alias for action."""
        return self.action

    @classmethod
    def log_event(
        cls,
        project_id: int | None = None,
        action: str | None = None,
        description: str | None = None,
        user_id: int | None = None,
        category: str = "info",
        **kwargs,
    ) -> "ActivityLog":
        """Factory helper method for logging activity events across blueprints."""
        log = cls()
        log.project_id = project_id
        log.user_id = user_id
        log.action = action or "ACTIVITY"
        log.description = description or ""
        log.category = category or "info"
        db.session.add(log)
        return log


class StripeWebhookEvent(db.Model):
    __tablename__ = "stripe_webhook_event"

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(
        db.String(100), nullable=False, unique=True, index=True
    )
    event_type = db.Column(db.String(100), nullable=False)
    project_id = db.Column(
        db.Integer, db.ForeignKey("project.id", ondelete="CASCADE"), nullable=True, index=True
    )
    processed_at = db.Column(
        db.DateTime, nullable=False, default=get_ist_now
    )
    payload = db.Column(db.Text, nullable=True)

    project = db.relationship("Project", backref="stripe_webhook_events")

    @classmethod
    def is_processed(cls, event_id: str) -> bool:
        return db.session.query(cls).filter_by(event_id=event_id).first() is not None

    @classmethod
    def mark_processed(
        cls,
        event_id: str,
        event_type: str,
        project_id: int | None = None,
        payload: str | None = None,
    ) -> "StripeWebhookEvent":
        event = cls()
        event.event_id = event_id
        event.event_type = event_type
        event.project_id = project_id
        event.payload = payload
        db.session.add(event)
        return event


# =========================================================
# PROFILE & EXPERTISE HELPERS
# =========================================================

def calculate_professional_profile_completion(profile: ProfessionalProfile | None) -> int:
    if not profile:
        return 0
    fields = [
        profile.expertise, profile.experience, profile.about, profile.phone,
        profile.location, profile.skills, profile.technologies, profile.skill_level,
        profile.project_types, profile.portfolio, profile.github, profile.linkedin,
        profile.education, profile.certifications, profile.work_experience,
        profile.availability, profile.current_workload
    ]
    completed = sum(1 for f in fields if f and str(f).strip())
    total = len(fields)
    return round((completed / total) * 100) if total else 0


def calculate_customer_profile_completion(profile: CustomerProfile | None) -> int:
    if not profile:
        return 0
    fields = [
        profile.phone, profile.location, profile.business_name, profile.business_type,
        profile.website, profile.social_links, profile.business_description,
        profile.target_audience, profile.business_goals, profile.preferred_budget
    ]
    completed = sum(1 for f in fields if f and str(f).strip())
    total = len(fields)
    return round((completed / total) * 100) if total else 0


def get_latest_project_delivery(project_id: int) -> ProjectDelivery | None:
    return (
        ProjectDelivery.query.filter_by(project_id=project_id)
        .order_by(ProjectDelivery.submission_number.desc())
        .first()
    )