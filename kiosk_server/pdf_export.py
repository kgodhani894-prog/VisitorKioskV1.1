from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from io import BytesIO
from pathlib import Path
import threading
import unicodedata

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle


VISIT_EXPORT_FIELDS = (
    ("Record ID", "id"),
    ("First name", "first_name"),
    ("Last name", "last_name"),
    ("Phone", "phone"),
    ("Email", "email"),
    ("Company", "company"),
    ("Host", "employee_name"),
    ("Host record ID", "employee_id"),
    ("Kiosk location", "kiosk_id"),
    ("Status", "status"),
    ("Voided at (UTC)", "voided_at"),
    ("Voided reason", "voided_reason"),
    ("Email status", "notification_status"),
    ("Email detail", "notification_detail"),
    ("Email attempts", "notification_attempts"),
    ("Next email retry (UTC)", "notification_next_attempt_at"),
    ("Last email attempt (UTC)", "notification_last_attempt_at"),
    ("Checked in (UTC)", "created_at"),
)
_FONT_LOCK = threading.Lock()


def _register_unicode_font() -> str:
    font_name = "EliteRfVera"
    with _FONT_LOCK:
        if font_name not in pdfmetrics.getRegisteredFontNames():
            font_path = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
            pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    return font_name


def _safe_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.splitlines())
    return "".join(
        character for character in text
        if unicodedata.category(character) not in {"Cc", "Cf"}
    )


def build_visits_pdf(visits: list[dict]) -> bytes:
    """Build a readable PDF of all visitor fields, excluding photo data and filename."""
    buffer = BytesIO()
    font_name = _register_unicode_font()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.55 * inch,
        rightMargin=0.55 * inch,
        topMargin=0.62 * inch,
        bottomMargin=0.58 * inch,
        title="Elite RF Visitor Database Export",
        author="Elite RF Visitor Kiosk",
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="EliteTitle", parent=styles["Title"], fontName=font_name,
        fontSize=18, leading=22, textColor=colors.HexColor("#1D5B91"),
        alignment=TA_CENTER, spaceAfter=5,
    ))
    styles.add(ParagraphStyle(
        name="EliteSubTitle", parent=styles["Normal"], fontName=font_name,
        fontSize=8, leading=11, textColor=colors.HexColor("#516A7B"),
        alignment=TA_CENTER, spaceAfter=14,
    ))
    styles.add(ParagraphStyle(
        name="EliteRecord", parent=styles["Heading3"], fontName=font_name,
        fontSize=10, leading=13, textColor=colors.HexColor("#C85232"),
        spaceBefore=7, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="EliteLabel", parent=styles["Normal"], fontName=font_name,
        fontSize=7, leading=9, textColor=colors.HexColor("#31536A"),
    ))
    styles.add(ParagraphStyle(
        name="EliteValue", parent=styles["Normal"], fontName=font_name,
        fontSize=7.5, leading=10, textColor=colors.HexColor("#1B2B36"),
        splitLongWords=True,
    ))

    def draw_footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont(font_name, 7)
        canvas.setFillColor(colors.HexColor("#657B89"))
        canvas.drawString(document.leftMargin, 0.32 * inch, "Confidential - visitor records")
        canvas.drawRightString(letter[0] - document.rightMargin, 0.32 * inch, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    story = [
        Paragraph("Elite RF Visitor Database Export", styles["EliteTitle"]),
        Paragraph(f"{len(visits)} visitor record(s) | Generated {generated} | Photos excluded", styles["EliteSubTitle"]),
    ]

    if not visits:
        story.append(Paragraph("No visitor records are currently in the database.", styles["Normal"]))

    usable_width = letter[0] - document.leftMargin - document.rightMargin
    label_width = 0.92 * inch
    value_width = (usable_width - 2 * label_width) / 2
    widths = [label_width, value_width, label_width, value_width]
    for visit in visits:
        story.append(Paragraph(f"Visitor record {escape(_safe_text(visit.get('id', '')))}", styles["EliteRecord"]))
        rows = []
        fields = list(VISIT_EXPORT_FIELDS)
        for offset in range(0, len(fields), 2):
            row = []
            for label, key in fields[offset:offset + 2]:
                row.extend((
                    Paragraph(escape(label), styles["EliteLabel"]),
                    Paragraph(escape(_safe_text(visit.get(key, ""))), styles["EliteValue"]),
                ))
            if len(row) == 2:
                row.extend(("", ""))
            rows.append(row)
        table = LongTable(rows, colWidths=widths, repeatRows=0, splitByRow=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F0F6F8")),
            ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#F0F6F8")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#D8E4E9")),
        ]))
        story.extend((table, Spacer(1, 9)))

    document.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buffer.getvalue()
