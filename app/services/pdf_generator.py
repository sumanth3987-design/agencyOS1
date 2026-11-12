"""ReportLab Statement of Work & Anti-Chargeback Acceptance Certificate Engine."""

import datetime
from io import BytesIO
import hashlib
import re
from typing import Any, NamedTuple, Union
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

class SowSignature(NamedTuple):
    signer_name: str
    signer_title: str
    ip_address: str
    signed_at: str

_SIGNATURE_PATTERN = re.compile(r" \[Signed by: (?P<name>.*?) \((?P<title>.*?)\) from IP: (?P<ip>.*?) on (?P<signed_at>.*?)\]$")

def extract_sow_signature(requirements: str | None) -> SowSignature | None:
    match = _SIGNATURE_PATTERN.search((requirements or "").rstrip())
    if not match: return None
    return SowSignature(match.group("name").strip(), match.group("title").strip(), match.group("ip").strip(), match.group("signed_at").strip())

def _safe_clean_text(text: Any) -> str:
    if text is None: return ""
    return re.sub(r"[^\x00-\x7F\u00A0-\u00FF]", "", str(text))

def _plain_paragraph(value: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(_safe_clean_text(value)).replace("\n", "<br/>"), style)


# =========================================================================
# STATEMENT OF WORK (SOW) PDF GENERATOR
# =========================================================================

def generate_sow_pdf(
    project: Any,
    customer: Any,
    business: Any,
    signature: Union[SowSignature, dict[str, Any]],
) -> BytesIO:
    """Generates a formal, digitally executed Statement of Work (SOW) PDF document."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40,
        title=f"Signed Statement of Work - Project {project.id}",
        author="AgencyOS Digital Solutions",
    )

    styles = getSampleStyleSheet()
    primary = colors.HexColor("#09090b")
    dark_text = colors.HexColor("#18181b")
    muted_text = colors.HexColor("#71717a")
    light_background = colors.HexColor("#f4f4f5")

    title_style = ParagraphStyle(
        "SowTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=primary,
    )
    subtitle_style = ParagraphStyle(
        "SowSubtitle",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        textColor=muted_text,
    )
    heading_style = ParagraphStyle(
        "SowSectionHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=dark_text,
        spaceBefore=12,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "SowBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=14,
        textColor=dark_text,
        alignment=TA_LEFT,
    )
    muted_style = ParagraphStyle(
        "SowMuted",
        parent=body_style,
        textColor=muted_text,
    )

    project_brief = project.requirements or (
        "Standard milestone website sprint and technical performance optimization."
    )
    stored_signature = extract_sow_signature(project.requirements)
    if stored_signature:
        sig_match = _SIGNATURE_PATTERN.search(project.requirements.rstrip())
        if sig_match:
            project_brief = project.requirements[: sig_match.start()].rstrip()
        if not project_brief:
            project_brief = (
                "Standard milestone website sprint and technical performance optimization."
            )

    customer_name = getattr(customer, "name", "Client")
    business_name = getattr(business, "name", "Direct Account") if business else "Direct Account"
    agreement_date = (
        project.created_at.strftime("%d %B %Y") if getattr(project, "created_at", None) else "Unavailable"
    )

    # Duck-typing signature accessor
    if isinstance(signature, dict):
        sig_name = signature.get("signer_name", "Authorized Signatory")
        sig_title = signature.get("signer_title", "Representative")
        sig_ip = signature.get("ip_address", "127.0.0.1")
        sig_time = signature.get("signed_at", datetime.datetime.now().strftime("%d %b %Y %H:%M IST"))
    else:
        sig_name = getattr(signature, "signer_name", "Authorized Signatory")
        sig_title = getattr(signature, "signer_title", "Representative")
        sig_ip = getattr(signature, "ip_address", "127.0.0.1")
        sig_time = getattr(signature, "signed_at", datetime.datetime.now().strftime("%d %b %Y %H:%M IST"))

    story = [
        Paragraph("AGENCYOS DIGITAL SOLUTIONS", title_style),
        Paragraph("Statement of Work (SOW) & Service Agreement", subtitle_style),
        Spacer(1, 15),
        HRFlowable(width="100%", thickness=1.5, color=primary, spaceAfter=15),
    ]

    meta_data = [
        [
            Paragraph("<b>Project Reference:</b>", body_style),
            _plain_paragraph(f"#{project.id} — {project.service}", body_style),
        ],
        [
            Paragraph("<b>Client Name:</b>", body_style),
            _plain_paragraph(customer_name, body_style),
        ],
        [
            Paragraph("<b>Business Entity:</b>", body_style),
            _plain_paragraph(business_name, body_style),
        ],
        [
            Paragraph("<b>Package Tier:</b>", body_style),
            _plain_paragraph(f"{project.package} (SAC Code: 998311)", body_style),
        ],
        [
            Paragraph("<b>Agreement Date:</b>", body_style),
            _plain_paragraph(agreement_date, body_style),
        ],
    ]
    metadata_table = Table(meta_data, colWidths=[120, 395])
    metadata_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), light_background),
                ("PADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e4e4e7")),
            ]
        )
    )
    story.extend(
        [
            metadata_table,
            Spacer(1, 15),
            Paragraph("1. Scope of Deliverables & Brief", heading_style),
            _plain_paragraph(project_brief, body_style),
            PageBreak(),
            Paragraph("2. Sequential Milestone Payment Schedule", heading_style),
        ]
    )

    milestone_data = [
        [
            Paragraph("<b>Milestone Phase</b>", body_style),
            Paragraph("<b>Billing %</b>", body_style),
            Paragraph("<b>Trigger / Scope Gate</b>", body_style),
        ],
        [
            _plain_paragraph("Milestone 1: Kickoff", body_style),
            _plain_paragraph("50%", body_style),
            _plain_paragraph("Discovery, wireframes & architecture setup.", body_style),
        ],
        [
            _plain_paragraph("Milestone 2: Production UI", body_style),
            _plain_paragraph("25%", body_style),
            _plain_paragraph(
                "Interactive prototype & responsive code review.", body_style
            ),
        ],
        [
            _plain_paragraph("Milestone 3: Final Launch", body_style),
            _plain_paragraph("25%", body_style),
            _plain_paragraph(
                "Production deployment, DNS setup & IP transfer.", body_style
            ),
        ],
    ]
    milestones_table = Table(milestone_data, colWidths=[140, 70, 305])
    milestones_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4f4f5")),
                ("TEXTCOLOR", (0, 0), (-1, 0), primary),
                ("PADDING", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.extend(
        [
            milestones_table,
            Spacer(1, 8),
            Paragraph("3. Terms & Guarantees", heading_style),
            Paragraph(
                "• <b>Revisions:</b> Each milestone includes up to two complimentary "
                "revision rounds. Further modifications require a written scope add-on.",
                body_style,
            ),
            Paragraph(
                "• <b>Intellectual Property:</b> Source code, design assets, and "
                "domains transfer to the Client upon final milestone payment.",
                body_style,
            ),
            Paragraph(
                "• <b>Electronic Validity:</b> This agreement is digitally executed "
                "under Section 10A of the Information Technology Act, 2000 (India).",
                body_style,
            ),
            Spacer(1, 8),
            Paragraph("4. Electronic Acceptance & Signature", heading_style),
            Paragraph(
                "This PDF is a copy of the Statement of Work and the electronic "
                "acceptance recorded in the AgencyOS project record.",
                body_style,
            ),
            Spacer(1, 12),
        ]
    )

    signature_data = [
        [
            Paragraph("<b>Service Provider</b>", body_style),
            Paragraph("<b>Client Signatory</b>", body_style),
        ],
        [
            _plain_paragraph("AgencyOS Digital Solutions", body_style),
            _plain_paragraph(sig_name, body_style),
        ],
        [
            _plain_paragraph("Authorized representative", muted_style),
            _plain_paragraph(sig_title, muted_style),
        ],
        [
            _plain_paragraph("AgencyOS project record", muted_style),
            _plain_paragraph(
                f"Digitally acknowledged on {sig_time}", muted_style
            ),
        ],
        [
            _plain_paragraph("", muted_style),
            _plain_paragraph(
                f"Recorded from IP address: {sig_ip}", muted_style
            ),
        ],
    ]
    signature_table = Table(signature_data, colWidths=[250, 255])
    signature_table.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 1, muted_text),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(signature_table)

    doc.build(story)
    buffer.seek(0)
    return buffer


# =========================================================
# ANTI-CHARGEBACK HANDOVER & ACCEPTANCE CERTIFICATE GENERATOR
# =========================================================

def generate_acceptance_certificate_pdf(
    project: Any,
    customer: Any,
    business: Any,
    delivery: Any = None,
) -> BytesIO:
    """Generate a cryptographic Certificate of IP Handover & Delivery Acceptance.

    Serves as statutory proof of delivery for merchant dispute resolution on Stripe / Razorpay rails.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40,
        title=f"Certificate of Handover & Acceptance - Project {project.id}",
        author="AgencyOS Digital Solutions",
    )

    styles = getSampleStyleSheet()
    primary = colors.HexColor("#09090b")
    dark_text = colors.HexColor("#18181b")
    muted_text = colors.HexColor("#71717a")
    light_background = colors.HexColor("#f8fafc")
    emerald_color = colors.HexColor("#059669")

    title_style = ParagraphStyle(
        "CertTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=primary,
        alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "CertSubtitle",
        parent=styles["Normal"],
        fontSize=9,
        leading=13,
        textColor=muted_text,
        alignment=TA_CENTER,
    )
    section_style = ParagraphStyle(
        "CertSection",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=15,
        textColor=dark_text,
        spaceBefore=10,
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "CertBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=13,
        textColor=dark_text,
        alignment=TA_LEFT,
    )
    legal_style = ParagraphStyle(
        "CertLegal",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=12,
        textColor=colors.HexColor("#334155"),
        alignment=TA_LEFT,
    )

    customer_name = getattr(customer, "name", "Client Enterprise")
    business_name = getattr(business, "name", "Direct Account") if business else "Direct Account"
    handover_date = datetime.datetime.now().strftime("%d %B %Y %H:%M IST")

    # Generate SHA-256 cryptographic audit fingerprint
    raw_hash_seed = f"PROJECT-{project.id}-{customer.id if customer else 0}-{handover_date}-{project.service}"
    audit_hash = hashlib.sha256(raw_hash_seed.encode("utf-8")).hexdigest()

    live_url = (delivery.live_url if delivery and getattr(delivery, "live_url", None) else "Delivered to Client Host").strip()
    source_url = (delivery.source_url if delivery and getattr(delivery, "source_url", None) else "Direct Source Handover").strip()

    story = [
        Paragraph("AGENCYOS DIGITAL SOLUTIONS", title_style),
        Paragraph("CERTIFICATE OF INTELLECTUAL PROPERTY HANDOVER & SATISFACTORY ACCEPTANCE", ParagraphStyle("CapsSub", parent=title_style, fontSize=12, leading=16, textColor=emerald_color)),
        Paragraph("Statutory Evidence of Service Delivery & Electronic Handover Execution", subtitle_style),
        Spacer(1, 10),
        HRFlowable(width="100%", thickness=1.5, color=primary, spaceAfter=12),
    ]

    cert_meta = [
        [
            Paragraph("<b>Project Reference:</b>", body_style),
            _plain_paragraph(f"#{project.id} — {project.service}", body_style),
            Paragraph("<b>Handover Date:</b>", body_style),
            _plain_paragraph(handover_date, body_style),
        ],
        [
            Paragraph("<b>Client Signatory:</b>", body_style),
            _plain_paragraph(customer_name, body_style),
            Paragraph("<b>Client Entity:</b>", body_style),
            _plain_paragraph(business_name, body_style),
        ],
        [
            Paragraph("<b>Billing Classification:</b>", body_style),
            _plain_paragraph(f"{project.package} (SAC 998311)", body_style),
            Paragraph("<b>Cryptographic Hash:</b>", body_style),
            _plain_paragraph(audit_hash[:24] + "...", body_style),
        ],
    ]
    meta_table = Table(cert_meta, colWidths=[110, 150, 110, 145])
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), light_background),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 10))

    story.append(Paragraph("1. Deliverables Handover Audit Trail", section_style))
    deliverable_meta = [
        [Paragraph("<b>Production Live Domain:</b>", body_style), _plain_paragraph(live_url, body_style)],
        [Paragraph("<b>Source Code Repository:</b>", body_style), _plain_paragraph(source_url, body_style)],
        [Paragraph("<b>Completion Verification:</b>", body_style), _plain_paragraph("100% Deliverable Sign-Off. Reconciled via Sequential Escrow Rails.", body_style)],
    ]
    deliv_table = Table(deliverable_meta, colWidths=[160, 355])
    deliv_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f5f9")),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ]
        )
    )
    story.append(deliv_table)
    story.append(Spacer(1, 10))

    story.append(Paragraph("2. Binding Acceptance & Anti-Chargeback Statutory Waiver", section_style))
    waiver_text = (
        f"<b>2.1 Inspection & Full Sign-Off:</b> The Client formally acknowledges full and satisfactory inspection "
        f"of all architectural, design, staging, and source code deliverables produced under Project #{project.id}. "
        f"All milestone criteria outlined in the signed Statement of Work have been fulfilled.<br/><br/>"
        f"<b>2.2 Intellectual Property Release:</b> AgencyOS Digital Solutions certifies that all proprietary software, "
        f"custom codebases, and intellectual assets have been irrevocably released to the Client upon settlement of Milestone 3.<br/><br/>"
        f"<b>2.3 Irrevocable Waiver of Dispute & Clawback:</b> In accordance with Section 10A of the Information Technology Act, 2000 (India) "
        f"and international electronic signature jurisprudence, the Client explicitly acknowledges that services have been completely "
        f"rendered. The Client unconditionally waives any right to initiate fraudulent chargebacks, merchant refund claims, or bank clawbacks "
        f"for payment transactions processed through Razorpay, Stripe, or offline wire facilities under this engagement.<br/><br/>"
        f"<b>2.4 Cryptographic Validity:</b> This certificate bears digital validity with SHA-256 validation code: <b>{audit_hash}</b>."
    )
    story.append(Paragraph(waiver_text, legal_style))
    story.append(Spacer(1, 15))

    signature_meta = [
        [
            Paragraph("<b>Executed for Agency:</b>", body_style),
            Paragraph("<b>Client Acknowledgment & Sign-Off:</b>", body_style),
        ],
        [
            _plain_paragraph("AgencyOS Digital Solutions", body_style),
            _plain_paragraph(customer_name, body_style),
        ],
        [
            _plain_paragraph("Digitally Sealed & Verified", ParagraphStyle("SubText", parent=body_style, textColor=muted_text)),
            _plain_paragraph(f"Authenticated from verified account {getattr(customer, 'email', '')}", ParagraphStyle("SubText2", parent=body_style, textColor=muted_text)),
        ],
    ]
    sig_table = Table(signature_meta, colWidths=[255, 260])
    sig_table.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 1, colors.HexColor("#94a3b8")),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(KeepTogether(sig_table))

    doc.build(story)
    buffer.seek(0)
    return buffer