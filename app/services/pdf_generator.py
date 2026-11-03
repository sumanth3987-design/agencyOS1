"""ReportLab Statement of Work PDF Generation Engine for AgencyOS."""

import re
from io import BytesIO
from typing import Any, NamedTuple
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


class SowSignature(NamedTuple):
    signer_name: str
    signer_title: str
    ip_address: str
    signed_at: str


_SIGNATURE_PATTERN = re.compile(
    r" \[Signed by: (?P<name>.*?) \((?P<title>.*?)\) "
    r"from IP: (?P<ip>.*?) on (?P<signed_at>.*?)\]$"
)


def extract_sow_signature(requirements: str | None) -> SowSignature | None:
    """Return the recorded SOW signature, if requirements contain one."""
    match = _SIGNATURE_PATTERN.search((requirements or "").rstrip())
    if not match or not match.group("name").strip() or not match.group("title").strip():
        return None

    return SowSignature(
        signer_name=match.group("name").strip(),
        signer_title=match.group("title").strip(),
        ip_address=match.group("ip").strip(),
        signed_at=match.group("signed_at").strip(),
    )


def _plain_paragraph(value: Any, style: ParagraphStyle) -> Paragraph:
    text = escape(str(value or "")).replace("\r\n", "\n").replace("\n", "<br/>")
    return Paragraph(text, style)


def generate_sow_pdf(project: Any, customer: Any, business: Any, signature: SowSignature) -> BytesIO:
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

    customer_name = customer.name if customer else "Client"
    business_name = business.name if business else "Direct Account"
    agreement_date = (
        project.created_at.strftime("%d %B %Y") if project.created_at else "Unavailable"
    )

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
            _plain_paragraph(signature.signer_name, body_style),
        ],
        [
            _plain_paragraph("Authorized representative", muted_style),
            _plain_paragraph(signature.signer_title, muted_style),
        ],
        [
            _plain_paragraph("AgencyOS project record", muted_style),
            _plain_paragraph(
                f"Digitally acknowledged on {signature.signed_at}", muted_style
            ),
        ],
        [
            _plain_paragraph("", muted_style),
            _plain_paragraph(
                f"Recorded from IP address: {signature.ip_address}", muted_style
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