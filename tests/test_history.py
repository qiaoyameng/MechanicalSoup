"""Tests for the bounded back/forward page history of StatefulBrowser.

Everything runs offline against a requests_mock counting adapter, so the
exact number and method of network requests can be asserted.
"""

import gc
import weakref

import bs4
import pytest
import requests
import requests_mock
import setpath  # noqa:F401, must come before 'import mechanicalsoup'

import mechanicalsoup
from utils import mock_get


FILTER_FORM = '''
<html>
  <form method="post" action="mock://history.test/results">
    <input type="text" name="query" value="" />
    <select name="sort">
      <option value="asc" selected="selected">asc</option>
      <option value="desc">desc</option>
    </select>
    <input type="checkbox" name="flag" value="on" />
    <button type="submit" name="go" value="1">Go</button>
  </form>
</html>
'''

RESULTS_PAGE = '''
<html>
  <h1>Results</h1>
  <a href="mock://history.test/details">details</a>
</html>
'''

DETAILS_PAGE = '<html><h1>Details</h1></html>'


def make_browser(max_pages=10, max_bytes=None, session=None):
    adapter = requests_mock.Adapter()
    kwargs = {'requests_adapters': {'mock': adapter}}
    if session is not None:
        kwargs['session'] = session
    browser = mechanicalsoup.StatefulBrowser(
        history_max_pages=max_pages, history_max_bytes=max_bytes, **kwargs)
    mock_get(adapter, 'mock://history.test/form', FILTER_FORM)
    mock_get(adapter, 'mock://history.test/details', DETAILS_PAGE)
    adapter.register_uri(
        'POST', 'mock://history.test/results',
        text=RESULTS_PAGE,
        headers={'Content-Type': 'text/html'})
    return browser, adapter


def test_back_forward_no_network_and_no_repost():
    """GET -> POST -> GET, then back/forward restores snapshots locally.

    The single POST must never be re-sent and no extra request may happen
    while moving through the history.
    """
    browser, adapter = make_browser()

    browser.open('mock://history.test/form')      # GET
    browser.select_form()
    browser['query'] = 'alice'
    browser['sort'] = 'desc'
    browser['flag'] = True
    browser.submit_selected()                     # POST
    browser.open('mock://history.test/details')   # GET

    assert adapter.call_count == 3
    assert [(r.method, r.url) for r in
            adapter.request_history] == [
        ('GET', 'mock://history.test/form'),
        ('POST', 'mock://history.test/results'),
        ('GET', 'mock://history.test/details'),
    ]

    browser.back()
    assert browser.url == 'mock://history.test/results'
    assert browser.page.find('h1').text == 'Results'

    browser.back()
    assert browser.url == 'mock://history.test/form'
    # The form selection and all edited values come back from the snapshot.
    assert browser.get_current_form() is not None
    assert browser.page.find('input', {'name': 'query'})['value'] == 'alice'
    assert browser.page.find('option', selected=True)['value'] == 'desc'
    assert browser.page.find('input', {'name': 'flag'}).has_attr('checked')

    browser.forward()
    assert browser.url == 'mock://history.test/results'
    browser.forward()
    assert browser.url == 'mock://history.test/details'

    # Moving in the history performed zero additional requests, hence the
    # original POST was not sent again.
    assert adapter.call_count == 3
    assert sum(r.method == 'POST' for r in adapter.request_history) == 1


def test_edit_condition_and_resubmit_after_back():
    """The headline workflow: go back to tweak one filter, then navigate
    again. The old POST is never replayed; only the new request is sent."""
    browser, adapter = make_browser()

    browser.open('mock://history.test/form')
    browser.select_form()
    browser['query'] = 'alice'
    browser.submit_selected()                     # POST #1
    post_count_after_submit = sum(
        r.method == 'POST' for r in adapter.request_history)

    # Back to the filter page purely from the snapshot.
    browser.back()
    assert browser.url == 'mock://history.test/form'
    assert adapter.call_count == 2

    # Change a single condition and submit again.
    browser['query'] = 'bob'
    browser.submit_selected()                     # POST #2

    posts = [r for r in adapter.request_history if r.method == 'POST']
    assert len(posts) == post_count_after_submit + 1
    assert 'bob' in posts[-1].text
    # The forward branch (old results page) is gone.
    assert not browser.can_go_forward
    assert browser.url == 'mock://history.test/results'


def test_restored_form_is_independent_from_history():
    """Editing a restored page must not mutate the stored history entry."""
    browser, adapter = make_browser()
    browser.open('mock://history.test/form')
    browser.select_form()
    browser['query'] = 'alice'
    stored_page = browser.history.current.page

    # The history stores a deep copy, not the live page.
    assert stored_page is not browser.page

    browser.open('mock://history.test/details')
    browser.back()
    assert browser.page is not browser.history.current.page

    browser['query'] = 'bob'
    assert browser.page.find('input')['value'] == 'bob'

    browser.forward()
    browser.back()
    assert browser.page.find('input')['value'] == 'alice'


def test_form_ordinal_and_submit_choice_are_restored():
    adapter = requests_mock.Adapter()
    browser = mechanicalsoup.StatefulBrowser(
        requests_adapters={'mock': adapter})
    two_forms = (
        '<html><body>'
        '<form id="f1" action="mock://history.test/x">'
        '<input name="a" value="1"></form>'
        '<form id="f2" action="mock://history.test/y">'
        '<input name="b" value="2">'
        '<button type="submit" name="go">Go</button></form>'
        '</body></html>')
    mock_get(adapter, 'mock://history.test/forms', two_forms)
    mock_get(adapter, 'mock://history.test/other', '<html>other</html>')

    browser.open('mock://history.test/forms')
    assert browser.history.current.form is None
    browser.select_form('#f2')
    browser.form.choose_submit('go')
    browser['b'] = 'edited'
    browser.open('mock://history.test/other')

    stored = browser.history.entries()[0]
    assert stored.form.form.get('id') == 'f2'
    assert stored.form.submit_chosen.get('name') == 'go'

    browser.back()
    assert browser.form.form.get('id') == 'f2'
    assert browser.form.submit_chosen.get('name') == 'go'
    assert browser.form.form in browser.page.find_all('form')
    assert browser.page.find('input', {'name': 'b'})['value'] == 'edited'

    # Selecting the other form before navigating re-snapshots the entry.
    browser.select_form('#f1')
    browser['a'] = 'changed'
    browser.open('mock://history.test/other')
    browser.back()
    assert browser.form.form.get('id') == 'f1'
    assert browser.page.find('input', {'name': 'a'})['value'] == 'changed'


def test_new_navigation_after_back_discards_forward_branch():
    browser, adapter = make_browser()
    browser.open('mock://history.test/form')
    browser.select_form()
    browser.submit_selected()
    browser.open('mock://history.test/details')

    browser.back()
    browser.back()
    assert browser.can_go_forward

    # A brand-new navigation truncates the forward branch.
    browser.open('mock://history.test/details')
    assert not browser.can_go_forward
    assert [e.url for e in browser.history.entries()] == [
        'mock://history.test/form',
        'mock://history.test/details',
    ]
    with pytest.raises(IndexError):
        browser.forward()


def test_back_forward_boundaries():
    browser, _ = make_browser()
    with pytest.raises(IndexError):
        browser.back()
    with pytest.raises(IndexError):
        browser.forward()

    browser.open('mock://history.test/form')
    assert not browser.can_go_back
    assert not browser.can_go_forward
    browser.open('mock://history.test/details')
    assert browser.can_go_back
    assert not browser.can_go_forward


def test_request_exception_adds_no_entry():
    adapter = requests_mock.Adapter()
    browser = mechanicalsoup.StatefulBrowser(
        requests_adapters={'mock': adapter})
    mock_get(adapter, 'mock://history.test/form', FILTER_FORM)
    adapter.register_uri(
        'GET', 'mock://history.test/boom',
        exc=requests.ConnectionError('boom'))

    browser.open('mock://history.test/form')
    with pytest.raises(requests.ConnectionError):
        browser.open('mock://history.test/boom')

    assert len(browser.history) == 1
    assert browser.url == 'mock://history.test/form'


def test_parse_failure_adds_no_entry(mocker):
    adapter = requests_mock.Adapter()
    browser = mechanicalsoup.StatefulBrowser(
        requests_adapters={'mock': adapter})
    mock_get(adapter, 'mock://history.test/form', FILTER_FORM)
    browser.open('mock://history.test/form')

    mocker.patch.object(
        bs4, 'BeautifulSoup', side_effect=ValueError('parse exploded'))
    with pytest.raises(ValueError, match='parse exploded'):
        browser.open('mock://history.test/form')

    assert len(browser.history) == 1
    assert browser.url == 'mock://history.test/form'


def test_non_html_response_is_not_history():
    adapter = requests_mock.Adapter()
    browser = mechanicalsoup.StatefulBrowser(
        requests_adapters={'mock': adapter})
    mock_get(adapter, 'mock://history.test/form', FILTER_FORM)
    mock_get(adapter, 'mock://history.test/bin', b'\x00\x01binary',
             content_type='application/octet-stream')

    browser.open('mock://history.test/form')
    browser.open('mock://history.test/bin')
    assert browser.page is None
    assert browser.url == 'mock://history.test/bin'
    assert len(browser.history) == 1


def test_max_pages_evicts_oldest():
    browser, adapter = make_browser(max_pages=3, max_bytes=None)
    for i in range(5):
        mock_get(adapter, f'mock://history.test/p{i}',
                 f'<html>page {i}</html>')
        browser.open(f'mock://history.test/p{i}')

    assert len(browser.history) == 3
    assert [e.url for e in browser.history.entries()] == [
        'mock://history.test/p2',
        'mock://history.test/p3',
        'mock://history.test/p4',
    ]
    assert browser.history.index == 2
    browser.back(2)
    assert browser.url == 'mock://history.test/p2'
    with pytest.raises(IndexError):
        browser.back()


def test_max_bytes_budget_and_single_page_policy():
    adapter = requests_mock.Adapter()
    browser = mechanicalsoup.StatefulBrowser(
        requests_adapters={'mock': adapter},
        history_max_pages=None, history_max_bytes=100)

    small = '<html>' + 'x' * 40 + '</html>'
    huge = '<html>' + 'y' * 500 + '</html>'
    tiny = '<html>cc</html>'
    mock_get(adapter, 'mock://history.test/small', small)
    mock_get(adapter, 'mock://history.test/huge', huge)
    mock_get(adapter, 'mock://history.test/tiny', tiny)

    browser.open('mock://history.test/small')
    small_size = browser.history.current.body_size
    assert small_size <= 100

    # The oversize page is still displayed, but refused by the history.
    browser.open('mock://history.test/huge')
    assert browser.history.oversize_skips == 1
    assert browser.url == 'mock://history.test/huge'
    assert [e.url for e in
            browser.history.entries()] == ['mock://history.test/small']

    # Budget eviction removes oldest entries instead of exceeding the cap.
    browser.open('mock://history.test/tiny')
    assert browser.history.total_bytes <= 100
    assert all(e.body_size <= 100 for e in browser.history.entries())


def test_oversize_page_does_not_poison_previous_entry():
    adapter = requests_mock.Adapter()
    browser = mechanicalsoup.StatefulBrowser(
        requests_adapters={'mock': adapter},
        history_max_pages=None, history_max_bytes=100)
    mock_get(adapter, 'mock://history.test/small',
             '<html><p>small</p></html>')
    mock_get(adapter, 'mock://history.test/huge',
             '<html>' + 'y' * 500 + '</html>')
    mock_get(adapter, 'mock://history.test/tiny', '<html>cc</html>')

    browser.open('mock://history.test/small')
    browser.open('mock://history.test/huge')   # refused by the history
    assert browser.history.oversize_skips == 1
    browser.open('mock://history.test/tiny')

    # The small entry must still hold its own page, not the huge live page.
    small_entry = browser.history.entries()[0]
    assert small_entry.url == 'mock://history.test/small'
    assert small_entry.page.find('p').text == 'small'
    browser.back()
    assert browser.url == 'mock://history.test/small'
    assert browser.page.find('p').text == 'small'


def test_non_html_page_does_not_poison_previous_entry():
    adapter = requests_mock.Adapter()
    browser = mechanicalsoup.StatefulBrowser(
        requests_adapters={'mock': adapter})
    mock_get(adapter, 'mock://history.test/small',
             '<html><p>small</p></html>')
    mock_get(adapter, 'mock://history.test/bin', b'\x00\x01binary',
             content_type='application/octet-stream')
    mock_get(adapter, 'mock://history.test/tiny', '<html>cc</html>')

    browser.open('mock://history.test/small')
    browser.open('mock://history.test/bin')    # no history entry
    browser.open('mock://history.test/tiny')

    assert (browser.history.entries()[0].url
            == 'mock://history.test/small')
    assert browser.history.entries()[0].page.find('p').text == 'small'
    browser.back()
    assert browser.url == 'mock://history.test/small'
    assert browser.page.find('p').text == 'small'


def test_back_reattaches_after_entryless_navigation():
    adapter = requests_mock.Adapter()
    browser = mechanicalsoup.StatefulBrowser(
        requests_adapters={'mock': adapter},
        history_max_pages=None, history_max_bytes=100)
    mock_get(adapter, 'mock://history.test/a', '<html><p>a</p></html>')
    mock_get(adapter, 'mock://history.test/b', '<html><p>b</p></html>')
    mock_get(adapter, 'mock://history.test/huge',
             '<html>' + 'z' * 400 + '</html>')

    browser.open('mock://history.test/a')
    browser.open('mock://history.test/b')
    browser.open('mock://history.test/huge')   # not stored
    assert not browser.history.live_in_history

    # First back re-attaches to the entry at the cursor (page b).
    browser.back()
    assert browser.history.live_in_history
    assert browser.url == 'mock://history.test/b'
    browser.back()
    assert browser.url == 'mock://history.test/a'


def test_back_steps_reattach_then_move():
    adapter = requests_mock.Adapter()
    browser = mechanicalsoup.StatefulBrowser(
        requests_adapters={'mock': adapter},
        history_max_pages=None, history_max_bytes=100)
    mock_get(adapter, 'mock://history.test/a', '<html><p>a</p></html>')
    mock_get(adapter, 'mock://history.test/b', '<html><p>b</p></html>')
    mock_get(adapter, 'mock://history.test/c', '<html><p>c</p></html>')
    mock_get(adapter, 'mock://history.test/huge',
             '<html>' + 'z' * 400 + '</html>')

    browser.open('mock://history.test/a')
    browser.open('mock://history.test/b')
    browser.open('mock://history.test/c')
    browser.open('mock://history.test/huge')   # cursor stays on c

    # Re-attach to c (no step), then move two entries back to a.
    browser.back(3)
    assert browser.url == 'mock://history.test/a'


def test_oversize_refresh_drops_entry_but_keeps_navigation():
    adapter = requests_mock.Adapter()
    browser = mechanicalsoup.StatefulBrowser(
        requests_adapters={'mock': adapter},
        history_max_pages=None, history_max_bytes=100)
    mock_get(adapter, 'mock://history.test/a', '<html><p>a</p></html>')
    mock_get(adapter, 'mock://history.test/b', '<html><p>b</p></html>')
    browser.open('mock://history.test/a')
    browser.open('mock://history.test/b')

    mock_get(adapter, 'mock://history.test/b',
             '<html>' + 'z' * 400 + '</html>')
    browser.refresh()
    assert browser.url == 'mock://history.test/b'
    assert browser.history.oversize_skips == 1
    assert [e.url for e in
            browser.history.entries()] == ['mock://history.test/a']
    browser.back()
    assert browser.url == 'mock://history.test/a'


def test_session_cookies_survive_back_forward():
    session = requests.Session()
    requests.utils.add_dict_to_cookiejar(
        session.cookies, {'sessionid': 'abc123'})
    browser, adapter = make_browser(session=session)

    browser.open('mock://history.test/form')
    browser.open('mock://history.test/details')
    browser.back()
    browser.forward()

    assert browser.session is session
    assert session.cookies.get('sessionid') == 'abc123'
    # History navigation itself produced no traffic at all.
    assert adapter.call_count == 2


def test_cleared_history_releases_pages():
    browser, adapter = make_browser(max_pages=2, max_bytes=None)
    mock_get(adapter, 'mock://history.test/p1', '<html>one</html>')
    mock_get(adapter, 'mock://history.test/p2', '<html>two</html>')
    mock_get(adapter, 'mock://history.test/p3', '<html>three</html>')
    browser.open('mock://history.test/p1')
    browser.open('mock://history.test/p2')

    evicted_candidate = browser.history.entries()[0].page
    page_ref = weakref.ref(evicted_candidate)
    browser.open('mock://history.test/p3')  # capacity evicts p1
    del evicted_candidate
    gc.collect()
    assert page_ref() is None

    live_page = browser.page
    live_ref = weakref.ref(live_page)
    browser.clear_history()
    assert len(browser.history) == 0
    # The currently displayed page remains available.
    assert browser.page is live_page
    del live_page
    gc.collect()
    assert live_ref() is not None


def test_close_clears_history():
    browser, adapter = make_browser()
    browser.open('mock://history.test/form')
    entry_page = browser.history.current.page
    ref = weakref.ref(entry_page)
    del entry_page
    browser.close()
    gc.collect()
    assert len(browser.history) == 0
    assert ref() is None


def test_refresh_replaces_current_entry():
    browser, adapter = make_browser()
    browser.open('mock://history.test/form')
    browser.open('mock://history.test/details')
    mock_get(adapter, 'mock://history.test/details',
             '<html><h1>Reloaded</h1></html>')

    count_before = len(browser.history)
    browser.refresh()

    assert len(browser.history) == count_before
    assert browser.page.find('h1').text == 'Reloaded'
    assert [e.url for e in browser.history.entries()] == [
        'mock://history.test/form',
        'mock://history.test/details',
    ]


def test_refresh_unavailable_on_restored_page():
    # A restored snapshot deliberately keeps no PreparedRequest, so refresh
    # cannot silently re-issue the original POST.
    browser, _ = make_browser()
    browser.open('mock://history.test/form')
    browser.select_form()
    browser.submit_selected()
    browser.back()
    with pytest.raises(ValueError, match='not refreshable'):
        browser.refresh()


def test_fake_page_and_no_state_update_skip_history():
    browser = mechanicalsoup.StatefulBrowser()
    browser.open_fake_page(FILTER_FORM, url='mock://history.test/form')
    assert len(browser.history) == 0

    adapter = requests_mock.Adapter()
    browser2 = mechanicalsoup.StatefulBrowser(
        requests_adapters={'mock': adapter})
    mock_get(adapter, 'mock://history.test/empty',
             '<html><form method="post" action="mock://history.test/post">'
             '</form></html>')
    adapter.register_uri(
        'POST', 'mock://history.test/post',
        text='<html>ok</html>',
        headers={'Content-Type': 'text/html'})
    browser2.open('mock://history.test/empty')
    browser2.select_form()
    before = len(browser2.history)
    browser2.submit_selected(btnName=False, update_state=False)
    assert len(browser2.history) == before


def test_final_url_is_stored_after_redirect():
    adapter = requests_mock.Adapter()
    browser = mechanicalsoup.StatefulBrowser(
        requests_adapters={'mock': adapter})
    adapter.register_uri(
        'GET', 'mock://history.test/redirect',
        status_code=301,
        headers={'Location': 'mock://history.test/target'})
    mock_get(adapter, 'mock://history.test/target',
             '<html><h1>target</h1></html>')

    browser.open('mock://history.test/redirect')
    assert browser.url == 'mock://history.test/target'
    assert browser.history.current.url == 'mock://history.test/target'
    assert len(browser.history) == 1
    with pytest.raises(IndexError):
        browser.back()
