"""AgencyOS Database Models & Repository Definitions.

Defines SQLAlchemy ORM models for users, multi-tenant businesses, professional profiles,
customer profiles with direct contact & GST context, gated projects, cryptographic payment ledgers,
invoice margin splits, two-stage escrow wallets, withdrawal requests with statutory Section 194-O TDS,
activity audit trails, contract digital signatures, retainer hours bank, micro-tasks, and billable time logs.
"""

from datetime import datetime
from decimal import Decimal
import json
import re
import secrets
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db

IST = ZoneInfo("Asia/Kolkata")


def get_ist_now() -> datetime:
    return datetime.now(IST)


def is_valid_http_url(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = urlparse(value.strip())
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


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


class User(db.Model, UserMixin):
    __tablename__ = "user"
    __table_args__ = (
        db.UniqueConstraint("email", "role", name="uq_user_email_role"),
    )

    id: Any = db.Column(db.Integer, primary_key=True)
    name: Any = db.Column(db.String(100), nullable=False)
    full_name: Any = db.Column(db.String(120), nullable=True)
    email: Any = db.Column(db.String(120), nullable=False, index=True)
    password: Any = db.Column(db.String(255), nullable=False)
    role: Any = db.Column(db.String(30), default="customer", nullable=False)
    is_active: Any = db.Column(db.Boolean, default=True, nullable=False)
    phone: Any = db.Column(db.String(20), nullable=True)

    is_verified: Any = db.Column(db.Boolean, default=False, nullable=False)
    verification_status: Any = db.Column(db.String(30), default="Verified", nullable=False)
    verified_at: Any = db.Column(db.DateTime, nullable=True)
    rejection_reason: Any = db.Column(db.Text, nullable=True)

    created_at: Any = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
    email_verified_at: Any = db.Column(db.DateTime, nullable=True)
    session_version: Any = db.Column(db.Integer, nullable=False, default=0)

    def __init__(self, name: str = "", email: str = "", password: str = "", role: str = "customer", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if name:
            self.name = name
            self.full_name = name
        if email:
            self.email = email
        if password:
            if password.startswith(("scrypt:", "pbkdf2:", "argon2:")):
                self.password = password
            else:
                self.set_password(password)
        if role:
            self.role = role

    def set_password(self, password: str) -> None:
        self.password = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password, password)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "full_name": self.full_name,
            "email": self.email,
            "role": self.role,
            "is_active": self.is_active,
            "is_verified": self.is_verified,
            "verification_status": self.verification_status,
            "phone": self.phone,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    projects = db.relationship("Project", backref="customer", foreign_keys="Project.customer_id", lazy=True)
    assigned_projects = db.relationship("Project", backref="assigned_professional", foreign_keys="Project.assigned_professional_id", lazy=True)
    professional_profile = db.relationship("ProfessionalProfile", backref="user", uselist=False, cascade="all, delete-orphan")
    customer_profile = db.relationship("CustomerProfile", backref="user", uselist=False, cascade="all, delete-orphan")
    businesses = db.relationship("Business", back_populates="customer", cascade="all, delete-orphan", lazy=True)
    withdrawal_requests = db.relationship("WithdrawalRequest", back_populates="specialist", lazy=True, cascade="all, delete-orphan")
    wallet_transactions = db.relationship("WalletTransaction", back_populates="user", lazy=True, cascade="all, delete-orphan")


class CustomerProfile(db.Model):
    __tablename__ = "customer_profile"

    id: Any = db.Column(db.Integer, primary_key=True)
    user_id: Any = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False, index=True)
    phone: Any = db.Column(db.String(30), nullable=True)
    secondary_phone: Any = db.Column(db.String(30), nullable=True)
    preferred_channel: Any = db.Column(db.String(50), nullable=True, default="WhatsApp")
    available_hours: Any = db.Column(db.String(100), nullable=True)
    billing_email: Any = db.Column(db.String(120), nullable=True)
    business_name: Any = db.Column(db.String(150), nullable=True)
    business_type: Any = db.Column(db.String(100), nullable=True)
    website: Any = db.Column(db.String(300), nullable=True)
    gstin: Any = db.Column(db.String(20), nullable=True)
    billing_address: Any = db.Column(db.Text, nullable=True)
    pincode: Any = db.Column(db.String(15), nullable=True)
    location: Any = db.Column(db.String(150), nullable=True)
    signatory_name: Any = db.Column(db.String(120), nullable=True)
    signatory_title: Any = db.Column(db.String(100), nullable=True)
    social_links: Any = db.Column(db.Text, nullable=True)
    business_description: Any = db.Column(db.Text, nullable=True)
    target_audience: Any = db.Column(db.Text, nullable=True)
    business_goals: Any = db.Column(db.Text, nullable=True)
    preferred_budget: Any = db.Column(db.String(100), nullable=True)
    profile_completed: Any = db.Column(db.Boolean, default=False, nullable=False)
    created_at: Any = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
    updated_at: Any = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now(), nullable=False)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)


class Business(db.Model):
    __tablename__ = "business"

    id: Any = db.Column(db.Integer, primary_key=True)
    customer_id: Any = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name: Any = db.Column(db.String(200), nullable=False)
    business_type: Any = db.Column(db.String(150), nullable=True)
    location: Any = db.Column(db.String(250), nullable=True)
    website: Any = db.Column(db.String(500), nullable=True)
    social_links: Any = db.Column(db.Text, nullable=True)
    description: Any = db.Column(db.Text, nullable=True)
    target_audience: Any = db.Column(db.Text, nullable=True)
    goals: Any = db.Column(db.Text, nullable=True)
    preferred_budget: Any = db.Column(db.String(100), nullable=True)
    created_at: Any = db.Column(db.DateTime, default=get_ist_now, nullable=False)
    updated_at: Any = db.Column(db.DateTime, default=get_ist_now, onupdate=get_ist_now, nullable=False)

    customer = db.relationship("User", back_populates="businesses")
    projects = db.relationship("Project", back_populates="business", lazy=True)

    def __init__(self, customer_id: int | None = None, name: str = "", business_type: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if customer_id is not None:
            self.customer_id = customer_id
        if name:
            self.name = name
        if business_type:
            self.business_type = business_type


class UserToken(db.Model):
    __tablename__ = "user_token"

    id: Any = db.Column(db.Integer, primary_key=True)
    user_id: Any = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    purpose: Any = db.Column(db.String(40), nullable=False, index=True)
    token_hash: Any = db.Column(db.String(64), nullable=False, unique=True, index=True)
    expires_at: Any = db.Column(db.DateTime, nullable=False, index=True)
    used_at: Any = db.Column(db.DateTime, nullable=True)
    created_at: Any = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)

    __table_args__ = (db.Index("ix_user_token_user_purpose", "user_id", "purpose"),)

    user = db.relationship("User", backref=db.backref("tokens", lazy=True, cascade="all, delete-orphan"))

    def __init__(self, user_id: int | None = None, purpose: str = "", token_hash: str = "", expires_at: datetime | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if user_id is not None:
            self.user_id = user_id
        if purpose:
            self.purpose = purpose
        if token_hash:
            self.token_hash = token_hash
        if expires_at is not None:
            self.expires_at = expires_at


class ProfessionalProfile(db.Model):
    __tablename__ = "professional_profile"

    id: Any = db.Column(db.Integer, primary_key=True)
    user_id: Any = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False, index=True)
    expertise: Any = db.Column(db.String(100), nullable=False, default="")
    experience: Any = db.Column(db.String(50), nullable=False, default="")
    about: Any = db.Column(db.Text, nullable=True)
    phone: Any = db.Column(db.String(30), nullable=True)
    location: Any = db.Column(db.String(150), nullable=True)
    skills: Any = db.Column(db.Text, nullable=True)
    technologies: Any = db.Column(db.Text, nullable=True)
    skill_level: Any = db.Column(db.String(50), nullable=True)
    project_types: Any = db.Column(db.Text, nullable=True)
    portfolio: Any = db.Column(db.String(500), nullable=True)
    github: Any = db.Column(db.String(300), nullable=True)
    linkedin: Any = db.Column(db.String(300), nullable=True)
    education: Any = db.Column(db.Text, nullable=True)
    certifications: Any = db.Column(db.Text, nullable=True)
    work_experience: Any = db.Column(db.Text, nullable=True)
    availability: Any = db.Column(db.String(50), nullable=True)
    current_workload: Any = db.Column(db.String(100), nullable=True)

    pending_balance: Any = db.Column(db.Numeric(12, 2), default=0.00, nullable=False)
    available_balance: Any = db.Column(db.Numeric(12, 2), default=0.00, nullable=False)

    upi_id: Any = db.Column(db.String(100), nullable=True)
    bank_account_number: Any = db.Column(db.String(50), nullable=True)
    bank_ifsc: Any = db.Column(db.String(20), nullable=True)
    bank_beneficiary_name: Any = db.Column(db.String(120), nullable=True)
    pan_number: Any = db.Column(db.String(20), nullable=True)

    verification_status: Any = db.Column(db.String(30), default="Pending", nullable=False)
    verification_notes: Any = db.Column(db.Text, nullable=True)
    profile_completed: Any = db.Column(db.Boolean, default=False, nullable=False)
    created_at: Any = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
    updated_at: Any = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now(), nullable=False)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    def get_skills_list(self) -> list[str]:
        if not self.skills:
            return []
        try:
            parsed = json.loads(self.skills)
            if isinstance(parsed, list):
                return [str(s).strip().lower() for s in parsed if str(s).strip()]
        except Exception:
            pass
        tokens = re.split(r"[,|\n/]+", self.skills)
        return [t.strip().lower() for t in tokens if t.strip()]

    def get_performance_metrics(self) -> dict[str, Any]:
        assigned_projects = (
            db.session.query(Project)
            .join(ProfessionalProjectApplication, ProfessionalProjectApplication.project_id == Project.id)
            .filter(
                ProfessionalProjectApplication.professional_id == self.user_id,
                ProfessionalProjectApplication.status == "Assigned",
            )
            .all()
        )
        projects_by_id = {project.id: project for project in assigned_projects}
        project_ids = set(projects_by_id)
        completed_ids = {project.id for project in assigned_projects if project.status == "Completed"}

        deliveries = (
            ProjectDelivery.query.filter(
                ProjectDelivery.professional_id == self.user_id,
                ProjectDelivery.project_id.in_(project_ids),
            )
            .order_by(ProjectDelivery.project_id, ProjectDelivery.submission_number, ProjectDelivery.id)
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

        reviews = Review.query.filter(Review.project_id.in_(completed_ids)).all() if completed_ids else []
        ratings = [r.rating for r in reviews if r.rating is not None and 1 <= r.rating <= 5]

        deadlines = {}
        if completed_ids:
            milestones = Milestone.query.filter(
                Milestone.project_id.in_(completed_ids),
                Milestone.due_date.is_not(None),
            ).all()
            for m in milestones:
                current_deadline = deadlines.get(m.project_id)
                if current_deadline is None or m.due_date > current_deadline:
                    deadlines[m.project_id] = m.due_date

        measurable_sprints = [pid for pid in completed_ids if pid in deadlines and latest_deliveries.get(pid) is not None]
        on_time_sprints = sum(latest_deliveries[pid].submitted_at <= deadlines[pid] for pid in measurable_sprints)
        early_sprints = sum(latest_deliveries[pid].submitted_at < deadlines[pid] for pid in measurable_sprints)
        completed_count = len(completed_ids)
        assigned_count = len(project_ids)

        return {
            "completed_sprints": completed_count,
            "active_sprints": sum(p.status in {"Assigned", "In Progress", "Submitted", "Revision"} for p in projects_by_id.values()),
            "completion_rate": (completed_count / assigned_count * 100) if assigned_count else None,
            "on_time_rate": (on_time_sprints / len(measurable_sprints) * 100) if measurable_sprints else None,
            "average_rating": (sum(ratings) / len(ratings)) if ratings else None,
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
            badges.append({"name": "Top Rated 🌟", "color": "badge-emerald"})
        if metrics["early_sprints"] > 0:
            badges.append({"name": "Speed Demon ⚡", "color": "badge-indigo"})
        if metrics["completion_rate"] is not None and metrics["completion_rate"] == 100:
            badges.append({"name": "Reliable 🎯", "color": "badge-emerald"})
        if self.verification_status == "Verified":
            badges.append({"name": "Verified Pro 🛡️", "color": "badge-emerald"})
        return badges


class Project(db.Model):
    __tablename__ = "project"

    id: Any = db.Column(db.Integer, primary_key=True)
    customer_id: Any = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    business_id: Any = db.Column(db.Integer, db.ForeignKey("business.id"), nullable=True, index=True)
    business = db.relationship("Business", back_populates="projects")
    service: Any = db.Column(db.String(100), nullable=False, default="Website Design & Development")
    package: Any = db.Column(db.String(100), nullable=False, default="Basic Website")
    currency: Any = db.Column(db.String(3), nullable=False, default="INR")
    requirements: Any = db.Column(db.Text, nullable=False, default="Standard milestone project deliverables.")
    required_skills: Any = db.Column(db.Text, nullable=True)
    status: Any = db.Column(db.String(50), default="Pending Assignment", nullable=False)
    assigned_professional_id: Any = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    revision_comment: Any = db.Column(db.Text, nullable=True)
    payment_reminder_sent_at: Any = db.Column(db.DateTime, nullable=True)

    # Formal Statement of Work (SOW) compiled PDF document path
    sow_pdf_path: Any = db.Column(db.String(500), nullable=True)

    # Monthly Retainer Hours Bank
    hours_allocated: Any = db.Column(db.Integer, default=0, nullable=False, server_default="0")
    hours_remaining: Any = db.Column(db.Numeric(5, 2), default=0.00, nullable=False, server_default="0.00")

    created_at: Any = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)

    milestones = db.relationship("Milestone", backref="project", cascade="all, delete-orphan", lazy=True)
    contract_signature = db.relationship("ContractSignature", backref="project", uselist=False, cascade="all, delete-orphan")
    time_logs = db.relationship("ProjectTimeLog", backref="project", cascade="all, delete-orphan", lazy=True)
    retainer_tasks = db.relationship("RetainerTask", backref="project", cascade="all, delete-orphan", lazy=True, order_by="RetainerTask.created_at.desc()")

    def __init__(self, customer_id: int | None = None, service: str = "", package: str = "", requirements: str = "", status: str = "Pending Assignment", sow_pdf_path: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if customer_id is not None:
            self.customer_id = customer_id
        self.service = service if service else "Website Design & Development"
        self.package = package if package else "Basic Website"
        self.requirements = requirements if requirements else "Standard milestone project deliverables."
        if status:
            self.status = status
        if sow_pdf_path is not None:
            self.sow_pdf_path = sow_pdf_path
        if self.package == "Starter Retainer":
            self.hours_allocated = 5
            self.hours_remaining = Decimal("5.00")

    def get_required_skills_list(self) -> list[str]:
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "business_id": self.business_id,
            "service": self.service,
            "package": self.package,
            "currency": self.currency,
            "requirements": self.requirements,
            "required_skills": self.get_required_skills_list(),
            "status": self.status,
            "assigned_professional_id": self.assigned_professional_id,
            "hours_allocated": self.hours_allocated,
            "hours_remaining": float(self.hours_remaining) if self.hours_remaining is not None else 0.0,
            "sow_pdf_path": self.sow_pdf_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class RetainerTask(db.Model):
    """Client maintenance and tweak tasks under active retainer subscriptions."""
    __tablename__ = "retainer_tasks"

    id: Any = db.Column(db.Integer, primary_key=True)
    project_id: Any = db.Column(db.Integer, db.ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id: Any = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    specialist_id: Any = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Any = db.Column(db.String(200), nullable=False)
    description: Any = db.Column(db.Text, nullable=False)
    asset_url: Any = db.Column(db.String(500), nullable=True)
    status: Any = db.Column(db.String(30), default="Queued", nullable=False)  # Queued, In Progress, Completed, Rejected
    hours_spent: Any = db.Column(db.Numeric(5, 2), default=0.00, nullable=False, server_default="0.00")
    completion_notes: Any = db.Column(db.Text, nullable=True)
    created_at: Any = db.Column(db.DateTime, default=get_ist_now, nullable=False)
    completed_at: Any = db.Column(db.DateTime, nullable=True)

    customer = db.relationship("User", foreign_keys=[customer_id], backref="customer_retainer_tasks")
    specialist = db.relationship("User", foreign_keys=[specialist_id], backref="assigned_retainer_tasks")

    def __init__(self, project_id: int | None = None, customer_id: int | None = None, title: str = "", description: str = "", specialist_id: int | None = None, status: str = "Queued", asset_url: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if project_id is not None:
            self.project_id = project_id
        if customer_id is not None:
            self.customer_id = customer_id
        self.title = title if title else "Maintenance Task"
        self.description = description if description else "Maintenance task instructions"
        if specialist_id is not None:
            self.specialist_id = specialist_id
        self.status = status if status else "Queued"
        if asset_url is not None:
            self.asset_url = asset_url


class ContractSignature(db.Model):
    __tablename__ = "contract_signatures"

    id: Any = db.Column(db.Integer, primary_key=True)
    project_id: Any = db.Column(db.Integer, db.ForeignKey("project.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    signer_name: Any = db.Column(db.String(120), nullable=False)
    signer_title: Any = db.Column(db.String(100), nullable=False)
    signature_hash: Any = db.Column(db.String(64), nullable=False)
    signature_data: Any = db.Column(db.Text, nullable=True)
    ip_address: Any = db.Column(db.String(45), nullable=False, default="127.0.0.1")
    signed_at: Any = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    def __init__(self, project_id: int | None = None, signer_name: str = "", signer_title: str = "", signature_hash: str = "", signature_data: str | None = None, ip_address: str = "127.0.0.1", signed_at: datetime | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if project_id is not None:
            self.project_id = project_id
        if signer_name:
            self.signer_name = signer_name
        if signer_title:
            self.signer_title = signer_title
        if signature_hash:
            self.signature_hash = signature_hash
        if signature_data is not None:
            self.signature_data = signature_data
        if ip_address:
            self.ip_address = ip_address
        if signed_at is not None:
            self.signed_at = signed_at


class ProjectTimeLog(db.Model):
    __tablename__ = "project_time_logs"

    id: Any = db.Column(db.Integer, primary_key=True)
    project_id: Any = db.Column(db.Integer, db.ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True)
    milestone_id: Any = db.Column(db.Integer, db.ForeignKey("milestones.id", ondelete="SET NULL"), nullable=True, index=True)
    professional_id: Any = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    hours: Any = db.Column(db.Numeric(6, 2), nullable=False)
    hourly_rate: Any = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)
    description: Any = db.Column(db.Text, nullable=False)
    logged_date: Any = db.Column(db.DateTime, default=get_ist_now, nullable=False)
    is_billed: Any = db.Column(db.Boolean, default=False, nullable=False)
    invoice_id: Any = db.Column(db.Integer, db.ForeignKey("invoice.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Any = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    milestone = db.relationship("Milestone", backref=db.backref("time_logs", lazy=True))
    professional = db.relationship("User", foreign_keys=[professional_id])
    invoice = db.relationship("Invoice", backref=db.backref("time_logs", lazy=True))

    def __init__(self, project_id: int | None = None, milestone_id: int | None = None, professional_id: int | None = None, hours: Any = 0, hourly_rate: Any = 0, description: str = "", logged_date: datetime | None = None, is_billed: bool = False, invoice_id: int | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if project_id is not None:
            self.project_id = project_id
        if milestone_id is not None:
            self.milestone_id = milestone_id
        if professional_id is not None:
            self.professional_id = professional_id
        if hours is not None:
            self.hours = hours
        if hourly_rate is not None:
            self.hourly_rate = hourly_rate
        if description:
            self.description = description
        if logged_date is not None:
            self.logged_date = logged_date
        self.is_billed = is_billed
        if invoice_id is not None:
            self.invoice_id = invoice_id


class Payment(db.Model):
    __tablename__ = "payment"
    __table_args__ = (
        db.UniqueConstraint("razorpay_order_id", name="uq_payment_razorpay_order_id"),
        db.Index(
            "uq_payment_project_customer_created",
            "project_id",
            "customer_id",
            unique=True,
            postgresql_where=db.text("status = 'Created'"),
            sqlite_where=db.text("status = 'Created'"),
        ),
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
    )

    id: Any = db.Column(db.Integer, primary_key=True)
    project_id: Any = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False, index=True)
    customer_id: Any = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    addon_id: Any = db.Column(db.Integer, db.ForeignKey("project_add_ons.id", ondelete="CASCADE"), nullable=True, index=True)
    razorpay_order_id: Any = db.Column(db.String(100), nullable=True)
    razorpay_payment_id: Any = db.Column(db.String(100), nullable=True)
    gateway_payment_id: Any = db.Column(db.String(100), unique=True, nullable=True, index=True)
    stripe_session_id: Any = db.Column(db.String(100), nullable=True, index=True)
    gateway_type: Any = db.Column(db.String(20), default="razorpay", nullable=False)
    amount: Any = db.Column(db.Numeric(12, 2), nullable=False)
    currency: Any = db.Column(db.String(10), default="INR", nullable=False)
    status: Any = db.Column(db.String(30), default="Created", nullable=False)
    created_at: Any = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
    paid_at: Any = db.Column(db.DateTime, nullable=True)

    project = db.relationship("Project", backref="payments")
    customer = db.relationship("User", backref="payments", foreign_keys=[customer_id])
    add_on = db.relationship("ProjectAddOn", backref="payments")

    def __init__(self, project_id: int | None = None, customer_id: int | None = None, razorpay_order_id: str | None = None, stripe_session_id: str | None = None, gateway_type: str = "razorpay", addon_id: int | None = None, amount: Any = 0, currency: str = "INR", status: str = "Created", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if project_id is not None:
            self.project_id = project_id
        if customer_id is not None:
            self.customer_id = customer_id
        if razorpay_order_id is not None:
            self.razorpay_order_id = razorpay_order_id
        if stripe_session_id is not None:
            self.stripe_session_id = stripe_session_id
        if gateway_type:
            self.gateway_type = gateway_type
        if addon_id is not None:
            self.addon_id = addon_id
        if amount is not None:
            self.amount = amount
        if currency:
            self.currency = currency
        if status:
            self.status = status


class Milestone(db.Model):
    __tablename__ = "milestones"

    id: Any = db.Column(db.Integer, primary_key=True)
    project_id: Any = db.Column(db.Integer, db.ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Any = db.Column(db.String(200), nullable=False)
    amount: Any = db.Column(db.Integer, nullable=False)
    status: Any = db.Column(db.String(30), default="pending", nullable=False)
    fund_flow_type: Any = db.Column(db.String(50), default="escrow_milestone", nullable=False)
    due_date: Any = db.Column(db.DateTime, nullable=True)
    released_at: Any = db.Column(db.DateTime, nullable=True)
    created_at: Any = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)

    def __init__(self, project_id: int | None = None, title: str = "", amount: int = 0, due_date: datetime | None = None, status: str = "pending", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if project_id is not None:
            self.project_id = project_id
        if title:
            self.title = title
        if amount:
            self.amount = amount
        if due_date is not None:
            self.due_date = due_date
        if status:
            self.status = status

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "amount": self.amount,
            "status": self.status,
            "fund_flow_type": self.fund_flow_type,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "released_at": self.released_at.isoformat() if self.released_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ProfessionalProjectApplication(db.Model):
    __tablename__ = "professional_project_application"

    id: Any = db.Column(db.Integer, primary_key=True)
    project_id: Any = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False, index=True)
    professional_id: Any = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    proposal: Any = db.Column(db.Text, nullable=True)
    proposed_rate: Any = db.Column(db.Numeric(12, 2), nullable=True)
    estimated_days: Any = db.Column(db.Integer, nullable=True)
    status: Any = db.Column(db.String(30), default="Applied", nullable=False)
    created_at: Any = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)

    project = db.relationship("Project", backref="applications")
    professional = db.relationship("User", backref="project_applications", foreign_keys=[professional_id])

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)


class ProjectDelivery(db.Model):
    __tablename__ = "project_delivery"

    id: Any = db.Column(db.Integer, primary_key=True)
    project_id: Any = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False, index=True)
    professional_id: Any = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    submission_number: Any = db.Column(db.Integer, nullable=False, default=1)
    live_url: Any = db.Column(db.String(500), nullable=True)
    source_url: Any = db.Column(db.String(500), nullable=True)
    preview_url: Any = db.Column(db.String(500), nullable=True)
    completion_notes: Any = db.Column(db.Text, nullable=False)
    status: Any = db.Column(db.String(30), nullable=False, default="Submitted")
    review_comment: Any = db.Column(db.Text, nullable=True)
    submitted_at: Any = db.Column(db.DateTime, nullable=False, default=get_ist_now)
    reviewed_at: Any = db.Column(db.DateTime, nullable=True)
    review_reminder_sent_at: Any = db.Column(db.DateTime, nullable=True)

    project = db.relationship("Project", backref=db.backref("deliveries", lazy=True, order_by="ProjectDelivery.submission_number"))
    professional = db.relationship("User", backref="project_deliveries", foreign_keys=[professional_id])

    def __init__(self, project_id: int | None = None, professional_id: int | None = None, submission_number: int = 1, completion_notes: str = "", status: str = "Submitted", submitted_at: datetime | None = None, reviewed_at: datetime | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if project_id is not None:
            self.project_id = project_id
        if professional_id is not None:
            self.professional_id = professional_id
        if submission_number:
            self.submission_number = submission_number
        if completion_notes:
            self.completion_notes = completion_notes
        if status:
            self.status = status
        if submitted_at is not None:
            self.submitted_at = submitted_at
        if reviewed_at is not None:
            self.reviewed_at = reviewed_at


class ChatMessage(db.Model):
    __tablename__ = "chat_message"

    id: Any = db.Column(db.Integer, primary_key=True)
    project_id: Any = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False, index=True)
    sender_id: Any = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    message: Any = db.Column(db.Text, nullable=False)
    attachment_url: Any = db.Column(db.String(500), nullable=True)
    created_at: Any = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    project = db.relationship("Project", backref=db.backref("chat_messages", lazy=True, order_by="ChatMessage.created_at.asc()"))
    sender = db.relationship("User", backref="sent_messages")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)


class WithdrawalRequest(db.Model):
    __tablename__ = "withdrawal_requests"

    id: Any = db.Column(db.Integer, primary_key=True)
    user_id: Any = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    amount: Any = db.Column(db.Numeric(12, 2), nullable=False)
    currency: Any = db.Column(db.String(10), default="INR", nullable=False)
    payout_mode: Any = db.Column(db.String(30), default="UPI", nullable=False)
    upi_id: Any = db.Column(db.String(100), nullable=True)
    bank_account_number: Any = db.Column(db.String(50), nullable=True)
    bank_ifsc: Any = db.Column(db.String(20), nullable=True)
    bank_beneficiary_name: Any = db.Column(db.String(120), nullable=True)
    status: Any = db.Column(db.String(30), default="Pending", nullable=False)
    utr_reference: Any = db.Column(db.String(100), nullable=True)
    admin_notes: Any = db.Column(db.Text, nullable=True)

    # Statutory Section 194-O / 194C TDS Audit Tracking
    pan_number: Any = db.Column(db.String(20), nullable=True)
    tds_rate: Any = db.Column(db.Numeric(4, 2), default=0.01, nullable=False, server_default="0.01")
    tds_amount: Any = db.Column(db.Numeric(12, 2), default=0.00, nullable=False, server_default="0.00")
    net_amount: Any = db.Column(db.Numeric(12, 2), default=0.00, nullable=False, server_default="0.00")

    requested_at: Any = db.Column(db.DateTime, default=get_ist_now, nullable=False)
    processed_at: Any = db.Column(db.DateTime, nullable=True)

    specialist = db.relationship("User", back_populates="withdrawal_requests")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)


class WalletTransaction(db.Model):
    __tablename__ = "wallet_transactions"

    id: Any = db.Column(db.Integer, primary_key=True)
    user_id: Any = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    amount: Any = db.Column(db.Numeric(12, 2), nullable=False)
    currency: Any = db.Column(db.String(10), default="INR", nullable=False)
    transaction_type: Any = db.Column(db.String(40), nullable=False)
    reference_type: Any = db.Column(db.String(40), nullable=True)
    reference_id: Any = db.Column(db.Integer, nullable=True)
    description: Any = db.Column(db.Text, nullable=False)
    created_at: Any = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    user = db.relationship("User", back_populates="wallet_transactions")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)


class Invoice(db.Model):
    __tablename__ = "invoice"

    id: Any = db.Column(db.Integer, primary_key=True)
    invoice_number: Any = db.Column(db.String(100), unique=True, nullable=False)
    razorpay_order_id: Any = db.Column(db.String(100), unique=True, nullable=True, index=True)
    project_id: Any = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False, index=True)
    customer_id: Any = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    gross_amount: Any = db.Column(db.Numeric(12, 2), nullable=False)
    talent_payout: Any = db.Column(db.Numeric(12, 2), nullable=False)
    agency_profit: Any = db.Column(db.Numeric(12, 2), nullable=False)
    tax_amount: Any = db.Column(db.Numeric(12, 2), default=0, nullable=False)
    paid_amount: Any = db.Column(db.Numeric(12, 2), default=0.00, nullable=False, server_default="0.00")
    currency: Any = db.Column(db.String(3), default="INR", nullable=False)
    status: Any = db.Column(db.String(30), default="Paid", nullable=False)
    payout_status: Any = db.Column(db.String(30), default="Pending Approval", nullable=False)
    payout_utr: Any = db.Column(db.String(100), nullable=True)
    payout_paid_at: Any = db.Column(db.DateTime, nullable=True)
    created_at: Any = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    project = db.relationship("Project", backref=db.backref("invoice_records", lazy=True, order_by="Invoice.id.desc()"))
    customer = db.relationship("User", backref="invoices", foreign_keys=[customer_id])

    def __init__(self, invoice_number: str = "", project_id: int | None = None, customer_id: int | None = None, gross_amount: Any = 0, talent_payout: Any = 0, agency_profit: Any = 0, tax_amount: Any = 0, currency: str = "INR", status: str = "Paid", payout_status: str = "Pending Approval", paid_amount: Any = 0.00, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.invoice_number = invoice_number if invoice_number else f"INV-{secrets.token_hex(4).upper()}"
        if project_id is not None:
            self.project_id = project_id
        if customer_id is not None:
            self.customer_id = customer_id
        self.gross_amount = gross_amount if gross_amount is not None else 0
        self.talent_payout = talent_payout if talent_payout is not None else 0
        self.agency_profit = agency_profit if agency_profit is not None else 0
        self.tax_amount = tax_amount if tax_amount is not None else 0
        self.currency = currency if currency else "INR"
        self.status = status if status else "Paid"
        self.payout_status = payout_status if payout_status else "Pending Approval"
        self.paid_amount = paid_amount if paid_amount is not None else 0.00

    @property
    def amount(self) -> Any:
        """Alias for gross_amount for backward compatibility with external integrations & tests."""
        return self.gross_amount

    @amount.setter
    def amount(self, value: Any) -> None:
        self.gross_amount = value

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "invoice_number": self.invoice_number,
            "project_id": self.project_id,
            "customer_id": self.customer_id,
            "gross_amount": float(self.gross_amount) if self.gross_amount is not None else 0.0,
            "amount": float(self.gross_amount) if self.gross_amount is not None else 0.0,
            "talent_payout": float(self.talent_payout) if self.talent_payout is not None else 0.0,
            "agency_profit": float(self.agency_profit) if self.agency_profit is not None else 0.0,
            "tax_amount": float(self.tax_amount) if self.tax_amount is not None else 0.0,
            "paid_amount": float(self.paid_amount) if self.paid_amount is not None else 0.0,
            "currency": self.currency,
            "status": self.status,
            "payout_status": self.payout_status,
            "payout_utr": self.payout_utr,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class FinancialAuditLog(db.Model):
    """Immutable ledger of all financial state mutations and administrative overrides."""
    __tablename__ = "financial_audit_logs"

    id: Any = db.Column(db.Integer, primary_key=True)
    invoice_id: Any = db.Column(db.Integer, db.ForeignKey("invoice.id", ondelete="CASCADE"), nullable=False, index=True)
    action: Any = db.Column(db.String(100), nullable=False)
    previous_status: Any = db.Column(db.String(50), nullable=True)
    new_status: Any = db.Column(db.String(50), nullable=False)
    amount: Any = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)
    actor_id: Any = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True)
    notes: Any = db.Column(db.Text, nullable=True)
    created_at: Any = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    invoice = db.relationship("Invoice", backref=db.backref("audit_logs", lazy="dynamic", cascade="all, delete-orphan"))
    actor = db.relationship("User", foreign_keys=[actor_id])

    def __init__(self, invoice_id: int | None = None, action: str = "", previous_status: str | None = None, new_status: str = "", amount: Any = 0.00, actor_id: int | None = None, notes: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if invoice_id is not None:
            self.invoice_id = invoice_id
        if action:
            self.action = action
        if previous_status is not None:
            self.previous_status = previous_status
        if new_status:
            self.new_status = new_status
        if amount is not None:
            self.amount = amount
        if actor_id is not None:
            self.actor_id = actor_id
        if notes is not None:
            self.notes = notes


class PaymentWebhookEvent(db.Model):
    """Global idempotent event tracking table for multi-gateway webhook callbacks."""
    __tablename__ = "payment_webhook_events"

    id: Any = db.Column(db.Integer, primary_key=True)
    event_id: Any = db.Column(db.String(255), unique=True, nullable=False, index=True)
    provider: Any = db.Column(db.String(50), nullable=False, default="stripe")  # 'stripe', 'razorpay'
    event_type: Any = db.Column(db.String(100), nullable=False)
    payload_hash: Any = db.Column(db.String(64), nullable=True)
    status: Any = db.Column(db.String(50), nullable=False, default="processed")  # 'processed', 'ignored', 'failed'
    processed_at: Any = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    def __init__(self, event_id: str = "", provider: str = "stripe", event_type: str = "", payload_hash: str | None = None, status: str = "processed", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if event_id:
            self.event_id = event_id
        if provider:
            self.provider = provider
        if event_type:
            self.event_type = event_type
        if payload_hash is not None:
            self.payload_hash = payload_hash
        if status:
            self.status = status


class AgencySetting(db.Model):
    __tablename__ = "agency_settings"

    id: Any = db.Column(db.Integer, primary_key=True, default=1)
    agency_name: Any = db.Column(db.String(120), nullable=False, default="AgencyOS")
    primary_color: Any = db.Column(db.String(7), nullable=False, default="#6366f1")
    logo_url: Any = db.Column(db.String(500), nullable=True)
    whatsapp_support_number: Any = db.Column(db.String(16), nullable=False, default="+919876543210")
    updated_at: Any = db.Column(db.DateTime, nullable=False, default=get_ist_now, onupdate=get_ist_now)

    def __init__(self, id: int = 1, agency_name: str = "AgencyOS", primary_color: str = "#6366f1", logo_url: str | None = None, whatsapp_support_number: str = "+919876543210", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.id = id
        if agency_name:
            self.agency_name = agency_name
        if primary_color:
            self.primary_color = primary_color
        if logo_url is not None:
            self.logo_url = logo_url
        if whatsapp_support_number:
            self.whatsapp_support_number = whatsapp_support_number


class RazorpaySubscriptionPlan(db.Model):
    __tablename__ = "razorpay_subscription_plan"

    id: Any = db.Column(db.Integer, primary_key=True)
    code: Any = db.Column(db.String(80), nullable=False, unique=True)
    razorpay_plan_id: Any = db.Column(db.String(100), nullable=True, unique=True)
    name: Any = db.Column(db.String(150), nullable=False)
    amount: Any = db.Column(db.Integer, nullable=False)
    currency: Any = db.Column(db.String(10), nullable=False, default="INR")
    created_at: Any = db.Column(db.DateTime, nullable=False, default=get_ist_now)

    def __init__(self, code: str = "", razorpay_plan_id: str | None = None, name: str = "", amount: int = 0, currency: str = "INR", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if code:
            self.code = code
        if razorpay_plan_id is not None:
            self.razorpay_plan_id = razorpay_plan_id
        if name:
            self.name = name
        if amount:
            self.amount = amount
        if currency:
            self.currency = currency


class RazorpaySubscription(db.Model):
    __tablename__ = "razorpay_subscription"
    __table_args__ = (
        db.Index(
            "uq_razorpay_subscription_project_open",
            "project_id",
            unique=True,
            postgresql_where=db.text("status IN ('initializing', 'created', 'authenticated', 'active', 'pending', 'halted', 'paused', 'resumed')"),
            sqlite_where=db.text("status IN ('initializing', 'created', 'authenticated', 'active', 'pending', 'halted', 'paused', 'resumed')"),
        ),
    )

    id: Any = db.Column(db.Integer, primary_key=True)
    project_id: Any = db.Column(db.Integer, db.ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id: Any = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id: Any = db.Column(db.Integer, db.ForeignKey("razorpay_subscription_plan.id"), nullable=False)
    razorpay_subscription_id: Any = db.Column(db.String(100), nullable=True, unique=True)
    amount: Any = db.Column(db.Integer, nullable=False)
    currency: Any = db.Column(db.String(10), nullable=False, default="INR")
    status: Any = db.Column(db.String(30), nullable=False, default="created")
    start_at: Any = db.Column(db.DateTime, nullable=False)

    # Monthly Hours Bank Quota on Subscription
    hours_allocated: Any = db.Column(db.Integer, default=5, nullable=False, server_default="5")
    hours_remaining: Any = db.Column(db.Numeric(5, 2), default=5.00, nullable=False, server_default="5.00")

    created_at: Any = db.Column(db.DateTime, nullable=False, default=get_ist_now)
    updated_at: Any = db.Column(db.DateTime, nullable=False, default=get_ist_now, onupdate=get_ist_now)

    project = db.relationship("Project", backref=db.backref("retainer_subscriptions", lazy=True))
    customer = db.relationship("User", backref=db.backref("retainer_subscriptions", lazy=True), foreign_keys=[customer_id])
    plan = db.relationship("RazorpaySubscriptionPlan")
    subscription_charges = db.relationship("SubscriptionCharge", back_populates="subscription", lazy=True)

    def __init__(self, project_id: int | None = None, customer_id: int | None = None, plan_id: int | None = None, razorpay_subscription_id: str | None = None, amount: int = 0, currency: str = "INR", status: str = "created", start_at: datetime | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if project_id is not None:
            self.project_id = project_id
        if customer_id is not None:
            self.customer_id = customer_id
        if plan_id is not None:
            self.plan_id = plan_id
        if razorpay_subscription_id is not None:
            self.razorpay_subscription_id = razorpay_subscription_id
        if amount:
            self.amount = amount
        if currency:
            self.currency = currency
        if status:
            self.status = status
        if start_at is not None:
            self.start_at = start_at
        self.hours_allocated = 5
        self.hours_remaining = Decimal("5.00")


class SubscriptionCharge(db.Model):
    __tablename__ = "subscription_charge"

    id: Any = db.Column(db.Integer, primary_key=True)
    subscription_id: Any = db.Column(db.Integer, db.ForeignKey("razorpay_subscription.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_id: Any = db.Column(db.Integer, db.ForeignKey("payment.id", ondelete="CASCADE"), nullable=False, unique=True)
    invoice_id: Any = db.Column(db.Integer, db.ForeignKey("invoice.id", ondelete="CASCADE"), nullable=False, unique=True)
    razorpay_payment_id: Any = db.Column(db.String(100), nullable=False, unique=True)
    event_id: Any = db.Column(db.String(100), nullable=True, unique=True)
    amount: Any = db.Column(db.Integer, nullable=False)
    currency: Any = db.Column(db.String(10), nullable=False, default="INR")
    charged_at: Any = db.Column(db.DateTime, nullable=False, default=get_ist_now)

    subscription = db.relationship("RazorpaySubscription", back_populates="subscription_charges")
    payment = db.relationship("Payment")
    invoice = db.relationship("Invoice")

    def __init__(self, subscription_id: int | None = None, payment_id: int | None = None, invoice_id: int | None = None, razorpay_payment_id: str = "", event_id: str | None = None, amount: int = 0, currency: str = "INR", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if subscription_id is not None:
            self.subscription_id = subscription_id
        if payment_id is not None:
            self.payment_id = payment_id
        if invoice_id is not None:
            self.invoice_id = invoice_id
        if razorpay_payment_id:
            self.razorpay_payment_id = razorpay_payment_id
        if event_id is not None:
            self.event_id = event_id
        if amount:
            self.amount = amount
        if currency:
            self.currency = currency

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subscription_id": self.subscription_id,
            "payment_id": self.payment_id,
            "invoice_id": self.invoice_id,
            "razorpay_payment_id": self.razorpay_payment_id,
            "event_id": self.event_id,
            "amount": self.amount,
            "currency": self.currency,
            "charged_at": self.charged_at.isoformat() if self.charged_at else None,
        }


class Review(db.Model):
    __tablename__ = "reviews"

    id: Any = db.Column(db.Integer, primary_key=True)
    project_id: Any = db.Column(db.Integer, db.ForeignKey(Project.id, ondelete="CASCADE"), nullable=False, index=True)
    customer_id: Any = db.Column(db.Integer, db.ForeignKey(User.id, ondelete="CASCADE"), nullable=False, index=True)
    rating: Any = db.Column(db.Integer, default=5, nullable=False)
    tags: Any = db.Column(db.String(255), nullable=True)
    feedback: Any = db.Column(db.Text, nullable=False)
    is_public: Any = db.Column(db.Boolean, default=True, nullable=False)
    created_at: Any = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    project = db.relationship("Project", backref=db.backref("review", uselist=False), foreign_keys=[project_id])
    customer = db.relationship("User", backref="reviews", foreign_keys=[customer_id])

    def __init__(self, project_id: int | None = None, customer_id: int | None = None, rating: int = 5, feedback: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if project_id is not None:
            self.project_id = project_id
        if customer_id is not None:
            self.customer_id = customer_id
        if rating:
            self.rating = rating
        if feedback:
            self.feedback = feedback


class ProjectAddOn(db.Model):
    __tablename__ = "project_add_ons"

    id: Any = db.Column(db.Integer, primary_key=True)
    project_id: Any = db.Column(db.Integer, db.ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True)
    invoice_id: Any = db.Column(db.Integer, db.ForeignKey("invoice.id", ondelete="CASCADE"), nullable=False, unique=True)
    title: Any = db.Column(db.String(200), nullable=False)
    amount: Any = db.Column(db.Numeric(12, 2), nullable=False)
    status: Any = db.Column(db.String(30), default="Pending Payment", nullable=False)
    created_at: Any = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    project = db.relationship("Project", backref=db.backref("add_ons", cascade="all, delete-orphan", lazy=True))
    invoice = db.relationship("Invoice", backref=db.backref("project_add_on", uselist=False))

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)


class ActivityLog(db.Model):
    __tablename__ = "activity_logs"
    __table_args__ = (db.Index("ix_activity_logs_project_created_at", "project_id", "created_at"),)

    id: Any = db.Column(db.Integer, primary_key=True)
    project_id: Any = db.Column(db.Integer, db.ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Any = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Any = db.Column(db.String(100), nullable=False)
    description: Any = db.Column(db.Text, nullable=False)
    category: Any = db.Column(db.String(30), default="info", nullable=False)
    created_at: Any = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    project = db.relationship("Project", backref=db.backref("activity_logs", cascade="all, delete-orphan", lazy=True))
    actor = db.relationship("User", backref="activity_logs", foreign_keys=[user_id])

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    @property
    def event_type(self) -> str:
        return self.action

    @classmethod
    def log_event(
        cls,
        project_id: int | None = None,
        action: str | None = None,
        description: str | None = None,
        user_id: int | None = None,
        category: str = "info",
        **kwargs: Any,
    ) -> "ActivityLog":
        log = cls()
        log.project_id = project_id
        log.user_id = user_id
        log.action = action or "ACTIVITY"
        log.description = description or ""
        log.category = category or "info"
        db.session.add(log)
        return log

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "user_id": self.user_id,
            "action": self.action,
            "description": self.description,
            "category": self.category,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class StripeWebhookEvent(db.Model):
    __tablename__ = "stripe_webhook_event"

    id: Any = db.Column(db.Integer, primary_key=True)
    event_id: Any = db.Column(db.String(100), nullable=False, unique=True, index=True)
    event_type: Any = db.Column(db.String(100), nullable=False)
    project_id: Any = db.Column(db.Integer, db.ForeignKey("project.id", ondelete="CASCADE"), nullable=True, index=True)
    processed_at: Any = db.Column(db.DateTime, nullable=False, default=get_ist_now)
    payload: Any = db.Column(db.Text, nullable=True)

    project = db.relationship("Project", backref="stripe_webhook_events")

    def __init__(self, event_id: str = "", event_type: str = "", project_id: int | None = None, payload: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if event_id:
            self.event_id = event_id
        if event_type:
            self.event_type = event_type
        if project_id is not None:
            self.project_id = project_id
        if payload is not None:
            self.payload = payload

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
        event = cls(event_id=event_id, event_type=event_type, project_id=project_id, payload=payload)
        db.session.add(event)
        return event


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


class Notification(db.Model):
    __tablename__ = "notification"

    id: Any = db.Column(db.Integer, primary_key=True)
    user_id: Any = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    title: Any = db.Column(db.String(200), nullable=False)
    message: Any = db.Column(db.Text, nullable=False)
    link: Any = db.Column(db.String(500), nullable=True)
    is_read: Any = db.Column(db.Boolean, default=False, nullable=False)
    created_at: Any = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    user = db.relationship("User", backref=db.backref("notifications", lazy=True, order_by="Notification.created_at.desc()"), foreign_keys=[user_id])

    def __init__(self, user_id: int | None = None, title: str | None = None, message: str | None = None, link: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if user_id is not None:
            self.user_id = user_id
        if title is not None:
            self.title = title
        if message is not None:
            self.message = message
        if link is not None:
            self.link = link

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "message": self.message,
            "link": self.link,
            "is_read": self.is_read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Dispute(db.Model):
    __tablename__ = "disputes"

    id: Any = db.Column(db.Integer, primary_key=True)
    project_id: Any = db.Column(db.Integer, db.ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True)
    raised_by_user_id: Any = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    reason: Any = db.Column(db.Text, nullable=False)
    status: Any = db.Column(db.String(30), default="OPEN", nullable=False)
    admin_verdict_notes: Any = db.Column(db.Text, nullable=True)
    resolved_at: Any = db.Column(db.DateTime, nullable=True)
    created_at: Any = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    project = db.relationship("Project", backref=db.backref("disputes", cascade="all, delete-orphan", lazy=True))
    raised_by = db.relationship("User", foreign_keys=[raised_by_user_id])

    def __init__(self, project_id: int | None = None, raised_by_user_id: int | None = None, reason: str | None = None, status: str = "OPEN", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if project_id is not None:
            self.project_id = project_id
        if raised_by_user_id is not None:
            self.raised_by_user_id = raised_by_user_id
        if reason is not None:
            self.reason = reason
        if status:
            self.status = status


class WebhookFailureLog(db.Model):
    __tablename__ = "webhook_failure_log"

    id: Any = db.Column(db.Integer, primary_key=True)
    gateway: Any = db.Column(db.String(50), nullable=False, index=True)  # "Stripe" or "Razorpay"
    payload: Any = db.Column(db.Text, nullable=False)
    error_message: Any = db.Column(db.Text, nullable=False)
    created_at: Any = db.Column(db.DateTime, default=get_ist_now, nullable=False)
    is_resolved: Any = db.Column(db.Boolean, default=False, nullable=False)

    def __init__(self, gateway: str, payload: str, error_message: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.gateway = gateway
        self.payload = payload
        self.error_message = error_message