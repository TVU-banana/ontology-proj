"""Read hash-verified existing publication; no parser or extractor invocation."""


class PublishedSemanticInput:
    def __init__(self, store): self.store = store

    def read(self, document_id):
        self.store.read_pdf(document_id)
        return self.store.read_artifact(document_id, 'normalized'), self.store.read_business_artifact(document_id, 'records')
