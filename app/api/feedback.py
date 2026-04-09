"""In-app feedback widget endpoint.

Submissions are emailed to FEEDBACK_TO_EMAIL via the configured mail provider.
No DB persistence — feedback is fire-and-forget. The widget hides itself in the
frontend if the backend reports the feature is disabled.
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import VERSION, settings
from app.libs.mail import is_mail_configured, send_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    message: str = Field(..., min_length=2, max_length=4000)
    email: str | None = Field(None, max_length=254)
    page_url: str | None = Field(None, max_length=1024)
    user_agent: str | None = Field(None, max_length=512)


class FeedbackStatus(BaseModel):
    enabled: bool


def _is_feedback_enabled() -> bool:
    """Feedback works only when an email destination AND a mail provider are set."""
    return bool(settings.FEEDBACK_TO_EMAIL.strip()) and is_mail_configured()


@router.get("/status", response_model=FeedbackStatus)
async def feedback_status():
    return FeedbackStatus(enabled=_is_feedback_enabled())


@router.post("", status_code=204)
async def submit_feedback(data: FeedbackRequest, request: Request):
    if not _is_feedback_enabled():
        raise HTTPException(
            status_code=503,
            detail="Feedback is not configured on this instance",
        )

    # Build the email body — keep it human-readable, no template engine
    user_id = getattr(request.state, "user_id", None)
    parts = [
        f"OpenLucid feedback",
        f"=" * 40,
        f"",
        data.message,
        f"",
        f"-" * 40,
        f"Reply-to: {data.email or '(not provided)'}",
        f"User ID:  {user_id or '(unknown)'}",
        f"Page:     {data.page_url or '(not provided)'}",
        f"UA:       {data.user_agent or '(not provided)'}",
        f"Version:  {VERSION}",
    ]
    body = "\n".join(parts)
    subject_preview = data.message[:60].replace("\n", " ")
    subject = f"[OpenLucid feedback] {subject_preview}"

    try:
        await send_email(settings.FEEDBACK_TO_EMAIL, subject, body)
        logger.info(
            "Feedback sent to %s by user=%s page=%s len=%d",
            settings.FEEDBACK_TO_EMAIL, user_id, data.page_url, len(data.message),
        )
    except Exception as e:
        logger.exception("Feedback delivery failed")
        raise HTTPException(status_code=502, detail=f"Failed to deliver feedback: {e}")
