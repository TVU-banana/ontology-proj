from contextlib import asynccontextmanager
from dataclasses import asdict
from uuid import uuid4

import json
from fastapi import FastAPI, Request, UploadFile, File, Form, Header, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from ontology_proj.application.readiness import CheckReadiness
from ontology_proj.application.dto import ImportContext
from ontology_proj.application.files import FileStoreError
from ontology_proj.domain.source import SourceKind, DocumentKind


class SourceMeta(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: DocumentKind
    source_kind: SourceKind
    bridge_id: str | None = None


class RetryBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    stage: str = 'failed_stage'


def create_http_app(readiness: CheckReadiness, ingestion=None, lookup=None, evidence_reader=None, static_directory=None, semantic=None, indexer=None, context=None, answer=None, reports=None, agents=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.readiness = readiness
        if ingestion:
            try:
                ingestion.start()
            except FileStoreError as error:
                if error.code == 'PROCESS_ALREADY_RUNNING':
                    raise
                app.state.ingestion_start_error = error.code
        if reports:
            reports.start()
        try:
            yield
        finally:
            if reports:
                reports.close()
            if ingestion:
                ingestion.close()
            del app.state.readiness

    app = FastAPI(title="Ontology bridge MVP", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Trace-ID"] = request.state.trace_id
        return response

    @app.get("/health")
    def health(request: Request):
        return {"status": "ok", "trace_id": request.state.trace_id}

    @app.get("/ready", responses={503: {"description": "Base capabilities not ready"}})
    def ready(request: Request):
        report = request.app.state.readiness.execute()
        return JSONResponse(
            {**asdict(report), "trace_id": request.state.trace_id},
            status_code=200 if report.ready else 503,
        )

    @app.exception_handler(FileStoreError)
    async def storage_error(request: Request, error: FileStoreError):
        code = error.code
        status = 503 if code in {'GRAPH_UNAVAILABLE', 'FILE_STORE_UNAVAILABLE', 'MODEL_NOT_CONFIGURED', 'EMBEDDING_NOT_CONFIGURED', 'EMBEDDING_UNAVAILABLE', 'INDEX_NOT_READY'} else 404 if code in {'OBJECT_NOT_FOUND', 'SOURCE_NOT_FOUND'} else 410 if code == 'ARTIFACT_MISSING' else 413 if code == 'UPLOAD_TOO_LARGE' else 422 if code in {'INVALID_REQUEST', 'INVALID_PDF', 'PAGE_LIMIT_EXCEEDED', 'INVALID_DOCUMENT_ID', 'INVALID_SOURCE_INPUT'} else 409
        if code=='MODEL_TRANSPORT_FAILED':status=503
        if code=='MODEL_OUTPUT_INVALID':status=502
        payload={'error': {'code': code, 'message': str(error), 'retryable': error.retryable, 'stage': None, 'details': {}}, 'trace_id': request.state.trace_id, 'operation_id': None}
        if hasattr(error,'question_id'):payload['question_id']=error.question_id
        return JSONResponse(payload,status_code=status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error):
        return await storage_error(request, FileStoreError('INVALID_REQUEST', 'Request fields are invalid'))

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error):
        return JSONResponse({'error': {'code':'INTERNAL_ERROR', 'message':'Request could not be completed', 'retryable':False, 'stage':None, 'details':{}}, 'trace_id':request.state.trace_id, 'operation_id':None},status_code=500)

    if agents:
        @app.post('/api/agents/ingestion')
        def ingestion_agent(body: dict, request: Request):
            return {**agents.execute('ingestion', body), 'trace_id': request.state.trace_id}

        @app.post('/api/agents/knowledge')
        def knowledge_agent(body: dict, request: Request):
            return {**agents.execute('knowledge', body), 'trace_id': request.state.trace_id}

    if ingestion:
        @app.get('/api/bridges')
        def bridges(request: Request):
            return {'bridges':ingestion.bridges(),'trace_id':request.state.trace_id}

        @app.post('/api/bridges')
        def register_bridge(body:dict,request:Request):
            return {**ingestion.register_bridge(body),'trace_id':request.state.trace_id}

        @app.get('/api/documents/{document_id}/bridge-resolution')
        def bridge_resolution(document_id:str,request:Request):
            return {**ingestion.resolve_bridge(document_id,read_only=True),'trace_id':request.state.trace_id}

        @app.post('/api/documents/{document_id}/bridge-resolution')
        def confirm_bridge(document_id:str,body:dict,request:Request):
            if set(body)-{'selected_bridge_id','confirm_details'}:raise FileStoreError('INVALID_REQUEST','Unknown bridge resolution fields')
            return {**ingestion.resolve_bridge(document_id,body.get('selected_bridge_id'),confirm_details=body.get('confirm_details',False)),'trace_id':request.state.trace_id}

        @app.post('/api/documents')
        async def upload(request: Request, file: UploadFile = File(...), meta: str = Form(...), key: str | None = Header(None, alias='Idempotency-Key')):
            try:
                fields = SourceMeta.model_validate_json(meta)
                context = ImportContext(fields.kind, fields.source_kind, fields.bridge_id)
            except (ValueError, TypeError):
                raise FileStoreError('INVALID_REQUEST', 'Source metadata is invalid') from None
            data = await file.read(ingestion.max_upload_bytes+1)
            # File validation, hashing and graph writes use the thread pool.
            from starlette.concurrency import run_in_threadpool
            result = await run_in_threadpool(ingestion.upload, data, file.filename or 'source.pdf', context, key)
            return JSONResponse({**result, 'trace_id': request.state.trace_id}, status_code=200 if result['duplicate'] else 201)

        @app.post('/api/documents/{document_id}/process', status_code=202)
        def process(document_id: str, request: Request, key: str | None = Header(None, alias='Idempotency-Key')):
            return {**ingestion.request_processing(document_id, key), 'trace_id': request.state.trace_id}

        @app.post('/api/documents/{document_id}/retry', status_code=202)
        def retry(document_id: str, body: RetryBody, request: Request, key: str | None = Header(None, alias='Idempotency-Key')):
            return {**ingestion.request_processing(document_id, key, retry=True, stage=body.stage), 'trace_id': request.state.trace_id}

        @app.get('/api/documents/{document_id}')
        def document(document_id: str, request: Request):
            result = ingestion.document(document_id)
            result.pop('pdf_key', None)
            result['counts']={name:result.get(name+'_count',0) for name in ('candidate','accepted','pending','rejected','located_block')}
            for field in ('bridge_id','parsed_key','normalized_key','page_count','error_code'):
                result.setdefault(field,None)
            return {**result, 'trace_id': request.state.trace_id}

        @app.get('/api/operations/{operation_id}')
        def operation(operation_id: str, request: Request):
            return {**ingestion.operation(operation_id), 'trace_id': request.state.trace_id}

        @app.get('/api/documents/{document_id}/file')
        def pdf(document_id: str):
            return Response(ingestion.pdf(document_id), media_type='application/pdf')

        @app.get('/api/documents/{document_id}/parsed')
        def artifact(document_id: str, request: Request, artifact: str = Query('normalized')):
            return {'artifact': ingestion.artifact(document_id, artifact), 'trace_id': request.state.trace_id}

    if lookup:
        @app.post('/api/graph/lookup')
        def graph_lookup(body: dict, request: Request):
            return {**lookup.execute(body), 'trace_id': request.state.trace_id}

    if evidence_reader:
        @app.get('/api/evidence/{evidence_id}')
        def evidence(evidence_id: str, request: Request):
            return {**evidence_reader.read(evidence_id), 'trace_id': request.state.trace_id}

    if indexer:
        @app.post('/api/documents/{document_id}/index')
        def index_document(document_id: str, body: dict, request: Request):
            if set(body)-{'retry'}:
                raise FileStoreError('INVALID_REQUEST', 'Unknown index fields')
            return {**indexer.execute(document_id, body.get('retry', False)), 'trace_id': request.state.trace_id}

    if semantic:
        @app.post('/api/semantic/search')
        def semantic_search(body: dict, request: Request):
            if 'graph_query' in body: raise FileStoreError('INVALID_REQUEST', 'Use context for graph query')
            return {**semantic.execute(body), 'trace_id': request.state.trace_id}

    if context:
        @app.post('/api/context')
        def context_bundle(body: dict, request: Request):
            return {**context.execute(body), 'trace_id': request.state.trace_id}

    if static_directory:
        app.mount('/static', StaticFiles(directory=static_directory), name='static')

        @app.get('/')
        def workspace_page():
            return FileResponse(str(static_directory) + '/workspace.html')

        @app.get('/query')
        def query_page():
            return FileResponse(str(static_directory) + '/query.html')

    if answer:
        @app.post('/api/answers')
        def answer_question(body: dict, request: Request):
            return {**answer.execute(body), 'trace_id': request.state.trace_id}

    @app.post('/api/records/{record_id}/review')
    def record_review(record_id: str, request: Request):
        return JSONResponse({'error': {'code': 'REVIEW_NOT_IMPLEMENTED', 'message': 'Record review is not implemented', 'retryable': False, 'stage': None, 'details': {}}, 'review_status': 'not_run', 'trace_id': request.state.trace_id, 'operation_id': None}, status_code=501)

    if reports:
        @app.post('/api/reports', status_code=202)
        def create_report(body: dict, request: Request):
            return {**reports.submit(body), 'trace_id': request.state.trace_id}

        @app.get('/api/reports/{report_id}')
        def report_data(report_id: str, request: Request):
            return {**reports.get(report_id), 'trace_id': request.state.trace_id}

        @app.get('/api/reports/{report_id}/markdown')
        def report_markdown(report_id: str):
            md, changes = reports.get(report_id, markdown=True)
            return Response(md, media_type='text/markdown', headers={'X-Citation-Status': 'changed' if changes else 'unchanged'})

        @app.post('/api/reports/{report_id}/retry', status_code=202)
        def retry_report(report_id: str, request: Request):
            return {**reports.retry(report_id), 'trace_id': request.state.trace_id}

        @app.post('/api/reports/{report_id}/professional-review')
        def professional_review(report_id: str, request: Request):
            reports.get(report_id)
            return JSONResponse({'error': {'code': 'REVIEW_NOT_IMPLEMENTED', 'message': 'Professional review is not implemented', 'retryable': False, 'stage': None, 'details': {}}, 'review_status': 'not_run', 'trace_id': request.state.trace_id, 'operation_id': None}, status_code=501)

    if static_directory and evidence_reader:
        @app.get('/evidence/{evidence_id}')
        def evidence_page(evidence_id: str):
            evidence_reader.read(evidence_id)
            return FileResponse(str(static_directory) + '/query.html')

    return app
