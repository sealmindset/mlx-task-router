"""Pydantic models matching the Anthropic Messages API format."""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel


# --- Request models ---


class ContentBlockText(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ContentBlockImage(BaseModel):
    type: Literal["image"] = "image"
    source: dict[str, Any]


class ContentBlockToolUse(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class ContentBlockToolResult(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: Union[str, list[dict[str, Any]], dict[str, Any], list[Any], Any] = ""
    is_error: Optional[bool] = None


class ContentBlockThinking(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str
    signature: Optional[str] = None


class SystemContent(BaseModel):
    type: Literal["text"] = "text"
    text: str
    cache_control: Optional[dict[str, str]] = None


class ThinkingConfig(BaseModel):
    type: str
    budget_tokens: Optional[int] = None


class Tool(BaseModel):
    name: str
    description: Optional[str] = None
    input_schema: dict[str, Any]


ContentBlock = Union[
    ContentBlockText,
    ContentBlockImage,
    ContentBlockToolUse,
    ContentBlockToolResult,
    ContentBlockThinking,
]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: Union[str, list[ContentBlock]]


class MessagesRequest(BaseModel):
    model: str
    max_tokens: int
    messages: list[Message]
    system: Optional[Union[str, list[SystemContent]]] = None
    stop_sequences: Optional[list[str]] = None
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    metadata: Optional[dict[str, Any]] = None
    tools: Optional[list[Tool]] = None
    tool_choice: Optional[dict[str, Any]] = None
    thinking: Optional[ThinkingConfig] = None
    original_model: Optional[str] = None


class TokenCountRequest(BaseModel):
    model: str
    messages: list[Message]
    system: Optional[Union[str, list[SystemContent]]] = None
    tools: Optional[list[Tool]] = None
    thinking: Optional[ThinkingConfig] = None
    tool_choice: Optional[dict[str, Any]] = None
    original_model: Optional[str] = None


# --- Response models ---


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: Optional[int] = 0
    cache_read_input_tokens: Optional[int] = 0


class MessageResponse(BaseModel):
    id: str
    type: str = "message"
    role: str = "assistant"
    content: list[dict[str, Any]]
    model: str
    stop_reason: str = "end_turn"
    stop_sequence: Optional[str] = None
    usage: Usage
