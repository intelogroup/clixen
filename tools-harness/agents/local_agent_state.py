"""
Pydantic state definition for the LangGraph local-agent.

State schema using Pydantic BaseModel for type safety and validation.
Follows LangGraph best practices: only pass what's needed between nodes.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any
from clients.ollama_client import DEFAULT_MODEL

from pydantic import BaseModel, Field

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class LocalAgentState(BaseModel):
    """
    State for the local-agent LangGraph agent.

    Attributes:
        messages: Conversation history (managed by LangGraph's add_messages reducer)
        step_count: Number of agent-tool loop iterations (safety counter)
        max_steps: Maximum allowed steps before forced termination
        current_model: Model being used (gemma4, mistral-nemo, etc.)
        chat_id: Optional chat session ID for conversation store
        error_count: Number of consecutive tool errors (for retry logic)
        run_id: Short hex ID for correlating logs within a single agent run
        trace: Ordered list of tool calls made during this run (append-only via operator.add)
    """

    messages: Annotated[list[AnyMessage], add_messages] = Field(
        default_factory=list,
        description="Conversation history managed by LangGraph",
    )
    step_count: int = Field(
        default=0,
        description="Number of agent-tool loop iterations",
    )
    max_steps: int = Field(
        default=15,
        description="Maximum allowed steps before forced termination",
    )
    current_model: str = Field(
        default=DEFAULT_MODEL,
        description="Model being used for inference",
    )
    chat_id: str | None = Field(
        default=None,
        description="Chat session ID for conversation store",
    )
    task: str = Field(
        default="full",
        description="Tool-filter hint: 'document' trims to filesystem+doc-creation tools "
        "for faster document-creation runs; 'full' (default) keeps form/vision/audio tools too",
    )
    error_count: int = Field(
        default=0,
        description="Consecutive tool error count",
    )
    escalated: bool = Field(
        default=False,
        description="Whether this run already escalated from the primary cloud model "
        "to CLOUD_FALLBACK_MODEL after repeated errors — set once per run to avoid "
        "flapping back and forth.",
    )
    run_id: str = Field(
        default="",
        description="Short hex ID for log correlation within a single agent run",
    )
    trace: Annotated[list[dict], operator.add] = Field(
        default_factory=list,
        description="Ordered tool-call trace for this run; accumulated across nodes",
    )
    verify_attempts: int = Field(
        default=0,
        description="Number of self-verification retries used (capped at 1)",
    )
    verified: bool = Field(
        default=False,
        description="Set True once verify_answer accepts the final answer — the streaming "
        "consumer uses this (not just 'tool-call-free AIMessage') to decide the run is truly done.",
    )
    wrap_up_nudged: bool = Field(
        default=False,
        description="Set True once should_continue has injected the one-time 'wrap up now' "
        "nudge at 80% of max_steps — prevents re-nudging every remaining step.",
    )

    model_config = {"arbitrary_types_allowed": True}
