"""API routes. Run locally with python main.py or uvicorn main:app."""

from contextlib import asynccontextmanager
from pathlib import Path
import sqlite3
import os
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Response, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.api.schemas import (
    IngestionInput, IngestionOutput, JobInput, JobOutput, JobPage,
    JobStatus, MatchRequest, MatchResult, Profile, TrackingUpdate,
)
from src.db import database as db
from src.jobs.fetch import FetchError, SOURCES, fetch_jobs
from src.jobs.models import Job
from src.jobs.parser import ParseError, parse_jobs
from src.telegram import store as telegram_store
from src.setup import store as setup_store
from src.setup.routes import router as setup_router
from src.applications import store as application_store


def create_app(db_path: Path = db.DATABASE_PATH, start_workers: bool = False) -> FastAPI:
    # A separate path lets tests use temporary databases instead of personal data.
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.initialize_database(db_path)
        telegram_store.initialize(db_path)
        setup_store.initialize(db_path)
        application_store.initialize(db_path)
        db_path.chmod(0o600)
        runtime = None
        if start_workers:
            from src.runtime import Runtime
            runtime = Runtime(db_path)
            runtime.start()
        try:
            yield
        finally:
            if runtime:
                runtime.close()

    app = FastAPI(
        title='Apply Agent', version='0.1.0', lifespan=lifespan,
        description='Local job ingestion and tracking. Matching is reserved for a future phase.',
    )
    allowed_hosts = ['localhost', '127.0.0.1', '[::1]', 'testserver']
    allowed_hosts += [host.strip() for host in os.environ.get('APPLY_AGENT_HOSTS', '').split(',') if host.strip()]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.middleware('http')
    async def local_access(request: Request, call_next):
        # Prevent other websites from issuing local mutations through the user's browser.
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and urlparse(origin).netloc != request.headers.get('host')):
                return JSONResponse({'detail': 'Use Apply Agent directly to make changes.'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        if request.url.path not in ('/docs', '/redoc'):
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    web_dir = Path(__file__).resolve().parents[1] / 'web'
    app.mount('/static', StaticFiles(directory=web_dir), name='static')
    app.include_router(setup_router(db_path))

    @app.get('/', include_in_schema=False)
    def home():
        return FileResponse(web_dir / 'index.html')

    @app.get('/health', tags=['System'])
    def health() -> dict[str, str]:
        with db.connect(db_path) as connection:
            connection.execute('SELECT 1').fetchone()
        return {'status': 'ok'}

    @app.get('/sources', tags=['Ingestion'])
    def sources() -> list[dict[str, str]]:
        return [{'id': key, 'url': url} for key, url in SOURCES.items()]

    @app.get('/applications', tags=['Applications'])
    def applications(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        """Approved applications and their browser preparation/review status."""
        return application_store.list_runs(db_path, limit, offset)

    @app.post('/ingestions', response_model=IngestionOutput, status_code=201, tags=['Ingestion'])
    def ingest(body: IngestionInput):
        """Synchronously fetch, parse, and save one source. Send {} for USA internships."""
        url = SOURCES[body.source]
        try:
            jobs = parse_jobs(fetch_jobs(url), source=url)
        except (FetchError, ParseError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return db.record_ingestion(jobs, url, db_path)

    @app.get('/ingestions', response_model=list[IngestionOutput], tags=['Ingestion'])
    def ingestions(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        """List successful imports, newest first. Failed imports return 502 and write nothing."""
        return db.list_ingestions(db_path, limit, offset)

    @app.get('/ingestions/{ingestion_id}', response_model=IngestionOutput, tags=['Ingestion'])
    def ingestion(ingestion_id: int):
        result = db.get_ingestion(ingestion_id, db_path)
        if result is None:
            raise HTTPException(404, 'Ingestion not found.')
        return result

    @app.get('/jobs', response_model=JobPage, tags=['Jobs'])
    def jobs(
        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
        q: str = Query('', max_length=300), status: JobStatus | None = None,
    ):
        """Search company, title, or location; optionally filter by tracking status."""
        return db.list_jobs(db_path, limit, offset, q, status)

    @app.post('/jobs', response_model=JobOutput, status_code=201, tags=['Jobs'])
    def add_job(body: JobInput):
        try:
            return db.create_job(Job(**body.model_dump(mode='json')), db_path)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, 'A job with this application URL already exists.') from exc

    @app.get('/jobs/{job_id}', response_model=JobOutput, tags=['Jobs'])
    def job(job_id: int):
        result = db.get_job(job_id, db_path)
        if result is None:
            raise HTTPException(404, 'Job not found.')
        return result

    @app.put('/jobs/{job_id}/tracking', response_model=JobOutput, tags=['Jobs'])
    def tracking(job_id: int, body: TrackingUpdate):
        """Replace status and notes. 'applied' records a manual action; it never submits anything."""
        result = db.update_tracking(job_id, body.status, body.notes, db_path)
        if result is None:
            raise HTTPException(404, 'Job not found.')
        return result

    @app.delete('/jobs/{job_id}', status_code=204, tags=['Jobs'])
    def remove_job(job_id: int):
        """Delete a local record. A future import may recreate it; use skipped to keep it."""
        if not db.delete_job(job_id, db_path):
            raise HTTPException(404, 'Job not found.')
        return Response(status_code=204)

    @app.get('/profile', response_model=Profile, tags=['Profile'])
    def profile():
        result = db.get_profile(db_path)
        if result is None:
            raise HTTPException(404, 'No profile saved yet.')
        return result

    @app.put('/profile', response_model=Profile, tags=['Profile'])
    def replace_profile(body: Profile):
        """Replace the single local profile. Resume text is supplied directly; no PDF extraction."""
        return db.save_profile(body.model_dump(), db_path)

    @app.delete('/profile', status_code=204, tags=['Profile'])
    def remove_profile():
        db.delete_profile(db_path)
        return Response(status_code=204)

    @app.post('/matches', response_model=list[MatchResult], tags=['Matching'],
              responses={501: {'description': 'Matching is not implemented yet.'}})
    def matches(body: MatchRequest):
        """Reserved contract: compare selected job IDs with the saved profile. Always returns 501."""
        raise HTTPException(501, 'Matching is not implemented yet. No scores were generated.')

    return app
