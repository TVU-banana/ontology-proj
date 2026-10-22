import ast
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src"


def test_core_imports_without_site_packages_or_external_sdks():
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import ontology_proj.domain.source; "
        "import ontology_proj.application.dto; "
        "import ontology_proj.application.readiness; "
        "import ontology_proj.application.files; "
        "import ontology_proj.application.ports.file_store; "
        "import ontology_proj.application.parsing; "
        "import ontology_proj.application.ports.document_parser; "
        "import ontology_proj.application.ports.document_normalizer; "
        "import ontology_proj.application.local_parsing; "
        "import ontology_proj.application.ports.local_processing_store; "
        "import ontology_proj.domain.business; "
        "import ontology_proj.application.business_extraction; "
        "import ontology_proj.application.ports.business_extraction; "
        "import ontology_proj.domain.graph_mapping; "
        "import ontology_proj.application.ingestion; "
        "import ontology_proj.application.ports.graph_repository; "
        "import ontology_proj.application.query; "
        "import ontology_proj.application.ports.query_repository; "
        "import ontology_proj.application.semantic; "
        "import ontology_proj.application.ports.semantic; "
        "import ontology_proj.application.answer; "
        "import ontology_proj.application.ports.answer; "
        "import ontology_proj.application.report; "
        "import ontology_proj.application.ports.report"
    )
    result = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(SOURCE)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_dependencies_point_toward_the_core():
    for layer in ("domain", "application", "interfaces"):
        for path in (SOURCE / "ontology_proj" / layer).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    assert node.level == 0, f"Resolve relative imports explicitly: {path}"
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if layer in ("domain", "application"):
                        if name.startswith("ontology_proj."):
                            permitted = ("ontology_proj.domain",) if layer == "domain" else ("ontology_proj.domain", "ontology_proj.application")
                            assert name.startswith(permitted), (path, name)
                        else:
                            assert name.split(".")[0] in sys.stdlib_module_names, (path, name)
                            assert name.split(".")[0] not in {"os", "pathlib", "subprocess", "socket", "tempfile"}, (path, name)
                    if layer == "interfaces":
                        assert not name.startswith(("ontology_proj.adapters", "ontology_proj.settings", "ontology_proj.bootstrap")), (path, name)
                        assert name.split(".")[0] not in {"docling", "neo4j", "openai", "httpx", "requests"}, (path, name)
