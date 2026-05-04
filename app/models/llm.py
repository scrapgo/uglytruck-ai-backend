import os
import json
import re
import asyncio
from typing import Dict, Any

from openai import AsyncOpenAI
import google.generativeai as genai

from app.communication.conversation_repository import fetch_current_status, decide_status, update_lead_status_pg, update_quickbase_status, _rule_based_intent

# ============================================================
# LLM CLIENTS
# ============================================================

openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

groq_client = AsyncOpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


# ============================================================
# PROMPT BUILDER
# ============================================================

# def build_analysis_prompt(conversation_text: str) -> str:
#     return f"""
# You are an AI sales assistant for UglyTruck.
#
# Below is an email conversation between UglyTruck and a truck seller.
#
# Your task:
# 1. Determine seller intent:
#    - interested
#    - not_interested
#    - needs_follow_up
# 2. Extract any pricing information if mentioned
# 3. Identify any requested next steps
# 4. Provide a short summary (2–3 lines)
#
# Respond strictly in JSON with this schema:
# {{
#   "intent": "",
#   "price": "",
#   "next_action": "",
#   "summary": ""
# }}
#
# Conversation:
# {conversation_text}
# """
async def classify_intent(body: str, attachments_present: bool) -> str:
    """
    LLM-based intent classifier to branch conversations:
    - photos: attachments present or explicit photo language
    - question: question signals without photos
    - mixed: both photos + questions
    - other: default

    Falls back to the legacy rule-based classifier if the LLM call fails
    or returns an invalid intent.
    """
    # Quick short-circuit: if body is empty, just use rule-based logic
    if not body:
        return _rule_based_intent(body, attachments_present)

    # Trim very long emails to avoid huge prompts
    text = body.strip()
    if len(text) > 4000:
        text = text[:4000] + "\n\n[TRUNCATED]"

    prompt = f"""
You are an email intent classifier for a truck sales workflow.

Your job is to read the seller's email and decide ONE primary intent from this list:
- "photos": the main purpose is to send or discuss photos/images, with no real question.
- "question": the seller is mainly asking a question, and there are no photos.
- "mixed": the seller both sends photos (or talks about sending photos) AND asks questions.
- "other": anything that doesn't fit the above categories.

Inputs:
- attachments_present: {str(bool(attachments_present)).lower()}
- email_body:
\"\"\"{text}\"\"\"

Rules:
- If attachments_present is true OR the email clearly talks about photos/pictures/images being attached or sent, treat that as a "photos" signal.
- If the email contains questions (like "?", or phrases such as "can you", "could you", "how do I", "please explain"), treat that as a "question" signal.
- If you see BOTH a photos signal AND a question signal, choose "mixed".
- If neither signal is clearly present, choose "other".

Output:
Respond ONLY with a single compact JSON object on one line, no markdown, no explanations.
The JSON MUST have exactly these fields:
{{
  "intent": "photos | question | mixed | other",
  "reason": "short natural language explanation of why you chose this intent"
}}
"""

    try:
        # You can change provider ("openai" | "groq" | "gemini") if needed
        llm_result = await call_llm(prompt, provider="openai")
        analysis = llm_result.get("analysis") or {}
        intent = str(analysis.get("intent", "")).strip().lower()

        allowed = {"photos", "question", "mixed", "other"}
        if intent not in allowed:
            # If LLM returned something unexpected, fall back
            return _rule_based_intent(body, attachments_present)

        return intent
    except Exception as e:
        logging.exception(f"LLM classify_intent failed, using rule-based fallback: {e}")
        return _rule_based_intent(body, attachments_present)


def build_analysis_prompt(
    conversation_text: str, 
    truck_photos_count: int = 0,
    documents_count: int = 0,
    document_filenames: list = None,
    current_status: str = None  # ← ADD THIS PARAMETER
) -> str:
    """
    Build LLM prompt with conversation context and attachment information.
    
    Args:
        conversation_text: Full email conversation thread
        truck_photos_count: Number of truck photo attachments received in the latest email
        documents_count: Number of document attachments received in the latest email
        document_filenames: List of document filenames received (for LLM to analyze)
    """
    if document_filenames is None:
        document_filenames = []
    
    attachment_context = ""
    
    # Build context about truck photos
    if truck_photos_count > 0:
        attachment_context += f"""
TRUCK PHOTOS RECEIVED:
- The seller just sent {truck_photos_count} truck photo(s) with their latest email
- Photos have been successfully uploaded to our system
- Do NOT suggest "Need Pics" status if ALL truck photos were just received
"""
    
    # Build context about documents
    if documents_count > 0 and current_status == 'Offer Accepted':
        doc_list = ", ".join(document_filenames) if document_filenames else "unknown filenames"
        attachment_context += f"""
DOCUMENTS RECEIVED:
- The seller just sent {documents_count} document file(s) with their latest email
- Document filenames: {doc_list}
- REQUIRED DOCUMENTS: The seller must provide ALL THREE of the following:
  1. Driver's License (DL) - look for filenames containing: dl, license, licence, driver, driving, id
  2. Copy of Title - look for filenames containing: title, copy_of_title
  3. Banking Details - look for filenames containing: bank, ach, routing, account, void, check, cheque, deposit, banking

CRITICAL STATUS RULES FOR DOCUMENTS:
- If current status is already "Complete" or beyond, maintain current status
- Analyze the document filenames carefully to determine if DL, Title, and Banking details are all present
"""
    
    # If no attachments at all
    if truck_photos_count == 0 and documents_count == 0:
        attachment_context = """
CONTEXT - NO ATTACHMENTS:
- No photos or documents were included in the latest email
- You may suggest "Need Pics" if photos are still required
"""
    # ← ADD THIS: Offer detection context
    offer_context = ""
    if current_status == "Offer Made":
        offer_context = """
OFFER CONTEXT:
- The seller has received an offer from UglyTruck
- The current status is "Offer Made"
- You need to detect if the seller is ACCEPTING or REJECTING the offer

ACCEPTANCE SIGNALS:
- "yes", "accept", "sounds good", "I agree", "let's do it", "I'll take it"
- Positive confirmation language
- Asking about next steps after accepting

REJECTION SIGNALS:
- "no", "decline", "not interested", "too low", "can't accept"
- Negative language about the offer
- Counter-offer requests

If seller accepts → suggest "Offer Accepted"
If seller rejects → suggest "Offer Declined"
If unclear → maintain current status "Offer Made"
"""
    return f"""
You are an AI sales assistant named Diane for UglyTruck.

Below is an email conversation between UglyTruck and a truck seller.
{offer_context}
{attachment_context}

Your task:
1. Identify seller signals related to:
   - document submission (note if attachments were received in latest email)
   - pricing agreement
   - responsiveness
   - deal completion
2. Extract any pricing information
3. Identify next expected step
4. Suggest the MOST APPROPRIATE lead status from this list:

Allowed Statuses:
- New Lead
- Need Pics (ONLY suggest this if ALL photos were NOT received in the latest email and they are still needed)
- Complete (ONLY suggest this if current status is "Need Pics" AND all the truck view pics are present)
- Offer Made (maintain if offer was made and seller hasn't responded clearly)
- Offer Accepted (ONLY if current status is "Offer Made" AND seller clearly accepts)
- Offer Declined (ONLY if current status is "Offer Made" AND seller clearly rejects)

CRITICAL RULES:
- There will be seven views of truck: "Exterior Front", "Exterior Driver Side",
"Exterior Passenger Side", "Engine Driver Side", "Engine Passenger Side", "Exterior Rear", "Interior View of the truck".
- If truck photo(s) were just received and still NOT fulfilling ALL the truck views, SUGGEST "Need Pics" status.
- If ALL truck photo(s) were just received and FULFILLING ALL the truck views, SUGGEST "Complete" status.
- If truck photo(s) were just received and FULFILLING ALL the truck views, turn truck_view_pics_received_confirmation to 1.
- If document(s) were received, analyze the filenames to determine if ALL THREE required documents (DL, Title, Banking) are present
- If document(s) were received, turn docs_received_confirmation to 1.
- To suggest "Complete": Current status must be "Need Pics" AND all three required documents (DL, Title, Banking) must be present based on filenames
- If documents are missing, suggest "Need Pics" (seller still needs to provide missing documents)
- If no attachments were received, you may suggest "Need Pics" if photos or documents are still needed
- Choose only ONE status
- If unsure, choose the EARLIEST valid status
- Do NOT invent information
- Consider the attachment context when determining status

Respond strictly in JSON:
{{
  "suggested_status": "",
  "price": "",
  "next_action": "",
  "summary": "",
  "truck_view_pics_received_confirmation": "",
  "docs_received_confirmation": "",
  "pickup_date": ""
}}

Conversation:
{conversation_text}
"""

def build_reply_prompt(conversation_text: str) -> str:
    """
    Prompt to generate a concise, helpful reply to the seller that keeps the
    conversation moving while requesting any missing photos/docs if relevant.
    """
    return f"""
You are an AI sales assistant, named Diane for UglyTruck responding to a seller email.

Conversation so far:
{conversation_text}

Instructions:
- Provide a brief, polite reply (<= 6 sentences).
- Answer any questions explicitly.
- If photos/documents are still needed, clearly list what is missing.
- There will be only three views of truck: FRONT VIEW, SIDE VIEW AND REAR VIEW. 
- Avoid pricing commitments; if price is asked, say the team will confirm after reviewing photos.
- End with one clear next step for the seller.

Respond with ONLY the email body text (plain text or simple line breaks, no JSON).
"""


def build_lead_info_extraction_prompt(body_text: str) -> str:
    """
    Prompt to extract structured lead information (name, contact, truck details)
    from a seller's free-form email reply.
    """
    return f"""
You are an AI assistant helping UglyTruck update seller lead information in Quickbase.

Below is an email from a truck seller. The email may contain some or all of the following fields:
- First Name
- Last Name
- Email
- Phone
- State
- City
- Truck Year
- Truck Make
- Truck Model
- Engine
- Transmission

Your job:
- Carefully read the email body.
- Extract ONLY the actual values clearly provided by the seller.
- If a field is not clearly present, leave it as an empty string.
- DO NOT invent or guess any values.
- Normalize phone to digits and basic symbols (+, -, spaces) if present.
- Normalize state to its text form (e.g., \"CA\" or \"California\") as given.

Respond STRICTLY in JSON with this exact schema:
{{
  "first_name": "",
  "last_name": "",
  "email": "",
  "phone": "",
  "state": "",
  "city": "",
  "year": "",
  "make": "",
  "model": "",
  "engine": "",
  "transmission": ""
}}

Email body:
{body_text}
"""


# ============================================================
# JSON EXTRACTION (CRITICAL)
# ============================================================

def extract_json(text: str) -> Dict[str, Any]:
    """
    Safely extract JSON from LLM output.
    Handles markdown, prose, malformed output.
    """
    try:
        text = re.sub(r"```json|```", "", text).strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)

        if not match:
            raise ValueError("No JSON object found")

        return json.loads(match.group())
    except Exception:
        return {
            "intent": "unknown",
            "price": "",
            "next_action": "",
            "summary": text[:500],
            "error": "Invalid JSON returned by LLM"
        }


# ============================================================
# PROVIDER IMPLEMENTATIONS
# ============================================================

async def call_openai(prompt: str) -> Dict[str, Any]:
    print("Calling OpenAI...")
    response = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )

    return {
        "provider": "openai",
        "text": response.choices[0].message.content,
    }


async def call_groq(prompt: str) -> Dict[str, Any]:
    print("Calling Groq...")
    response = await groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )

    return {
        "provider": "groq",
        "text": response.choices[0].message.content,
    }


async def call_gemini(prompt: str) -> Dict[str, Any]:
    print("Calling Gemini...")
    model = genai.GenerativeModel("gemini-1.5-pro")

    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: model.generate_content(
            prompt,
            generation_config={"temperature": 0.2},
        ),
    )

    return {
        "provider": "gemini",
        "text": response.text,
    }


# ============================================================
# UNIFIED LLM CALLER
# ============================================================

async def call_llm(prompt: str, provider: str = "openai") -> Dict[str, Any]:
    """
    Unified async LLM caller with safe JSON parsing.
    provider: openai | groq | gemini
    """

    if provider == "openai":
        print("Calling OpenAI")
        result = await call_openai(prompt)
    elif provider == "groq":
        print("Calling GroQ")
        result = await call_groq(prompt)
    elif provider == "gemini":
        print("Calling Gemini")
        result = await call_gemini(prompt)
    else:
        raise ValueError(f"Unsupported provider: {provider}")

    parsed = extract_json(result["text"])
    print({
        "provider": result["provider"],
        "analysis": parsed,
    })
    return {
        "provider": result["provider"],
        "analysis": parsed,
    }


# ============================================================
# MAIN ENTRYPOINT
# ============================================================

import logging
from typing import Dict, Any

async def analyse_conversation(
    record_id: str,
    conversation_text: str,
    provider: str = "openai",
    truck_photos_count: int = 0,
    documents_count: int = 0,
    document_filenames: list = None,
) -> Dict[str, Any] | None:
    # ← ADD THIS: Fetch current status
    from app.communication.conversation_repository import fetch_current_status
    current_status = await fetch_current_status(int(record_id))

    if document_filenames is None:
        document_filenames = []

    logging.info("=== ANALYSE_CONVERSATION START ===")
    logging.info(
        f"Attachments received in this email: "
        f"{truck_photos_count} truck photo(s), {documents_count} document(s)"
    )
    if document_filenames:
        logging.info(f"Document filenames: {', '.join(document_filenames)}")

    if not conversation_text:
        return None

    # 1️⃣ Call LLM with attachment context
    print("Calling LLM...")
    prompt = build_analysis_prompt(
        conversation_text, 
        truck_photos_count=truck_photos_count,
        documents_count=documents_count,
        document_filenames=document_filenames or [],
        current_status=current_status  # ← ADD THIS
    )
    print("PROMPT: ", prompt)
    llm_result = await call_llm(prompt, provider)
    print("RESPONSE FROM LLM: ", llm_result)
    if not llm_result:
        return None

    # 2️⃣ Fetch CURRENT status from PostgreSQL
    print("Fetching CURRENT status from PostgreSQL")
    current_status = await fetch_current_status(record_id)
    print("Current status: ", current_status)

    # 3️⃣ Decide FINAL status (AUTHORITY LAYER)
    print("Deciding FINAL status (AUTHORITY LAYER)")
    final_status = decide_status(
        llm_output=llm_result.get("analysis", {}),
        current_status=current_status,
        #attachments_received=attachments_received,  # Pass attachment context to prevent "Need Pics" when attachments received
    )
    print("Final status: ", final_status)

    # 4️⃣ Persist status (PostgreSQL → Quickbase)
    # print("Persisting status (PostgreSQL → Quickbase)")
    # if final_status != current_status:
    #     await update_lead_status_pg(record_id, final_status)
    #     # Update Quickbase (returns False on failure, but doesn't raise exception)
    #     qb_success = await update_quickbase_status(record_id, final_status)
    #     if not qb_success:
    #         logging.warning(
    #             f"⚠️  Quickbase status update failed for record_id={record_id}, "
    #             f"but PostgreSQL was updated. Status: '{final_status}'"
    #         )

    # 5️⃣ Return enriched response (for logs/UI)
    print({
        "status_code": 200,
        "record_id": record_id,
        "previous_status": current_status,
        "final_status": final_status,
        "analysis": llm_result,
    })
    return {
        "status_code": 200,
        "record_id": record_id,
        "previous_status": current_status,
        "final_status": final_status,
        "analysis": llm_result,
    }


async def extract_lead_info_from_email(
    body_text: str,
    provider: str = "groq",
) -> Dict[str, Any]:
    """
    Use LLM to extract structured lead info (name, contact, truck details)
    from a seller's email body.
    """
    if not body_text:
        return {}

    prompt = build_lead_info_extraction_prompt(body_text)
    llm_result = await call_llm(prompt, provider)
    if not llm_result:
        return {}

    analysis = llm_result.get("analysis") or {}
    # Ensure we return only the expected keys
    fields = [
        "first_name",
        "last_name",
        "email",
        "phone",
        "state",
        "city",
        "year",
        "make",
        "model",
        "engine",
        "transmission",
    ]
    return {k: (analysis.get(k) or "").strip() if isinstance(analysis.get(k), str) else analysis.get(k, "") for k in fields}


async def generate_email_reply(
    conversation_text: str,
    provider: str = "openai",
) -> str:
    """
    Generate a seller-facing reply for Q&A loops.
    """
    prompt = build_reply_prompt(conversation_text)

    if provider == "groq":
        response = await groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        return response.choices[0].message.content

    # default to openai
    response = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content
