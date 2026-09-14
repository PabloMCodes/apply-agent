import pytest
from src.jobs.parser import ParseError, parse_jobs

HEADER = '| Company | Position | Location | Salary | Posting | Age |\n|---|---|---|---|---|---|\n'


def test_html_markdown_continuation_and_duplicates():
    text = HEADER + '''| <b>A &amp; B</b> | SWE | NYC<br>Remote | - | <a href="https://example.com/1">Apply</a> | 1d |
| ↳ | Backend | NYC | - | [Apply](https://example.com/2) | 1d |
| A &amp; B | Updated | NYC | - | [Apply](https://example.com/1) | 1d |
| Closed | SWE | NYC | - | 🔒 | 1d |
'''
    jobs = parse_jobs(text, 'test-source')
    assert len(jobs) == 2
    assert jobs[0].title == 'Updated'
    assert jobs[1].company == 'A & B'
    assert jobs[1].source == 'test-source'


def test_location_breaks():
    jobs = parse_jobs(HEADER + '| A | SWE | NYC<br>Remote | - | [Apply](https://example.com/1) | 1d |')
    assert jobs[0].location == 'NYC Remote'


@pytest.mark.parametrize('text', [
    'not a table',
    HEADER + '| A | SWE |',
    HEADER + '| A | SWE | NYC | - | missing | 1d |',
    HEADER + '| A | SWE | NYC | - | <a href="javascript:alert(1)">Apply</a> | 1d |',
])
def test_bad_source_is_explicit(text):
    with pytest.raises(ParseError):
        parse_jobs(text)


def test_empty_recognized_table():
    assert parse_jobs(HEADER) == []


def test_blank_location_is_preserved_as_unspecified():
    jobs = parse_jobs(HEADER + '| A | SWE | | - | [Apply](https://example.com/1) | 1d |')
    assert jobs[0].location == 'Not specified'
