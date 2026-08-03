"""
Pydantic schema for the local action-planner pipeline.

Parses raw JSON output from any model and validates required fields.
Bad/placeholder/fabricated values in required fields are caught by field
validators and converted to ask_clarification via PipelinePlan.from_raw().
"""
from __future__ import annotations

import datetime
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# ── Bad-value detection ───────────────────────────────────────────────────────

BAD_TOKENS: frozenset[str] = frozenset({
    # Generic/missing markers
    "", "unknown", "tbd", "null", "none", "unspecified", "n/a", "na",
    "not specified", "not provided", "missing", "placeholder",
    # Relative time expressions (not explicit datetimes)
    "now", "today", "tomorrow", "yesterday", "soon", "later", "asap",
    "immediately", "shortly", "eventually", "whenever", "someday",
    # Self-referential recipient stand-ins and pronouns
    "user", "yourself", "sender", "me", "myself", "recipient",
    "i", "you", "he", "she", "they", "we", "it", "him", "her", "them", "us",
})

_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def is_bad_str(value: str) -> bool:
    """True if value is a known placeholder or starts with one."""
    v = value.strip().lower()
    if v in BAD_TOKENS:
        return True
    first = re.split(r"\W+", v)[0] if v else ""
    return first in BAD_TOKENS


def is_past_datetime(value: str) -> bool:
    """True if value is an ISO date set in the past (likely fabricated)."""
    m = _ISO_RE.match(value.strip())
    if not m:
        return False
    try:
        d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return d < datetime.date.today()
    except ValueError:
        return True


# ── Per-action args models ────────────────────────────────────────────────────

class CreateReminderArgs(BaseModel):
    title: str
    due_at: str

    @field_validator("title")
    @classmethod
    def title_not_bad(cls, v: str) -> str:
        if is_bad_str(v):
            raise ValueError(f"title is a placeholder: {v!r}")
        return v

    @field_validator("due_at")
    @classmethod
    def due_at_must_be_explicit(cls, v: str) -> str:
        if is_bad_str(v):
            raise ValueError(f"due_at is a placeholder: {v!r}")
        if is_past_datetime(v):
            raise ValueError(f"due_at is a past/fabricated datetime: {v!r}")
        return v


class SendMessageArgs(BaseModel):
    channel: str
    recipient: str
    message: str

    @field_validator("recipient")
    @classmethod
    def recipient_must_be_specific(cls, v: str) -> str:
        if is_bad_str(v):
            raise ValueError(f"recipient is a placeholder: {v!r}")
        return v


class CreateTaskArgs(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def title_not_bad(cls, v: str) -> str:
        if is_bad_str(v):
            raise ValueError(f"title is a placeholder: {v!r}")
        return v


class WebSearchArgs(BaseModel):
    url: str
    query: str


class SummarizeDocumentArgs(BaseModel):
    summary: str


class DbReadArgs(BaseModel):
    table: str
    query: str


class DbInsertArgs(BaseModel):
    table: str
    row: dict[str, Any]

    @field_validator("row", mode="before")
    @classmethod
    def coerce_row_to_dict(cls, v: Any) -> dict[str, Any]:
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            import json as _json
            try:
                parsed = _json.loads(v)
                if isinstance(parsed, dict):
                    return parsed
            except _json.JSONDecodeError:
                pass
            # Handle Python key='value' format produced by some models
            pairs = re.findall(r"(\w+)\s*=\s*'([^']*)'", v)
            if pairs:
                return dict(pairs)
        raise ValueError(f"row must be a dict or parseable string, got {v!r}")


class AskClarificationArgs(BaseModel):
    missing: list[str] = Field(default_factory=list)
    question: str = ""


# ── Action enum ───────────────────────────────────────────────────────────────

class Action(str, Enum):
    GET_EMAILS         = "get_emails"
    SUMMARIZE_DOCUMENT = "summarize_document"
    CREATE_REMINDER    = "create_reminder"
    SEND_MESSAGE       = "send_message"
    CREATE_TASK        = "create_task"
    WEB_SEARCH         = "web_search"
    DB_READ            = "db_read"
    DB_INSERT          = "db_insert"
    DB_UPDATE          = "db_update"
    DB_DELETE          = "db_delete"
    ASK_CLARIFICATION  = "ask_clarification"


SIDE_EFFECT_ACTIONS: frozenset[Action] = frozenset({
    Action.CREATE_REMINDER,
    Action.SEND_MESSAGE,
    Action.CREATE_TASK,
    Action.DB_INSERT,
    Action.DB_UPDATE,
    Action.DB_DELETE,
})

READ_ONLY_ACTIONS: frozenset[Action] = frozenset({
    Action.GET_EMAILS,
    Action.WEB_SEARCH,
    Action.SUMMARIZE_DOCUMENT,
    Action.DB_READ,
})

_ARGS_MODEL: dict[Action, type[BaseModel]] = {
    Action.CREATE_REMINDER:    CreateReminderArgs,
    Action.SEND_MESSAGE:       SendMessageArgs,
    Action.CREATE_TASK:        CreateTaskArgs,
    Action.WEB_SEARCH:         WebSearchArgs,
    Action.SUMMARIZE_DOCUMENT: SummarizeDocumentArgs,
    Action.DB_READ:            DbReadArgs,
    Action.DB_INSERT:          DbInsertArgs,
    Action.ASK_CLARIFICATION:  AskClarificationArgs,
}

# ── Top-level plan ────────────────────────────────────────────────────────────

class PipelinePlan(BaseModel):
    action: Action
    args: dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = False
    user_response: str = ""

    @model_validator(mode="after")
    def _enforce_confirmation_rule(self) -> "PipelinePlan":
        if self.action in SIDE_EFFECT_ACTIONS:
            self.requires_confirmation = True
        elif self.action in READ_ONLY_ACTIONS:
            self.requires_confirmation = False
        return self

    def validate_args(self) -> BaseModel:
        """Parse and validate args against the per-action schema.

        Raises pydantic.ValidationError if any field is missing or bad.
        Returns the typed args model on success.
        """
        model_cls = _ARGS_MODEL.get(self.action)
        if model_cls is None:
            return AskClarificationArgs()
        return model_cls.model_validate(self.args)

    @classmethod
    def from_raw(cls, data: dict[str, Any]) -> "PipelinePlan":
        """Parse raw model output dict.

        Returns an ask_clarification plan if:
        - the action/args are structurally invalid
        - a required field is missing or contains a bad/placeholder value
        - due_at is a past (fabricated) datetime
        """
        from pydantic import ValidationError

        try:
            plan = cls.model_validate(data)
        except ValidationError:
            return cls(
                action=Action.ASK_CLARIFICATION,
                args={"missing": [], "question": "Could not parse the plan."},
                requires_confirmation=False,
                user_response="Could not parse the plan.",
            )

        if plan.action == Action.ASK_CLARIFICATION:
            return plan

        try:
            plan.validate_args()
        except ValidationError as exc:
            missing = sorted({str(e["loc"][-1]) for e in exc.errors() if e.get("loc")})
            question = (
                f"What should I use for {', '.join(missing)}?"
                if missing else "Could you clarify the details?"
            )
            return cls(
                action=Action.ASK_CLARIFICATION,
                args={"missing": missing, "question": question},
                requires_confirmation=False,
                user_response=question,
            )

        return plan
