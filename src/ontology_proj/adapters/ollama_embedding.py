"""Pinned local Ollama embedding adapter, never calls a generation endpoint."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse
from ontology_proj.application.files import FileStoreError
from ontology_proj.application.dto import CapabilityState, CapabilityStatus
from ontology_proj.application.semantic import QUERY_INSTRUCTION, CHUNK_VERSION, stable, validate_vectors


class OllamaEmbedding:
    def __init__(self, base_url, model, digest, dimension, context=4096, provider='ollama'):
        parsed = urlparse(base_url)
        self.configured = bool(provider == 'ollama' and parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1')
                               and not parsed.username and not parsed.password and model and digest and dimension)
        self.base_url, self.model, self.digest = base_url.rstrip('/'), model, digest
        self.dimension, self.context = dimension, context
        self.profile = stable({'model': model, 'digest': digest, 'dimension': dimension, 'context': context,
                               'instruction': QUERY_INSTRUCTION, 'chunks': CHUNK_VERSION, 'normalization': 'l2'})
        self.calls = 0

    def _call(self, path, body=None):
        if not self.configured: raise FileStoreError('EMBEDDING_NOT_CONFIGURED', 'Local embedding configuration is incomplete')
        request = Request(self.base_url+path, data=None if body is None else json.dumps(body).encode(),
                          headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=120 if body is not None else 5) as response: return json.load(response)
        except HTTPError as exc:
            code = 'EMBEDDING_INPUT_REJECTED' if exc.code == 400 else 'EMBEDDING_UNAVAILABLE'
            raise FileStoreError(code, 'Local embedding request was rejected', retryable=exc.code >= 500) from None
        except (URLError, OSError, ValueError):
            raise FileStoreError('EMBEDDING_UNAVAILABLE', 'Local embedding service is unavailable', retryable=True) from None

    def verify_identity(self):
        response = self._call('/api/tags')
        if not isinstance(response, dict) or not isinstance(response.get('models'), list) or any(not isinstance(m, dict) for m in response['models']):
            raise FileStoreError('EMBEDDING_INVALID', 'Embedding metadata response is invalid')
        models = response['models']
        actual = next((m for m in models if m.get('name') == self.model), None)
        if actual is None or actual.get('digest') != self.digest:
            raise FileStoreError('EMBEDDING_MODEL_CHANGED', 'Embedding model content differs from pinned configuration')

    def embed(self, texts, *, query=False):
        self.verify_identity()
        if not isinstance(texts, list) or not texts or len(texts) > 16 or any(not isinstance(t, str) or not t for t in texts):
            raise FileStoreError('INVALID_REQUEST', 'Embedding batch is invalid')
        inputs = ['Instruct: '+QUERY_INSTRUCTION+'\nQuery: '+t for t in texts] if query else texts
        self.calls += 1
        result = self._call('/api/embed', {'model': self.model, 'input': inputs, 'truncate': False,
                                         'options': {'num_ctx': self.context}, 'keep_alive': '5m'})
        self.verify_identity()
        if not isinstance(result, dict): raise FileStoreError('EMBEDDING_INVALID', 'Embedding response is invalid')
        if result.get('model') != self.model: raise FileStoreError('EMBEDDING_MODEL_CHANGED', 'Embedding response model differs')
        return validate_vectors(result.get('embeddings'), len(texts), self.dimension)

    def check(self):
        if not self.configured: return CapabilityStatus(CapabilityState.NOT_CONFIGURED, False)
        try:
            self.verify_identity()
            return CapabilityStatus(CapabilityState.READY, True)
        except FileStoreError as exc:
            return CapabilityStatus(CapabilityState.UNAVAILABLE, True, exc.code)

