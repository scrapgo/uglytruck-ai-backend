from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import uvicorn
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()
#from app.routers.twilio_routes import router as twilio_router
#from app.routers.mailchimp_routes import router as mailchimp_router
from app.routers.communication import router as communication_router
from app.routers.database import router as database_router
from app.routers.quickbase import router as quickbase_router
from app.routers.models import router as ai_router
from app.routers.agent import router as agent_router
from app.routers.analytics import router as analytics_router
from app.routers.authenticate import router as auth_router
from app.routers.llm import router as llm_router
from app.routers.webhooks import router as webhooks_router

# Creates all tables (including users) in UglyTruck DB
from app.database.database import Base, engine
Base.metadata.create_all(bind=engine)

app = FastAPI(title="UglyTruck AI Automation Services")

# ✅ Define allowed origins (frontend URLs)
# origins = [     # if using subdomain
#     "http://localhost:8081",         # for local dev
# ]

# ✅ Apply CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],           # 🔒 no "*", use explicit origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
#app.include_router(twilio_router, prefix="/twilio", tags=["Twilio"])
#app.include_router(mailchimp_router, prefix="/mailchimp", tags=["Mailchimp"])
app.include_router(communication_router, prefix="/communication", tags=["Communication"])
app.include_router(database_router, prefix="/database", tags=["Database"])
app.include_router(quickbase_router, prefix="/quickbase", tags=["Quickbase"])
app.include_router(ai_router, prefix="/ai", tags=["AI"])
app.include_router(agent_router, prefix="/agent", tags=["Agent"])
app.include_router(analytics_router, prefix="/analytics", tags=["Analytics"])
app.include_router(auth_router, prefix="/auth", tags=["Auth"])
app.include_router(llm_router, prefix="/llm", tags=["LLM"])
app.include_router(webhooks_router, prefix="/webhooks", tags=["Webhooks"])

# Bump on each deploy. `GET /health` returns it, so you can confirm which code the server runs.
APP_VERSION = "2026-10-01.4-photo-sync"
STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")

def _git_commit() -> str:
    """Short git hash of the running code, or 'unknown' (e.g. inside Docker where .git is excluded)."""
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL, timeout=3
        ).decode().strip() or "unknown"
    except Exception:
        return os.getenv("GIT_COMMIT", "unknown")

GIT_COMMIT = _git_commit()

@app.get("/health")
def health_check():
    return {"status": "ok", "version": APP_VERSION, "commit": GIT_COMMIT, "started_at": STARTED_AT}


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")# default to 8000 if not set
    print(f"🚀 Starting UglyTruck backend on port {port}")
    uvicorn.run("app.backend.main:app", host=host, port=port, reload=True)

