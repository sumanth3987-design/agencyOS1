from app.extensions import db, login_manager
from flask_login import UserMixin, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
def get_ist_now():
    return datetime.now(IST)


# =========================================================
# URL VALIDATION
# =========================================================

def is_valid_http_url(value):
    """
    Validate that a supplied URL uses HTTP or HTTPS.
    """

    if not value:
        return False

    try:

        parsed = urlparse(
            value.strip()
        )

        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
        )

    except Exception:

        return False


# =========================================================
# PACKAGE PRICES
# =========================================================

PACKAGE_PRICES = {

    "Basic Website": 4999,

    "Business Website": 9999,

    "Advanced Website": 19999,

    "SEO Starter": 2999,

    "SEO Growth": 6999,

    "SEO Advanced": 12000
}

class User(UserMixin, db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(100),
        nullable=False
    )

    full_name = db.Column(
        db.String(120),
        nullable=True
    )

    email = db.Column(
        db.String(120),
        nullable=False
    )

    password = db.Column(
        db.String(255),
        nullable=False
    )

    role = db.Column(
        db.String(30),
        default="customer"
    )

    is_active = db.Column(
        db.Boolean,
        default=True,
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        server_default=db.func.now()
    )

    email_verified_at = db.Column(
        db.DateTime,
        nullable=True
    )

    session_version = db.Column(
        db.Integer,
        nullable=False,
        default=0
    )


    def set_password(self, password):
        """Hash and store the given plain-text password."""
        self.password = generate_password_hash(password)

    def check_password(self, password):
        """Return True if the given plain-text password matches the stored hash."""
        return check_password_hash(self.password, password)


    projects = db.relationship(
        "Project",
        backref="customer",
        lazy=True
    )

    professional_profile = db.relationship(
        "ProfessionalProfile",
        backref="user",
        uselist=False,
        cascade="all, delete-orphan"
    )

    customer_profile = db.relationship(
        "CustomerProfile",
        backref="user",
        uselist=False,
        cascade="all, delete-orphan"
    )
    businesses = db.relationship(
    "Business",
    back_populates="customer",
    cascade="all, delete-orphan",
    lazy=True
)


# =========================================================
# CUSTOMER PROFILE MODEL
# =========================================================

class CustomerProfile(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        unique=True,
        nullable=False
    )

    phone = db.Column(
        db.String(30),
        nullable=True
    )

    location = db.Column(
        db.String(150),
        nullable=True
    )

    business_name = db.Column(
        db.String(150),
        nullable=True
    )

    business_type = db.Column(
        db.String(100),
        nullable=True
    )

    website = db.Column(
        db.String(300),
        nullable=True
    )

    social_links = db.Column(
        db.Text,
        nullable=True
    )

    business_description = db.Column(
        db.Text,
        nullable=True
    )

    target_audience = db.Column(
        db.Text,
        nullable=True
    )

    business_goals = db.Column(
        db.Text,
        nullable=True
    )

    preferred_budget = db.Column(
        db.String(100),
        nullable=True
    )

    profile_completed = db.Column(
        db.Boolean,
        default=False
    )

    created_at = db.Column(
        db.DateTime,
        server_default=db.func.now()
    )

    updated_at = db.Column(
        db.DateTime,
        server_default=db.func.now(),
        onupdate=db.func.now()
    )
class Business(db.Model):
    __tablename__ = "business"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    name = db.Column(
        db.String(200),
        nullable=False
    )

    business_type = db.Column(
        db.String(150),
        nullable=True
    )

    location = db.Column(
        db.String(250),
        nullable=True
    )

    website = db.Column(
        db.String(500),
        nullable=True
    )

    social_links = db.Column(
        db.Text,
        nullable=True
    )

    description = db.Column(
        db.Text,
        nullable=True
    )

    target_audience = db.Column(
        db.Text,
        nullable=True
    )

    goals = db.Column(
        db.Text,
        nullable=True
    )

    preferred_budget = db.Column(
        db.String(100),
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        default=get_ist_now
    )

    updated_at = db.Column(
        db.DateTime,
        default=get_ist_now,
        onupdate=get_ist_now
    )

    customer = db.relationship(
        "User",
        back_populates="businesses"
    )

    projects = db.relationship(
        "Project",
        back_populates="business",
        lazy=True
    )

# =========================================================
# PROFESSIONAL PROFILE MODEL
# =========================================================

class UserToken(db.Model):
    __tablename__ = "user_token"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
        index=True
    )

    purpose = db.Column(
        db.String(40),
        nullable=False,
        index=True
    )

    token_hash = db.Column(
        db.String(64),
        nullable=False,
        unique=True,
        index=True
    )

    expires_at = db.Column(
        db.DateTime,
        nullable=False,
        index=True
    )

    used_at = db.Column(
        db.DateTime,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        server_default=db.func.now(),
        nullable=False
    )

    __table_args__ = (
        db.Index(
            "ix_user_token_user_purpose",
            "user_id",
            "purpose"
        ),
    )

    user = db.relationship(
        "User",
        backref=db.backref(
            "tokens",
            lazy=True,
            cascade="all, delete-orphan"
        )
    )



class ProfessionalProfile(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        unique=True,
        nullable=False
    )

    expertise = db.Column(
        db.String(100),
        nullable=False,
        default=""
    )

    experience = db.Column(
        db.String(50),
        nullable=False,
        default=""
    )

    about = db.Column(
        db.Text,
        nullable=True
    )

    phone = db.Column(
        db.String(30),
        nullable=True
    )

    location = db.Column(
        db.String(150),
        nullable=True
    )

    skills = db.Column(
        db.Text,
        nullable=True
    )

    technologies = db.Column(
        db.Text,
        nullable=True
    )

    skill_level = db.Column(
        db.String(50),
        nullable=True
    )

    project_types = db.Column(
        db.Text,
        nullable=True
    )

    portfolio = db.Column(
        db.String(500),
        nullable=True
    )

    github = db.Column(
        db.String(300),
        nullable=True
    )

    linkedin = db.Column(
        db.String(300),
        nullable=True
    )

    education = db.Column(
        db.Text,
        nullable=True
    )

    certifications = db.Column(
        db.Text,
        nullable=True
    )

    work_experience = db.Column(
        db.Text,
        nullable=True
    )

    availability = db.Column(
        db.String(50),
        nullable=True
    )

    current_workload = db.Column(
        db.String(100),
        nullable=True
    )

    # === NEW PAYOUT & BANKING FIELDS ===
    upi_id = db.Column(
        db.String(100),
        nullable=True
    )

    bank_account_number = db.Column(
        db.String(50),
        nullable=True
    )

    bank_ifsc = db.Column(
        db.String(20),
        nullable=True
    )

    pan_number = db.Column(
        db.String(20),
        nullable=True
    )

    verification_status = db.Column(
        db.String(30),
        default="Pending"
    )

    verification_notes = db.Column(
        db.Text,
        nullable=True
    )

    profile_completed = db.Column(
        db.Boolean,
        default=False
    )

    created_at = db.Column(
        db.DateTime,
        server_default=db.func.now()
    )

    updated_at = db.Column(
        db.DateTime,
        server_default=db.func.now(),
        onupdate=db.func.now()
    )


# =========================================================
# PROJECT MODEL
# =========================================================

class Project(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    business_id = db.Column(
    db.Integer,
    db.ForeignKey("business.id"),
    nullable=True
    )

    business = db.relationship(
    "Business",
    back_populates="projects"
    )

    service = db.Column(
        db.String(100),
        nullable=False
    )

    package = db.Column(
        db.String(100),
        nullable=False
    )

    requirements = db.Column(
        db.Text,
        nullable=False
    )

    status = db.Column(
        db.String(50),
        default="Pending Assignment"
    )

    revision_comment = db.Column(
        db.Text,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        server_default=db.func.now(),
        nullable=False
    )

    milestones = db.relationship(
        "Milestone",
        backref="project",
        cascade="all, delete-orphan",
        lazy=True
    )


# =========================================================
# PAYMENT MODEL
# =========================================================

class Payment(db.Model):
    __table_args__ = (
        # Unique razorpay_order_id across all payment records
        db.UniqueConstraint('razorpay_order_id', name='uq_payment_razorpay_order_id'),
        # Unique razorpay_payment_id when non-NULL (partial unique index)
        db.Index(
            'uq_payment_razorpay_payment_id_partial',
            'razorpay_payment_id',
            unique=True,
            postgresql_where=db.text('razorpay_payment_id IS NOT NULL'),
            sqlite_where=db.text('razorpay_payment_id IS NOT NULL'),
        ),
        # At most one payment with status 'Created' per (project_id, customer_id)
        db.Index(
            'uq_payment_project_customer_created',
            'project_id',
            'customer_id',
            unique=True,
            postgresql_where=db.text("status = 'Created'"),
            sqlite_where=db.text("status = 'Created'"),
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project.id"),
        nullable=False
    )

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    razorpay_order_id = db.Column(
        db.String(100),
        nullable=False
    )

    razorpay_payment_id = db.Column(
        db.String(100),
        nullable=True
    )

    gateway_payment_id = db.Column(
        db.String(100),
        unique=True,
        nullable=True,
        index=True
    )

    amount = db.Column(
        db.Integer,
        nullable=False
    )

    currency = db.Column(
        db.String(10),
        default="INR",
        nullable=False
    )

    status = db.Column(
        db.String(30),
        default="Created",
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        server_default=db.func.now(),
        nullable=False
    )

    paid_at = db.Column(
        db.DateTime,
        nullable=True
    )

    project = db.relationship(
        "Project",
        backref="payments"
    )

    customer = db.relationship(
        "User",
        backref="payments"
    )


# =========================================================
# MILESTONE MODEL (Fund Flow)
# =========================================================

class Milestone(db.Model):
    __tablename__ = "milestones"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False
    )
    title = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    status = db.Column(
        db.String(30),
        default="pending",
        nullable=False
    )  # 'pending', 'funded', 'released'
    fund_flow_type = db.Column(
        db.String(50),
        default="escrow_milestone",
        nullable=False
    )
    due_date = db.Column(db.DateTime, nullable=True)
    released_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(
        db.DateTime,
        server_default=db.func.now(),
        nullable=False
    )


# =========================================================
# PROFESSIONAL PROJECT APPLICATION
# =========================================================

class ProfessionalProjectApplication(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project.id"),
        nullable=False
    )

    professional_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    status = db.Column(
        db.String(30),
        default="Applied"
    )

    created_at = db.Column(
        db.DateTime,
        server_default=db.func.now()
    )

    project = db.relationship(
        "Project",
        backref="applications"
    )

    professional = db.relationship(
        "User",
        backref="project_applications"
    )


# =========================================================
# PROJECT DELIVERY MODEL
# =========================================================

class ProjectDelivery(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project.id"),
        nullable=False,
        index=True
    )

    professional_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
        index=True
    )

    submission_number = db.Column(
        db.Integer,
        nullable=False,
        default=1
    )

    live_url = db.Column(
        db.String(500),
        nullable=True
    )

    source_url = db.Column(
        db.String(500),
        nullable=True
    )

    preview_url = db.Column(
        db.String(500),
        nullable=True
    )

    completion_notes = db.Column(
        db.Text,
        nullable=False
    )

    status = db.Column(
        db.String(30),
        nullable=False,
        default="Submitted"
    )

    review_comment = db.Column(
        db.Text,
        nullable=True
    )

    submitted_at = db.Column(
        db.DateTime,
        nullable=False,
        default=get_ist_now
    )

    reviewed_at = db.Column(
        db.DateTime,
        nullable=True
    )

    project = db.relationship(
        "Project",
        backref=db.backref(
            "deliveries",
            lazy=True,
            order_by="ProjectDelivery.submission_number"
        )
    )

    professional = db.relationship(
        "User",
        backref="project_deliveries"
    )


# =========================================================
# SERVICES
# =========================================================

services = [

    {
        "slug": "website-development",
        "name": "Website Design & Development",
        "description":
            "Professional websites for businesses, "
            "portfolios, startups and organizations.",
        "icon": "🌐",
        "packages": [
            {
                "id": "website-basic",
                "name": "Basic Website",
                "price": 4999
            },
            {
                "id": "website-business",
                "name": "Business Website",
                "price": 9999
            },
            {
                "id": "website-advanced",
                "name": "Advanced Website",
                "price": 19999
            }
        ]
    },

    {
        "slug": "seo",
        "name": "SEO",
        "description":
            "Search engine optimization to improve "
            "website visibility and organic traffic.",
        "icon": "📈",
        "packages": [
            {
                "id": "seo-starter",
                "name": "SEO Starter",
                "price": 2999
            },
            {
                "id": "seo-growth",
                "name": "SEO Growth",
                "price": 6999
            },
            {
                "id": "seo-advanced",
                "name": "SEO Advanced",
                "price": 12000
            }
        ]
    }
]
# ========================================================
# Professional expertise matching helper
# =========================================================

def professional_has_expertise(profile, service):
   
    if not profile or not profile.expertise or not service:
        return False

    expertise_values = [
        item.strip()
        for item in profile.expertise.split(",")
        if item.strip()
    ]

    return service.strip() in expertise_values

# =========================================================
# PROFILE COMPLETION HELPERS
# =========================================================

def calculate_professional_profile_completion(profile):

    if not profile:
        return 0

    fields = [
        profile.expertise,
        profile.experience,
        profile.about,
        profile.phone,
        profile.location,
        profile.skills,
        profile.technologies,
        profile.skill_level,
        profile.project_types,
        profile.portfolio,
        profile.github,
        profile.linkedin,
        profile.education,
        profile.certifications,
        profile.work_experience,
        profile.availability,
        profile.current_workload
    ]

    completed = sum(
        1
        for field in fields
        if field and str(field).strip()
    )

    total = len(fields)

    if total == 0:
        return 0

    return round(
        (completed / total) * 100
    )


def calculate_customer_profile_completion(profile):

    if not profile:
        return 0

    fields = [
        profile.phone,
        profile.location,
        profile.business_name,
        profile.business_type,
        profile.website,
        profile.social_links,
        profile.business_description,
        profile.target_audience,
        profile.business_goals,
        profile.preferred_budget
    ]

    completed = sum(
        1
        for field in fields
        if field and str(field).strip()
    )

    total = len(fields)

    if total == 0:
        return 0

    return round(
        (completed / total) * 100
    )

def get_business_for_customer(business_id, customer_id):

    if not business_id:
        return None 

    try:
        business_id = int(business_id)
    except (TypeError, ValueError):
        return None

    return (
        Business.query
        .filter_by(
            id=business_id,
            customer_id=customer_id
        )
        .first()
    )

# =========================================================
# GET ASSIGNED PROFESSIONAL APPLICATION
# =========================================================

def get_assigned_professional_application(project_id):

    return ProfessionalProjectApplication.query.filter_by(
        project_id=project_id,
        professional_id=current_user.id,
        status="Assigned"
    ).first()


# =========================================================
# GET LATEST DELIVERY
# =========================================================

def get_latest_project_delivery(project_id):

    return (
        ProjectDelivery.query
        .filter_by(
            project_id=project_id
        )
        .order_by(
            ProjectDelivery.submission_number.desc()
        )
        .first()
    )





# =========================================================
# FEATURE 1: CHAT & PROJECT MESSAGING
# =========================================================

class ChatMessage(db.Model):
    __tablename__ = "chat_message"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    attachment_url = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    project = db.relationship("Project", backref=db.backref("chat_messages", lazy=True, order_by="ChatMessage.created_at.asc()"))
    sender = db.relationship("User", backref="sent_messages")

# =========================================================
# FEATURE 2: NOTIFICATIONS ENGINE
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

    user = db.relationship("User", backref=db.backref("notifications", lazy=True, order_by="Notification.created_at.desc()"))

# =========================================================
# FEATURE 4: INVOICE & PROFIT MARGIN TRACKING
# =========================================================

class Invoice(db.Model):
    __tablename__ = "invoice"

    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(100), unique=True, nullable=False)
    razorpay_order_id = db.Column(db.String(100), unique=True, nullable=True, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    gross_amount = db.Column(db.Integer, nullable=False)
    talent_payout = db.Column(db.Integer, nullable=False)
    agency_profit = db.Column(db.Integer, nullable=False)
    tax_amount = db.Column(db.Integer, default=0, nullable=False)
    status = db.Column(db.String(30), default="Paid", nullable=False)

    # === NEW TALENT PAYOUT TRACKING FIELDS ===
    payout_status = db.Column(db.String(30), default="Pending Approval", nullable=False)
    payout_utr = db.Column(db.String(100), nullable=True)
    payout_paid_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    project = db.relationship("Project", backref=db.backref("invoice_record", uselist=False))
    customer = db.relationship("User", backref="invoices")
# Add this class to app/models/__init__.py:

class Review(db.Model):
    __tablename__ = 'reviews'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    rating = db.Column(db.Integer, default=5, nullable=False)
    tags = db.Column(db.String(255), nullable=True)  # e.g. "Shipped on Time, High ROI"
    feedback = db.Column(db.Text, nullable=False)
    is_public = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=get_ist_now)

    project = db.relationship('Project', backref=db.backref('review', uselist=False))
    customer = db.relationship('User', backref='reviews')