"""FastAPI serves packaged assets and a read-only trace repository."""
from pathlib import Path
import hashlib

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import create_router
from .trace.repository import Repository


STATIC = Path(__file__).parent / 'static'


def create_app(database):
    """Create an app for an already indexed trace; no ROS node is initialized."""
    app = FastAPI(title='ROS 2 Trace Viewer', docs_url=None, redoc_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.include_router(create_router(Repository(database)))
    # Version the entire module directory, including relative JS imports, so a
    # browser cannot combine an older shared module with a newer entry point.
    assets = hashlib.sha256()
    for path in sorted(STATIC.iterdir()):
        if path.is_file():
            assets.update(path.name.encode())
            assets.update(path.read_bytes())
    asset_path = '/assets/' + assets.hexdigest()[:16]
    index_html = (STATIC / 'index.html').read_text().replace('/static/',asset_path+'/')

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'")
        return response

    @app.get('/')
    def index():
        return HTMLResponse(index_html)

    app.mount(asset_path,StaticFiles(directory=STATIC),name='versioned_assets')
    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    return app
