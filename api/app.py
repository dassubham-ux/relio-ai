from __future__ import annotations

import threading
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.fetcher import fetch_website_sync, pages_to_text
from agent.finder import find_subreddits
from agent.researcher import research_company
from agent.storage import MONGO_URI, DB_NAME, upsert_brief, upsert_subreddit_map

app = FastAPI(title="Relio AI", version="1.0.0")

# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------

jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _set_job(job_id: str, **kwargs: object) -> None:
    with _lock:
        jobs[job_id].update(kwargs)


def _new_job() -> str:
    job_id = uuid.uuid4().hex
    with _lock:
        jobs[job_id] = {"job_id": job_id, "status": "queued", "result": None, "error": None}
    return job_id


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ResearchRequest(BaseModel):
    url: str
    paste_text: str | None = None


class SubredditsRequest(BaseModel):
    domain: str


class ThreadRequest(BaseModel):
    domain: str
    subreddit: str  # with or without r/ prefix


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    from pymongo import MongoClient
    mongo_status = "ok"
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        client.server_info()
    except Exception:
        mongo_status = "error"
    return {"status": "ok", "mongo": mongo_status}


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

def _run_research(job_id: str, url: str, paste_text: str | None) -> None:
    try:
        _set_job(job_id, status="running")

        if paste_text:
            website_text = paste_text
        else:
            pages = fetch_website_sync(url)
            website_text = pages_to_text(pages)

        brief = research_company(url, website_text)
        brief_dict = brief.model_dump()
        upsert_brief(brief_dict)

        _set_job(job_id, status="completed", result=brief_dict)
    except Exception as exc:
        _set_job(job_id, status="failed", error=str(exc))


def _run_subreddits(job_id: str, domain: str) -> None:
    try:
        _set_job(job_id, status="running")

        from pymongo import MongoClient
        client = MongoClient(MONGO_URI)
        doc = client[DB_NAME]["company_briefs"].find_one({"domain": domain})
        if not doc:
            raise ValueError(f"No CompanyBrief found for domain '{domain}'. Run /api/v1/research first.")

        doc.pop("_id", None)

        from agent.models import CompanyBrief
        brief = CompanyBrief.model_validate(doc)

        subreddit_map = find_subreddits(brief, domain)
        map_dict = subreddit_map.model_dump()
        upsert_subreddit_map(map_dict)

        _set_job(job_id, status="completed", result=map_dict)
    except Exception as exc:
        _set_job(job_id, status="failed", error=str(exc))


def _run_threads(job_id: str, domain: str, subreddit: str) -> None:
    try:
        _set_job(job_id, status="running")

        from pymongo import MongoClient
        client = MongoClient(MONGO_URI)
        doc = client[DB_NAME]["company_briefs"].find_one({"domain": domain})
        if not doc:
            raise ValueError(f"No CompanyBrief found for domain '{domain}'. Run /api/v1/research first.")
        doc.pop("_id", None)

        from agent.models import CompanyBrief
        brief = CompanyBrief.model_validate(doc)

        from agent.thread_finder import find_threads
        result = find_threads(brief, domain, subreddit)
        result_dict = result.model_dump()

        from agent.storage import upsert_thread_search
        upsert_thread_search(result_dict)

        _set_job(job_id, status="completed", result=result_dict)
    except Exception as exc:
        _set_job(job_id, status="failed", error=str(exc))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/v1/research", status_code=202)
def start_research(req: ResearchRequest) -> dict:
    job_id = _new_job()
    t = threading.Thread(target=_run_research, args=(job_id, req.url, req.paste_text), daemon=True)
    t.start()
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/v1/subreddits", status_code=202)
def start_subreddits(req: SubredditsRequest) -> dict:
    job_id = _new_job()
    t = threading.Thread(target=_run_subreddits, args=(job_id, req.domain), daemon=True)
    t.start()
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with _lock:
        job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


@app.get("/api/v1/briefs")
def list_briefs() -> list:
    from pymongo import MongoClient
    client = MongoClient(MONGO_URI)
    docs = client[DB_NAME]["company_briefs"].find(
        {},
        {"domain": 1, "metadata": 1, "company_snapshot.what_it_does": 1, "_id": 0},
    ).sort("metadata.researched_at", -1)
    return list(docs)


@app.get("/api/v1/briefs/{domain}")
def get_brief(domain: str) -> dict:
    from pymongo import MongoClient
    client = MongoClient(MONGO_URI)
    doc = client[DB_NAME]["company_briefs"].find_one({"domain": domain})
    if not doc:
        raise HTTPException(status_code=404, detail=f"No brief found for domain '{domain}'")
    doc.pop("_id", None)
    return doc


@app.get("/api/v1/subreddits")
def list_subreddits() -> list:
    from pymongo import MongoClient
    client = MongoClient(MONGO_URI)
    docs = client[DB_NAME]["subreddit_maps"].find(
        {},
        {"domain": 1, "metadata": 1, "_id": 0},
    ).sort("metadata.generated_at", -1)
    return list(docs)


@app.get("/api/v1/subreddits/{domain}")
def get_subreddits(domain: str) -> dict:
    from pymongo import MongoClient
    client = MongoClient(MONGO_URI)
    doc = client[DB_NAME]["subreddit_maps"].find_one({"domain": domain})
    if not doc:
        raise HTTPException(status_code=404, detail=f"No subreddit map found for domain '{domain}'")
    doc.pop("_id", None)
    return doc


@app.post("/api/v1/threads", status_code=202)
def start_threads(req: ThreadRequest) -> dict:
    # Normalize: strip r/ prefix
    subreddit = req.subreddit.removeprefix("r/").strip("/")
    job_id = _new_job()
    t = threading.Thread(target=_run_threads, args=(job_id, req.domain, subreddit), daemon=True)
    t.start()
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/v1/threads/{domain}/{subreddit}")
def get_threads(domain: str, subreddit: str) -> dict:
    from pymongo import MongoClient
    client = MongoClient(MONGO_URI)
    sub_normalized = f"r/{subreddit}" if not subreddit.startswith("r/") else subreddit
    doc = client[DB_NAME]["thread_searches"].find_one({"domain": domain, "subreddit": sub_normalized})
    if not doc:
        raise HTTPException(status_code=404, detail=f"No thread search found for {domain} / {sub_normalized}")
    doc.pop("_id", None)
    return doc


# ---------------------------------------------------------------------------
# Static frontend — mount last so API routes take priority
# ---------------------------------------------------------------------------

app.mount("/", StaticFiles(directory="api/static", html=True), name="static")
