"""Pydantic models shared by the API, the store and the LLM layer."""

from pydantic import BaseModel, Field


class Turn(BaseModel):
    """One speaker turn parsed from a transcript."""

    index: int
    start: float  # seconds from meeting start
    speaker: str
    text: str


class Chunk(BaseModel):
    """A retrieval unit: a window of consecutive turns, never splitting a turn."""

    id: str  # "<meeting_id>:<n>"
    meeting_id: str
    start: float
    end: float
    speakers: list[str]
    text: str  # rendered as "[mm:ss] Speaker: text" lines


class ActionItem(BaseModel):
    task: str = Field(description="What has to be done, as a short imperative sentence")
    owner: str = Field(description="Person responsible, exactly as named in the transcript, or 'Unassigned'")
    due: str = Field(description="Due date or timeframe as stated, or 'Not specified'")
    timestamp: str = Field(description="mm:ss or hh:mm:ss where it was agreed")


class Decision(BaseModel):
    decision: str = Field(description="The decision, stated precisely")
    made_by: str = Field(description="Who made or announced it, or 'Group'")
    timestamp: str = Field(description="mm:ss or hh:mm:ss where it was made")


class MeetingInsights(BaseModel):
    summary: str = Field(description="3-5 sentence neutral summary of the meeting")
    decisions: list[Decision]
    action_items: list[ActionItem]
    open_questions: list[str] = Field(description="Unresolved questions or risks raised but not closed")


class MeetingInfo(BaseModel):
    id: str
    title: str
    date: str | None
    attendees: list[str]
    duration_seconds: float
    turn_count: int
    insights: MeetingInsights | None = None


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    question: str
    meeting_ids: list[str] | None = None  # None = search all meetings
    history: list[ChatMessage] = []


class Citation(BaseModel):
    chunk_id: str
    meeting_id: str
    meeting_title: str
    start: float
    end: float
    speakers: list[str]
    snippet: str
    score: float
