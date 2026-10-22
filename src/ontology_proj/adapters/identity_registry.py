"""Load an explicitly configured local registry with fixed bytes and root."""
import hashlib
import json
from ontology_proj.application.files import FileStoreError


def load_registry(settings):
    path = settings.identity_registry_path
    if path is None:
        if settings.identity_registry_sha256:
            raise FileStoreError('REGISTRATION_NOT_CONFIGURED', 'Registry path is required')
        return None
    try:
        resolved = path.resolve()
        resolved.relative_to(settings.data_root.resolve())
        if path.is_symlink() or not resolved.is_file() or not settings.identity_registry_sha256:
            raise ValueError
        raw = resolved.read_bytes()
        if hashlib.sha256(raw).hexdigest() != settings.identity_registry_sha256:
            raise ValueError
        registry = json.loads(raw)
        if registry.get('status') != 'confirmed_by_user':
            raise ValueError
        return registry
    except (OSError, ValueError, TypeError, AttributeError):
        raise FileStoreError('REGISTRATION_INVALID', 'Registry bytes, root or confirmation differ') from None
