from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.communication.conversation_repository import build_conversation
from app.models.llm import analyse_conversation

router = APIRouter(tags=["LLM"])

class AnalyseConversationRequest(BaseModel):
    record_id: str

@router.post("/analyse-conversation")
async def analyse_conversation_endpoint(
    payload: AnalyseConversationRequest
):
    record_id = payload.record_id

    # 1️⃣ Build conversation text from DB
    conversation_text = await build_conversation(record_id)

    if not conversation_text:
        raise HTTPException(
            status_code=404,
            detail="No conversation found for given record_id"
        )

    # 2️⃣ Run LLM analysis
    llm_result = await analyse_conversation(
        record_id=record_id,
        conversation_text=conversation_text
    )

    return {
        "status": "ok",
        "record_id": record_id,
        "analysis": llm_result,
    }
