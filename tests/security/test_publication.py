"""Security boundary tests; run with stdlib unittest, without application SDKs."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('publication', ROOT / 'scripts/export_publication.py')
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)
POLICY = json.loads((ROOT / '.publication.json').read_text())


class PublicationBoundary(unittest.TestCase):
    def test_private_files_refused_even_under_allowed_source_roots(self):
        for path in ['.env', 'src/.env.prod', 'deploy/runtime.env', 'deploy/runtime.env.old',
                     'src/private.key', 'tests/credentials.json', 'src/id_ed25519',
                     'src/data/request.json', 'docs/audits/input.json',
                     'specs/008-system-acceptance/results/request.json',
                     'docs/ui-ux/screenshot.png', 'input-pdfs/report.pdf',
                     '../README.md', '/tmp/README.md', 'unknown/file.py']:
            with self.subTest(path=path):
                self.assertFalse(publication.allowed(path, POLICY))

    def test_source_and_reviewed_synthetic_fixtures_allowed(self):
        for path in ['.env.example', 'deploy/.env.example', 'src/ontology_proj/settings.py',
                     'samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf',
                     *POLICY['synthetic_fixtures']]:
            with self.subTest(path=path):
                self.assertTrue(publication.allowed(path, POLICY))

    def test_redaction_removes_operator_metadata_preserves_public_configuration(self):
        raw = (b'/' + b'Users/' + b'private-person/project http://' +
               b'private-laptop' + b'.local:7863 http://' + b'192.168.' +
               b'1.2:80 person' + b'@private.test http://127.0.0.1:8000')
        value = publication.sanitize('README.md', raw)
        for marker in [b'private-person', b'private-laptop', b'192.168.' + b'1.2', b'person' + b'@private.test']:
            self.assertNotIn(marker, value)
        self.assertIn(b'http://127.0.0.1:8000', value)

    def test_export_never_copies_history_untracked_secrets_or_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'; root.mkdir()
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / 'README.md').write_text('safe')
            (root / '.env').write_text('PRIVATE_CANARY=must-stay-local')
            subprocess.run(['git', '-C', str(root), 'add', 'README.md', '.env'], check=True)
            output = Path(tmp) / 'release'
            report = publication.export(root, output, POLICY)
            self.assertEqual((output / 'README.md').read_text(), 'safe')
            self.assertFalse((output / '.git').exists())
            self.assertFalse((output / '.env').exists())
            self.assertIn('.env', report['excluded'])
            with self.assertRaises(FileExistsError):
                publication.export(root, output, POLICY)
            (root / 'README.md').unlink()
            (root / 'README.md').symlink_to(root / '.env')
            with self.assertRaisesRegex(ValueError, 'regular file'):
                publication.export(root, Path(tmp) / 'symlink-release', POLICY)

    def test_missing_pdf_runtime_or_synthetic_fixtures_blocks_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            errors = publication.check(root, POLICY)
            self.assertTrue(any('pdf.worker.mjs' in error for error in errors))
            for fixture in POLICY['synthetic_fixtures']:
                self.assertIn(f'required file is not tracked: {fixture}', errors)


if __name__ == '__main__':
    unittest.main()
