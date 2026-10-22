import pytest
from ontology_proj.application.query import LookupGraph,validate
from ontology_proj.application.files import FileStoreError
from ontology_proj.adapters.evidence_reader import PublishedEvidenceReader
from types import SimpleNamespace


def request(**kw):
    return dict(query_type='component_observations',scope={'document_ids':['doc-a'],'component_ids':['component-a']},**kw)


class Repo:
    def __init__(self,rows):self.rows=rows
    def lookup(self,q):return self.rows


def row(value='2.6',observed=None):
    return dict(record_id='o1',record_type='Observation',area_value=value,area_unit='m²',observed_at=observed)


@pytest.mark.parametrize('change',[{'query_type':'MATCH (n) DELETE n'},{'scope':{}},{'scope':{'document_ids':'a'}},{'scope':{'document_ids':['a'],'component_ids':['c'],'date_from':'2026-02-30'}},{'scope':{'document_ids':['a'],'component_ids':['c'],'date_from':'2026-10-11','date_to':'2026-10-01'}},{'area_min':1},{'area_min':'NaN'},{'area_min':'Infinity'},{'area_min':'-1'},{'area_min':'3','area_max':'2'},{'cypher':'return 1'},{'acceptance_mode':'rejected'},{'scope':{'document_ids':['a'],'component_ids':['c'],'include_synthetic':'false'}},{'scope':{'document_ids':['a'],'component_ids':['c'],'review_mode':'confirmed_only'},'acceptance_mode':'pending_review'}])
def test_invalid_requests(change):
    q=request();q.update(change)
    with pytest.raises(FileStoreError) as e:validate(q)
    assert e.value.code=='INVALID_REQUEST'


def test_decimal_and_unknown():
    use=LookupGraph(Repo([dict(row('10.0'),record_id='o2'),row(),dict(row(None),record_id='o3')]))
    assert [r['area_value'] for r in use.execute(request(area_min='3.0'))['records']]==['10.0']
    assert use.execute(request(area_max='2.6'))['records'][0]['area_value']=='2.6'
    q=request();q['scope']['date_from']='2026-01-01'
    assert use.execute(q)['count']==0
    assert use.execute(request())['records'][0]['quality_score'] is None


def test_date_inclusive_and_limit():
    q=request();q['scope'].update(date_from='2026-01-01',date_to='2026-01-01')
    assert LookupGraph(Repo([row(observed='2026-01-01')])).execute(q)['count']==1
    with pytest.raises(FileStoreError) as e:LookupGraph(Repo([row()]*501)).execute(request())
    assert e.value.code=='RESULT_LIMIT_EXCEEDED'


def test_unknown_date_omission_count_is_separate_from_out_of_range():
    rows=[row(observed=None),dict(row(observed='2019-01-01'),record_id='old'),dict(row(observed='2026-01-01'),record_id='included')]
    q=request();q['scope']['date_from']='2026-01-01'
    result=LookupGraph(Repo(rows)).execute(q)
    assert result['count']==1 and result['omitted_unknown_date_count']==1
    assert result['warnings']==['Date filter excluded 1 observations with unknown dates']
    assert LookupGraph(Repo(rows)).execute(request())['omitted_unknown_date_count']==0


def test_non_observation_filters_are_rejected():
    with pytest.raises(FileStoreError):LookupGraph(Repo([{'record_type':'Bridge','record_id':'b'}])).execute(dict(query_type='record_by_id',record_id='b',scope={'document_ids':['d']},area_min='0'))


class EvidenceFixture:
    def __init__(self):
        self.block={'block_id':'b','text':'桥😀桥😀','source_ref':'#/text/0','locations':[{'page_no':1,'bbox':[.1,.2,.3,.4]}]}
        self.e={'document_id':'d','block_id':'b','source_ref':'#/text/0','quote':'😀','charspan':[3,4],'page_no':1,'bbox':[.1,.2,.3,.4]}
    def evidence(self,id):return dict(evidence=self.e,block_text=self.block['text'],document={'document_id':'d'},records=[])
    def read_artifact(self,*args):return dict(blocks=[self.block],pages=[{'page_no':1,'width':10,'height':20,'rotation':0}],source_kind='synthetic')
    def read_source(self,*args):return SimpleNamespace(original_filename='test.pdf')
    def read_pdf(self,*args):return b'%PDF'


def test_unicode_exact_span_and_metadata():
    f=EvidenceFixture();r=PublishedEvidenceReader(f,f).read('ev')
    assert r['evidence']['charspan']==[3,4] and r['pdf_url']=='/api/documents/d/file'
    assert r['document']['source_kind']=='synthetic'


@pytest.mark.parametrize('key,value',[('charspan',[2,3]),('bbox',[0,0,2,2]),('page_no',2),('source_ref','fake'),('document_id','outside')])
def test_bad_evidence_never_guesses(key,value):
    f=EvidenceFixture();f.e[key]=value
    with pytest.raises(FileStoreError) as e:PublishedEvidenceReader(f,f).read('ev')
    assert e.value.code=='EVIDENCE_QUOTE_MISMATCH'


def test_absent_location_is_explicit():
    f=EvidenceFixture();f.e['bbox']=None;f.e['page_no']=None
    r=PublishedEvidenceReader(f,f).read('ev');assert r['warnings'] and r['page_geometry'] is None


def test_file_failure_propagates():
    f=EvidenceFixture()
    def fail(*a):raise FileStoreError('ARTIFACT_MISMATCH','hash mismatch')
    f.read_pdf=fail
    with pytest.raises(FileStoreError) as e:PublishedEvidenceReader(f,f).read('ev')
    assert e.value.code=='ARTIFACT_MISMATCH'


def test_existing_six_decimal_bbox_precision():
    f=EvidenceFixture();f.block["locations"][0]["bbox"]=[.1000003,.2000004,.3000001,.4000002]
    assert PublishedEvidenceReader(f,f).read("ev")["evidence"]["bbox"]==[.1,.2,.3,.4]
