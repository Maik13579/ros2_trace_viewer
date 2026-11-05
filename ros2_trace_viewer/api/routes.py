"""HTTP API: normalized records only; all endpoints are read-only."""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..trace.filters import Filters, to_ns


def create_router(repository):
    router = APIRouter(prefix='/api')

    def filters(request):
        try:
            return Filters.parse(request.query_params)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get('/trace/info')
    def info():
        return repository.info

    @router.get('/dimensions')
    def dimensions():
        return repository.dimensions()

    @router.get('/timeline')
    def timeline(request: Request, grouping: str = 'node', view_start: str = '0',
                 view_end: str = None, lane_offset: int = Query(0, ge=0),
                 lane_limit: int = Query(24, ge=1, le=64),
                 budget: int = Query(10000, ge=1, le=20000), bins: int = Query(256, ge=16, le=512)):
        active = filters(request)
        try:
            start = to_ns(view_start)
            end = to_ns(view_end) if view_end is not None else int(repository.info['duration_ns'])
            if end < start:
                raise ValueError('Visible interval end must be at least its start')
            return repository.timeline(active, grouping, start, end, lane_offset,
                                       lane_limit, budget, bins)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get('/statistics')
    def statistics(request: Request, level: str = 'process', sort: str = 'total_ns',
                   descending: bool = True, offset: int = Query(0, ge=0),
                   limit: int = Query(100, ge=1, le=500)):
        try:
            return repository.statistics(filters(request), level, sort, descending, offset, limit)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get('/charts')
    def charts(request: Request):
        return repository.charts(filters(request))

    @router.get('/boxplots')
    def boxplots(request: Request, sort: str = 'median_ns', descending: bool = True,
                 offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100)):
        try:
            return repository.boxplots(filters(request), sort, descending, offset, limit)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get('/publications/{publication_id}')
    def publication(publication_id: int, request: Request):
        result = repository.publication(publication_id,filters(request))
        if result is None:
            raise HTTPException(404, 'Publish event not found')
        return result

    @router.get('/timers')
    def timers(request: Request, sort: str = 'total_ns', descending: bool = True,
               offset: int = Query(0,ge=0),limit: int = Query(50,ge=1,le=100)):
        try:
            return repository.timers(filters(request),sort,descending,offset,limit)
        except ValueError as error:
            raise HTTPException(422,str(error)) from error

    @router.get('/services')
    def services(request: Request, service: str = None,
                 offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        return repository.services(filters(request),offset,limit,service)

    @router.get('/topics')
    def topics(request: Request, topic_search: str = '', topic: str = None,
               offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        return repository.topics(filters(request), topic_search[:500], offset, limit, topic)

    @router.get('/locate')
    def locate(request: Request):
        return JSONResponse(repository.locate(filters(request)))

    @router.get('/executions/{execution_id}')
    def execution(execution_id: int, request: Request):
        result = repository.execution(execution_id,filters(request))
        if result is None:
            raise HTTPException(404, 'Execution not found')
        return result

    @router.get('/executions/{execution_id}/publications')
    def publications(execution_id: int, request: Request, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        if repository.execution(execution_id) is None:
            raise HTTPException(404, 'Execution not found')
        return repository.publications(execution_id, offset, limit,filters(request))

    @router.get('/executions/{execution_id}/{direction}')
    def adjacent(request: Request, execution_id: int, direction: str):
        if direction not in ('next', 'previous'):
            raise HTTPException(422, 'Direction must be next or previous')
        return JSONResponse(repository.adjacent(execution_id, direction, filters(request)))

    @router.get('/{kind}')
    def catalog(kind: str, search: str = '', offset: int = Query(0, ge=0),
                limit: int = Query(200, ge=1, le=500), pid: int = None, node: str = None):
        if kind not in ('processes', 'nodes', 'callbacks'):
            raise HTTPException(404, 'Unknown API endpoint')
        if kind == 'processes' and pid is not None:
            raise HTTPException(422, 'pid is only supported for nodes and callbacks')
        return repository.catalog(kind, search, offset, limit, pid, node)

    return router
