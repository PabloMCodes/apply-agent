import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from src.api.app import create_app
from src.jobs import link_import as links
from src.jobs.models import Job
from src.jobs.fetch import FetchError

URL = 'https://careers.example.com/jobs/123'


def html(posting):
    return '<script type="application/ld+json">' + json.dumps(posting) + '</script>'


def test_structured_job_graph_and_multiple_locations():
    result = links.parse_page(html({'@graph':[{'@type':'Organization'}, {
        '@type':['Thing','JobPosting'], 'title':'Software Engineer',
        'hiringOrganization':{'name':'Example Co'},
        'jobLocation':[{'address':{'addressLocality':'Austin','addressRegion':'TX','addressCountry':{'name':'US'}}},
                       {'address':{'addressLocality':'Boston','addressRegion':'MA'}}]
    }]}), URL)
    assert result == {'company':'Example Co','title':'Software Engineer','location':'Austin, TX, US; Boston, MA'}


def test_remote_and_missing_location_are_distinct():
    assert links.parse_page(html({'@type':'JobPosting','title':'Designer','jobLocationType':'TELECOMMUTE'}),URL)['location']=='Remote'
    assert links.parse_page(html({'@type':'JobPosting','title':'Designer'}),URL)['location']==''


def test_invalid_json_and_metadata_fallback():
    content = '<script type="application/ld+json">oops</script><meta property="og:title" content="Designer at Acme"><h1>Jobs</h1>'
    assert links.parse_page(content,URL)['title']=='Designer at Acme'
    assert links.parse_page('<h1>Product Designer</h1>',URL)['title']=='Product Designer'


def test_multiple_postings_do_not_pick_unrelated_job():
    postings = [{'@type':'JobPosting','title':'Wrong job','url':'https://example.com/wrong'},
                {'@type':'JobPosting','title':'Correct job','url':URL}]
    assert links.parse_page(html(postings),URL)['title']=='Correct job'
    assert links.parse_page(html(postings),'https://example.com/board')['title']==''


def test_careerpuck_preserves_original_link(monkeypatch):
    url='https://app.careerpuck.com/job-board/lyft/job/123?gh_jid=123'
    monkeypatch.setattr(links,'load_source',lambda source:[Job('Lyft','Engineer','New York','https://elsewhere.example.com',url)])
    job,warnings=links.from_link(url)
    assert (job.company,job.title,job.location,job.application_url)==('Lyft','Engineer','New York',url)
    assert not warnings


def test_unavailable_page_saves_explicit_fallback(monkeypatch):
    monkeypatch.setattr(links,'_read_page',Mock(side_effect=FetchError('Login required')))
    job,warnings=links.from_link(URL)
    assert job.title=='Job details unavailable'
    assert job.company=='careers.example.com'
    assert job.location=='Not specified'
    assert 'Login required' in warnings


@pytest.mark.parametrize('url',['http://example.com','https://user:secret@example.com','https://example.com:8080'])
def test_rejects_invalid_links(url):
    with pytest.raises(ValueError):links.from_link(url)


def test_private_address_never_connects(monkeypatch):
    monkeypatch.setattr(links.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
    connect=Mock();monkeypatch.setattr(links.socket,'create_connection',connect)
    with pytest.raises(ValueError,match='public websites'):links._read_page(URL)
    connect.assert_not_called()


def test_redirect_to_private_host_is_rejected(monkeypatch):
    def resolve(host,*a,**k):
        return [(2,1,6,'',('127.0.0.1' if host=='localhost' else '93.184.216.34',443))]
    monkeypatch.setattr(links.socket,'getaddrinfo',resolve)
    connect=Mock();monkeypatch.setattr(links.socket,'create_connection',connect)
    monkeypatch.setattr(links.ssl,'create_default_context',Mock())
    response=Mock(status=302);response.getheader.return_value='https://localhost/private'
    connection=Mock();connection.getresponse.return_value=response
    monkeypatch.setattr(links.http.client,'HTTPSConnection',Mock(return_value=connection))
    with pytest.raises(ValueError,match='public websites'):links._read_page(URL)
    connect.assert_called_once_with(('93.184.216.34',443),timeout=8)
    connection.close.assert_called_once()


def test_link_import_api_is_idempotent_and_preserves_applied(tmp_path,monkeypatch):
    metadata=Mock(return_value=(Job('Acme','Engineer','Remote',URL,URL),[]))
    monkeypatch.setattr(links,'from_link',metadata)
    with TestClient(create_app(tmp_path/'test.sqlite3')) as client:
        first=client.post('/jobs/from-link',json={'application_url':URL})
        assert first.status_code==200
        item=first.json();assert item['created'] and item['job']['title']=='Engineer'
        job_id=item['job']['id']
        client.put(f'/jobs/{job_id}/tracking',json={'status':'applied','notes':'Already sent'})
        second=client.post('/jobs/from-link',json={'application_url':URL}).json()
        assert not second['created'] and second['job']['status']=='applied'
        assert second['job']['notes']=='Already sent'
        metadata.assert_called_once()
        assert client.post('/jobs/from-link',json={'application_url':'http://example.com'}).status_code==422
        assert client.post('/jobs/from-link',json={'application_url':'not a URL'}).status_code==422


def test_remote_keeps_country_restrictions():
    result=links.parse_page(html({'@type':'JobPosting','title':'Designer',
        'jobLocationType':'TELECOMMUTE','applicantLocationRequirements':{'@type':'Country','name':'Canada'}}),URL)
    assert result['location']=='Remote; Canada'


def test_challenge_page_is_not_a_job_title():
    assert links.parse_page('<title>Just a moment...</title>',URL)['title']==''
