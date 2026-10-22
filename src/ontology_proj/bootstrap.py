from pathlib import Path
from ontology_proj.application.agent import RunAgent
from ontology_proj.adapters.agent_provider import RelayAgentProvider
from ontology_proj.adapters.agent_journal import LocalAgentJournal
from ontology_proj.application.query import LookupGraph
from ontology_proj.adapters.graph_queries import Neo4jQueries
from ontology_proj.adapters.evidence_reader import PublishedEvidenceReader
from ontology_proj.adapters.docling_parser import DoclingParser
from ontology_proj.adapters.document_normalizer import DoclingNormalizer
from ontology_proj.adapters.local_processing_store import LocalProcessingStore
from ontology_proj.application.local_parsing import LocalParsingService
from ontology_proj.adapters.readiness import DataDirectoryProbe, UnimplementedProbe
from ontology_proj.application.readiness import CapabilityCheck, CheckReadiness
from ontology_proj.interfaces.http.app import create_http_app
from ontology_proj.settings import Settings
from ontology_proj.adapters.business_store import LocalBusinessStore
from ontology_proj.adapters.business_validation import BusinessValidator
from ontology_proj.adapters.fact_extractor import RelayFactExtractor
from ontology_proj.application.business_extraction import ExtractBusiness, ReadBusiness
from ontology_proj.adapters.neo4j_repository import Neo4jRepository
from ontology_proj.adapters.ingestion_store import IngestionStore, ProcessLease
from ontology_proj.application.ingestion import IngestionService
from ontology_proj.adapters.model_probe import RelayModelProbe
from ontology_proj.adapters.ollama_embedding import OllamaEmbedding
from ontology_proj.adapters.semantic_repository import Neo4jSemantic, SemanticReadiness
from ontology_proj.adapters.semantic_inputs import PublishedSemanticInput
from ontology_proj.application.semantic import IndexDocument, SemanticSearch, BuildContext
from ontology_proj.application.answer import AnswerQuestion
from ontology_proj.adapters.answer_generator import RelayAnswerGenerator
from ontology_proj.adapters.answer_journal import LocalAnswerJournal
from ontology_proj.application.report import GenerateReport
from ontology_proj.adapters.report_repository import Neo4jReports
from ontology_proj.adapters.report_store import LocalReportStore
from ontology_proj.adapters.identity_registry import load_registry
from ontology_proj.adapters.registered_extractor import RegisteredFactExtractor
from ontology_proj.adapters.business_rules import load_rules
from ontology_proj.adapters.bridge_catalog import BridgeCatalog
from ontology_proj.adapters.catalog_extractor import CatalogFactExtractor


def build_validator(settings):
    return BusinessValidator(load_registry(settings),BridgeCatalog(settings.data_root,settings.actor_id))


def build_extractor(settings):
    return CatalogFactExtractor(settings.llm_base_url,settings.llm_model,settings.llm_api_key.get_secret_value(),
                                BridgeCatalog(settings.data_root,settings.actor_id),load_registry(settings),settings.llm_fallback_models)


def build_business_extraction(settings: Settings) -> ExtractBusiness:
    return ExtractBusiness(LocalBusinessStore(settings.data_root), build_extractor(settings), build_validator(settings))


def build_business_reader(settings: Settings) -> ReadBusiness:
    return ReadBusiness(LocalBusinessStore(settings.data_root))


def build_local_parsing(settings: Settings, parser=None) -> LocalParsingService:
    service = LocalParsingService(LocalProcessingStore(settings.data_root), parser if parser is not None else build_parser(settings), build_normalizer())
    service.recover()
    return service


def build_normalizer() -> DoclingNormalizer:
    return DoclingNormalizer()


def build_parser(settings: Settings) -> DoclingParser:
    return DoclingParser(settings.docling_artifacts_path, settings.docling_num_threads,
                         ocr_mode=settings.docling_ocr_mode, ocr_models_path=settings.docling_ocr_models_path,
                         ocr_force_pages=settings.docling_ocr_force_pages)


def build_readiness(settings: Settings, graph=None, parser=None, semantic_probe=None) -> CheckReadiness:
    graph_configured = bool(settings.neo4j_uri and settings.neo4j_user and settings.neo4j_password.get_secret_value())
    model_configured = bool(settings.llm_provider and settings.llm_base_url and settings.llm_model and settings.llm_api_key.get_secret_value())
    index_configured = bool(settings.embedding_provider and settings.embedding_model and settings.embedding_dimension)
    return CheckReadiness((
        CapabilityCheck("data_root", True, DataDirectoryProbe(settings.data_root)),
        CapabilityCheck("graph", True, graph if graph is not None else build_graph(settings)),
        CapabilityCheck("parser", False, parser if parser is not None else build_parser(settings)),
        CapabilityCheck("model", False, RelayModelProbe(settings.llm_base_url, settings.llm_model, settings.llm_api_key.get_secret_value(), settings.llm_fallback_models)),
        CapabilityCheck("index", False, semantic_probe if semantic_probe is not None else UnimplementedProbe(index_configured)),
    ))


def build_graph(settings):
    return Neo4jRepository(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password.get_secret_value(), settings.neo4j_database)


def build_ingestion(settings, graph=None):
    graph = graph if graph is not None else build_graph(settings)
    store = IngestionStore(settings.data_root)
    parsing = LocalParsingService(store, build_parser(settings), build_normalizer())
    extraction = None
    if settings.llm_base_url and settings.llm_model and settings.llm_api_key.get_secret_value():
        extraction = ExtractBusiness(store, build_extractor(settings), build_validator(settings))
    return IngestionService(store, parsing, extraction, graph, validator=build_validator(settings), catalog=BridgeCatalog(settings.data_root,settings.actor_id), actor=settings.actor_id, lease=ProcessLease(store), max_upload_bytes=settings.max_upload_bytes, max_pages=settings.max_pages)


def create_app(settings: Settings | None = None):
    settings = settings if settings is not None else Settings()
    graph = build_graph(settings)
    ingestion = build_ingestion(settings, graph)
    queries = Neo4jQueries(graph)
    indexer, semantic, context, probe = build_semantic(settings, graph, ingestion.store, queries)
    evidence = PublishedEvidenceReader(queries, ingestion.store)
    answer = build_answer(settings, context, LookupGraph(queries,load_rules()), evidence)
    reports = GenerateReport(LookupGraph(queries,load_rules()), evidence, Neo4jReports(graph, settings.actor_id), LocalReportStore(settings.data_root),load_rules())
    provider = RelayAgentProvider(settings.llm_base_url, settings.llm_model, settings.llm_api_key.get_secret_value(), settings.llm_fallback_models) if settings.llm_base_url and settings.llm_model and settings.llm_api_key.get_secret_value() else None
    agents = RunAgent(provider, LocalAgentJournal(settings.data_root), ingestion, LookupGraph(queries,load_rules()), semantic, evidence, answer, reports)
    static = Path(__file__).parent / "interfaces" / "http" / "static"
    return create_http_app(build_readiness(settings, graph, ingestion.parsing.parser, probe), ingestion, LookupGraph(queries,load_rules()), evidence, static, semantic, indexer, context, answer, reports, agents)


def build_answer(settings, context, lookup, evidence):
    generator = None
    if settings.llm_base_url and settings.llm_model and settings.llm_api_key.get_secret_value():
        generator = RelayAnswerGenerator(settings.llm_base_url, settings.llm_model, settings.llm_api_key.get_secret_value(), settings.llm_fallback_models)
    return AnswerQuestion(context, lookup, evidence, generator, LocalAnswerJournal(settings.data_root),load_rules())


def build_semantic(settings, graph, store, queries=None):
    provider = OllamaEmbedding(settings.embedding_base_url, settings.embedding_model, settings.embedding_model_digest,
                               settings.embedding_dimension, settings.embedding_num_ctx, settings.embedding_provider)
    repository = Neo4jSemantic(graph, settings.embedding_dimension, settings.actor_id)
    indexer = IndexDocument(PublishedSemanticInput(store), repository, provider, provider.profile, settings.embedding_dimension)
    semantic = SemanticSearch(repository, provider, provider.profile, settings.embedding_dimension)
    context = BuildContext(semantic, LookupGraph(queries or Neo4jQueries(graph),load_rules()))
    return indexer, semantic, context, SemanticReadiness(repository, provider)
