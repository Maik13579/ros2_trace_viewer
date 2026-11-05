"""FastAPI serves packaged assets and a read-only trace repository."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import create_router
from .trace.repository import Repository


STATIC = Path(__file__).parent / 'static'


def create_app(database):
    """Create an app for an already indexed trace; no ROS node is initialized."""
    app = FastAPI(title='ROS 2 Trace Viewer', docs_url=None, redoc_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.include_router(create_router(Repository(database)))

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'")
        return response

    @app.get('/')
    def index():
        return FileResponse(STATIC / 'index.html')

    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    return app
