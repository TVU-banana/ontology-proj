"""Local-only command interface; processing rules live in application use cases."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from ontology_proj.application.dto import ImportContext
from ontology_proj.application.files import FileStoreError
from ontology_proj.domain.source import DocumentKind, SourceKind


def _write(value, *, failed=False):
    print(json.dumps(value, ensure_ascii=False), file=sys.stderr if failed else sys.stdout)


class _Arguments(argparse.ArgumentParser):
    def error(self, message):
        # argparse messages may echo input values, paths or credentials.
        _write({'error': {'code': 'INVALID_ARGUMENT', 'message': 'Check required arguments and allowed values with --help'}}, failed=True)
        raise SystemExit(2)


def arguments():
    parser = _Arguments(description='Local PDF preparation and parsing; no graph registration')
    parser.add_argument('--env-file', help='Explicit settings file; none loaded by default')
    parser.add_argument('--data-root', type=Path, help='Override DATA_ROOT for this invocation')
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare', help='Save PDF bytes and explicit source context')
    prepare.add_argument('--pdf', required=True, type=Path)
    prepare.add_argument('--kind', required=True, choices=[k.value for k in DocumentKind])
    prepare.add_argument('--source-kind', required=True, choices=[k.value for k in SourceKind])
    prepare.add_argument('--bridge-id', default=None, help='Known bridge ID; omit when unknown')
    parse = commands.add_parser('parse', help='Parse a saved document; failures require explicit retry')
    parse.add_argument('--document-id', required=True)
    parse.add_argument('--retry', action='store_true')
    read = commands.add_parser('read', help='Read verified published artifacts, never convert')
    read.add_argument('--document-id', required=True)
    read.add_argument('--artifact', choices=['manifest', 'docling', 'normalized'], default='manifest')
    extract = commands.add_parser('extract', help='Extract and validate a saved normalized document')
    extract.add_argument('--document-id', required=True)
    extract.add_argument('--fresh', action='store_true', help='Explicit new independent attempt, preserve previous outcomes')
    business = commands.add_parser('read-business', help='Read the verified local business publication summary')
    business.add_argument('--document-id', required=True)
    business.add_argument('--artifact', choices=['manifest', 'candidate', 'validation', 'records'], default='manifest')
    return parser


def main(argv=None, *, service_factory, business_service_factory=None) -> int:
    args = arguments().parse_args(argv)
    try:
        context = None
        if args.command == 'prepare':
            context = ImportContext(DocumentKind(args.kind), SourceKind(args.source_kind), args.bridge_id)
            try:
                data = args.pdf.read_bytes()
            except OSError:
                raise FileStoreError('INPUT_UNAVAILABLE', 'Input PDF cannot be read') from None
        if args.command in ('extract', 'read-business'):
            service = business_service_factory(args.env_file, args.data_root, args.command == 'read-business')
            result = service.read(args.document_id, args.artifact) if args.command == 'read-business' else service.execute(args.document_id, fresh=args.fresh)
            failed = result.get('status') in ('rejected', 'failed')
            _write(result, failed=failed)
            return 1 if failed else 0
        service = service_factory(args.env_file, args.data_root)
        if args.command == 'prepare':
            saved = service.prepare(data, args.pdf.name, context)
            result = {'scope': 'local_parse', 'duplicate': saved.duplicate, 'source': asdict(saved.source)}
        elif args.command == 'parse':
            result = service.process(args.document_id, retry=args.retry)
            if result.get('operation', {}).get('status') in ('failed', 'interrupted'):
                _write({'scope': 'local_parse', **result}, failed=True)
                return 1
            result = {'scope': 'local_parse', **result}
        elif args.artifact == 'manifest':
            result = service.read(args.document_id)
        else:
            result = service.store.read_artifact(args.document_id, args.artifact)
        _write(result)
        return 0
    except FileStoreError as error:
        _write({'error': {'code': error.code, 'message': str(error), 'retryable': error.retryable}}, failed=True)
        return 2 if error.code in {'INVALID_CONFIGURATION', 'CONFIG_FILE_UNAVAILABLE'} else 1
    except ValueError:
        _write({'error': {'code': 'INVALID_ARGUMENT', 'message': 'Import context or argument is invalid'}}, failed=True)
        return 2
    except KeyboardInterrupt:
        _write({'error': {'code': 'LOCAL_PROCESS_INTERRUPTED', 'message': 'Interrupted; recover after the previous process stops'}}, failed=True)
        return 130
    except Exception:
        _write({'error': {'code': 'LOCAL_COMMAND_FAILED', 'message': 'Local command failed; inspect retained operation diagnostics'}}, failed=True)
        return 1
