"""Request correlation without logging request bodies or credentials."""
import json
import logging
import time
import uuid

logger = logging.getLogger('uvicorn.error')


def install_observability(app):
    @app.middleware('http')
    async def request_log(request, call_next):
        request_id = uuid.uuid4().hex
        start = time.monotonic()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers['X-Request-ID'] = request_id
            if (request.url.path.endswith(('.js', '.css')) or
                    response.headers.get('content-type', '').startswith('text/html')):
                response.headers['Cache-Control'] = 'no-cache'
            return response
        finally:
            logger.info(json.dumps({
                'event': 'http_request', 'request_id': request_id,
                'method': request.method, 'path': request.url.path,
                'status': status, 'duration_ms': round((time.monotonic() - start) * 1000, 2),
            }, ensure_ascii=False))
