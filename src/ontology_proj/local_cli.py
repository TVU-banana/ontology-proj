"""Composition entry point for the local CLI; HTTP startup remains __main__."""
from pathlib import Path

from pydantic import ValidationError

from ontology_proj.application.files import FileStoreError
from ontology_proj.bootstrap import build_local_parsing, build_business_extraction, build_business_reader
from ontology_proj.interfaces.cli import main as run_cli
from ontology_proj.settings import Settings


def main(argv=None, *, service_factory=None) -> int:
    def settings_for(env_file, data_root):
        if env_file is not None and not Path(env_file).is_file():
            raise FileStoreError('CONFIG_FILE_UNAVAILABLE', 'Explicit settings file is unavailable')
        try:
            settings = Settings(_env_file=env_file, **({'data_root': data_root} if data_root is not None else {}))
        except (ValidationError, OSError):
            raise FileStoreError('INVALID_CONFIGURATION', 'Configuration fields or file are invalid') from None
        return settings
    def build_service(env_file, data_root):
        return (service_factory or build_local_parsing)(settings_for(env_file, data_root))
    def build_business(env_file, data_root, readonly):
        settings = settings_for(env_file, data_root)
        return build_business_reader(settings) if readonly else build_business_extraction(settings)
    return run_cli(argv, service_factory=build_service, business_service_factory=build_business)


if __name__ == '__main__':
    raise SystemExit(main())
