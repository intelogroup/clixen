"""
Workflow Starter Pack — pre-built workflows that save money by replacing Zapier/Make.
Includes multi-step pipeline workflows that chain Gmail, AI, iMessage, Telegram, etc.

Usage:
    from workflows import create_lead_alert, create_daily_digest, ...
    create_lead_alert(spreadsheet_id="abc123")
"""

from workflows.lead_alert import create_lead_alert, WATCHER_SCHEMA as LEAD_ALERT_SCHEMA
from workflows.invoice_tracker import (
    create_invoice_tracker,
    WATCHER_SCHEMA as INVOICE_TRACKER_SCHEMA,
)
from workflows.daily_digest import create_daily_digest, WATCHER_SCHEMA as DAILY_DIGEST_SCHEMA
from workflows.form_forwarder import create_form_forwarder, WATCHER_SCHEMA as FORM_FORWARDER_SCHEMA
from workflows.meeting_prep import create_meeting_prep, WATCHER_SCHEMA as MEETING_PREP_SCHEMA
from workflows.imessage_reminder import (
    create_imessage_reminder,
    WATCHER_SCHEMA as IMESSAGE_REMINDER_SCHEMA,
)
from workflows.imessage_digest import (
    create_imessage_digest,
    WATCHER_SCHEMA as IMESSAGE_DIGEST_SCHEMA,
)
from workflows.imessage_forwarder import (
    create_imessage_forwarder,
    WATCHER_SCHEMA as IMESSAGE_FORWARDER_SCHEMA,
)
from workflows.imessage_briefing import (
    create_imessage_briefing,
    WATCHER_SCHEMA as IMESSAGE_BRIEFING_SCHEMA,
)
from workflows.imessage_meeting_prep import (
    create_imessage_meeting_prep,
    WATCHER_SCHEMA as IMESSAGE_MEETING_PREP_SCHEMA,
)
from workflows.imessage_smart_monitor import (
    create_imessage_smart_monitor,
    WATCHER_SCHEMA as IMESSAGE_SMART_MONITOR_SCHEMA,
)
from workflows.daily_research_briefing import (
    create_daily_research_briefing,
    WATCHER_SCHEMA as RESEARCH_BRIEFING_SCHEMA,
)
from workflows.expense_tracker import (
    create_expense_tracker,
    WATCHER_SCHEMA as EXPENSE_TRACKER_SCHEMA,
)
from workflows.competitor_monitor import (
    create_competitor_monitor,
    WATCHER_SCHEMA as COMPETITOR_MONITOR_SCHEMA,
)
from workflows.document_processor import (
    create_document_processor,
    WATCHER_SCHEMA as DOC_PROCESSOR_SCHEMA,
)
from workflows.multi_escalator import (
    create_multi_escalator,
    WATCHER_SCHEMA as MULTI_ESCALATOR_SCHEMA,
)
from workflows.svg_poster_generator import (
    create_svg_poster_generator,
    WATCHER_SCHEMA as SVG_POSTER_SCHEMA,
)
from workflows.image_batch_processor import (
    create_image_batch_processor,
    WATCHER_SCHEMA as IMAGE_BATCH_SCHEMA,
)
from workflows.report_generator import (
    create_report_generator,
    WATCHER_SCHEMA as REPORT_GENERATOR_SCHEMA,
)
from workflows.file_organizer import (
    create_file_organizer,
    WATCHER_SCHEMA as FILE_ORGANIZER_SCHEMA,
)
from workflows.template_filler import (
    create_template_filler,
    WATCHER_SCHEMA as TEMPLATE_FILLER_SCHEMA,
)

__all__ = [
    "create_lead_alert",
    "create_daily_digest",
    "create_form_forwarder",
    "create_invoice_tracker",
    "create_meeting_prep",
    "create_imessage_reminder",
    "create_imessage_digest",
    "create_imessage_forwarder",
    "create_imessage_briefing",
    "create_imessage_meeting_prep",
    "create_imessage_smart_monitor",
    "create_daily_research_briefing",
    "create_expense_tracker",
    "create_competitor_monitor",
    "create_document_processor",
    "create_multi_escalator",
    "create_svg_poster_generator",
    "create_image_batch_processor",
    "create_report_generator",
    "create_file_organizer",
    "create_template_filler",
    "LEAD_ALERT_SCHEMA",
    "INVOICE_TRACKER_SCHEMA",
    "DAILY_DIGEST_SCHEMA",
    "FORM_FORWARDER_SCHEMA",
    "MEETING_PREP_SCHEMA",
    "IMESSAGE_REMINDER_SCHEMA",
    "IMESSAGE_DIGEST_SCHEMA",
    "IMESSAGE_FORWARDER_SCHEMA",
    "IMESSAGE_BRIEFING_SCHEMA",
    "IMESSAGE_MEETING_PREP_SCHEMA",
    "IMESSAGE_SMART_MONITOR_SCHEMA",
    "RESEARCH_BRIEFING_SCHEMA",
    "EXPENSE_TRACKER_SCHEMA",
    "COMPETITOR_MONITOR_SCHEMA",
    "DOC_PROCESSOR_SCHEMA",
    "MULTI_ESCALATOR_SCHEMA",
    "SVG_POSTER_SCHEMA",
    "IMAGE_BATCH_SCHEMA",
    "REPORT_GENERATOR_SCHEMA",
    "FILE_ORGANIZER_SCHEMA",
    "TEMPLATE_FILLER_SCHEMA",
]


def list_workflows() -> dict:
    """List all available workflows."""
    return {
        "lead_alert": {
            "name": "Lead Alert Pipeline",
            "description": "Google Sheet → Telegram + task",
            "schema": LEAD_ALERT_SCHEMA,
        },
        "invoice_tracker": {
            "name": "Invoice Tracker",
            "description": "New file → save + notify",
            "schema": INVOICE_TRACKER_SCHEMA,
        },
        "daily_digest": {
            "name": "Daily Business Digest",
            "description": "Schedule → daily summary",
            "schema": DAILY_DIGEST_SCHEMA,
        },
        "form_forwarder": {
            "name": "Form Response Forwarder",
            "description": "Webhook → email + notify",
            "schema": FORM_FORWARDER_SCHEMA,
        },
        "meeting_prep": {
            "name": "Meeting Prep",
            "description": "Calendar → pre-meeting reminder",
            "schema": MEETING_PREP_SCHEMA,
        },
        "imessage_reminder": {
            "name": "iMessage Reminder",
            "description": "Schedule → iMessage reminder",
            "schema": IMESSAGE_REMINDER_SCHEMA,
        },
        "imessage_digest": {
            "name": "iMessage Email Digest",
            "description": "Gmail query → iMessage summary",
            "schema": IMESSAGE_DIGEST_SCHEMA,
        },
        "imessage_forwarder": {
            "name": "iMessage Webhook Forwarder",
            "description": "Webhook → iMessage alert",
            "schema": IMESSAGE_FORWARDER_SCHEMA,
        },
        "imessage_briefing": {
            "name": "iMessage Morning Briefing (6-step)",
            "description": "Calendar + Tasks + Gmail → AI summary → iMessage + Telegram",
            "schema": IMESSAGE_BRIEFING_SCHEMA,
        },
        "imessage_meeting_prep": {
            "name": "iMessage Meeting Prep (6-step)",
            "description": "Calendar → Tasks → Attendee Gmail → AI prep → iMessage + Telegram",
            "schema": IMESSAGE_MEETING_PREP_SCHEMA,
        },
        "imessage_smart_monitor": {
            "name": "iMessage Smart Monitor (4-step)",
            "description": "Gmail → AI classify urgency → route urgent to iMessage+TG, normal to TG",
            "schema": IMESSAGE_SMART_MONITOR_SCHEMA,
        },
        "daily_research_briefing": {
            "name": "Daily Research Briefing (8-step)",
            "description": "Scrape 3 URLs → AI summarize each → compile digest → iMessage → Telegram → Email → Log",
            "schema": RESEARCH_BRIEFING_SCHEMA,
        },
        "expense_tracker": {
            "name": "Expense Tracker (7-step)",
            "description": "Gmail receipts → AI extract amount/vendor → categorize → Sheet → iMessage → Telegram → Log",
            "schema": EXPENSE_TRACKER_SCHEMA,
        },
        "competitor_monitor": {
            "name": "Competitor Monitor (8-step)",
            "description": "Scrape URL → diff → AI classify → iMessage if major → Telegram all → Sheet → Log",
            "schema": COMPETITOR_MONITOR_SCHEMA,
        },
        "document_processor": {
            "name": "Document Processor (7-step)",
            "description": "Watch folder → detect type → OCR/summarize → iMessage result → Log",
            "schema": DOC_PROCESSOR_SCHEMA,
        },
        "multi_escalator": {
            "name": "Multi-Channel Escalator (9-step)",
            "description": "iMessage → wait → check ack → Telegram → wait → check ack → Email → Log. Conditional branching per channel.",
            "schema": MULTI_ESCALATOR_SCHEMA,
        },
        "svg_poster_generator": {
            "name": "SVG Poster Generator (8-step)",
            "description": "AI SVG → write file → convert PNG → thumbnail → copy → iMessage paths → Log",
            "schema": SVG_POSTER_SCHEMA,
        },
        "image_batch_processor": {
            "name": "Image Batch Processor (7-step)",
            "description": "Watch folder → list images → resize each → convert format → output → iMessage summary → Log",
            "schema": IMAGE_BATCH_SCHEMA,
        },
        "report_generator": {
            "name": "Report Generator (9-step)",
            "description": "Scrape URL → AI write report → save .md → HTML → PDF → archive → iMessage → Email → Log",
            "schema": REPORT_GENERATOR_SCHEMA,
        },
        "file_organizer": {
            "name": "File Organizer (6-step)",
            "description": "Watch folder → classify by extension → create subdirs → move files → iMessage summary → Log",
            "schema": FILE_ORGANIZER_SCHEMA,
        },
        "template_filler": {
            "name": "Template Filler (8-step)",
            "description": "Read template → fetch data URL → AI fill → save output → copy to dist → iMessage → Log",
            "schema": TEMPLATE_FILLER_SCHEMA,
        },
    }


if __name__ == "__main__":
    for wf in list_workflows().values():
        print(f"- {wf['name']}: {wf['description']}")
