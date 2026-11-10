import sys

import bs4
import pytest
import setpath  # noqa:F401, must come before 'import mechanicalsoup'
from utils import setup_mock_browser

import mechanicalsoup


def test_construct_form_fail():
    """Form objects must be constructed from form html elements."""
    soup = bs4.BeautifulSoup('<notform>This is not a form</notform>', 'lxml')
    tag = soup.find('notform')
    assert isinstance(tag, bs4.element.Tag)
    with pytest.warns(FutureWarning, match="from a 'notform'"):
        mechanicalsoup.Form(tag)


def test_submit_online(httpbin):
    """Complete and submit the pizza form at http://httpbin.org/forms/post """
    browser = mechanicalsoup.Browser()
    page = browser.get(httpbin + "/forms/post")
    form = mechanicalsoup.Form(page.soup.form)

    input_data = {"custname": "Philip J. Fry"}
    form.input(input_data)

    check_data = {"size": "large", "topping": ["cheese"]}
    form.check(check_data)
    check_data = {"size": "medium", "topping": "onion"}
    form.check(check_data)

    form.textarea({"comments": "warm"})
    form.textarea({"comments": "actually, no, not warm"})
    form.textarea({"comments": "freezer"})

    response = browser.submit(form, page.url)

    # helpfully the form submits to http://httpbin.org/post which simply
    # returns the request headers in json format
    json = response.json()
    data = json["form"]
    assert data["custname"] == "Philip J. Fry"
    assert data["custtel"] == ""  # web browser submits "" for input left blank
    assert data["size"] == "medium"
    assert data["topping"] == ["cheese", "onion"]
    assert data["comments"] == "freezer"


def test_submit_set(httpbin):
    """Complete and submit the pizza form at http://httpbin.org/forms/post """
    browser = mechanicalsoup.Browser()
    page = browser.get(httpbin + "/forms/post")
    form = mechanicalsoup.Form(page.soup.form)

    form["custname"] = "Philip J. Fry"

    form["size"] = "medium"
    form["topping"] = ("cheese", "onion")

    form["comments"] = "freezer"

    response = browser.submit(form, page.url)

    # helpfully the form submits to http://httpbin.org/post which simply
    # returns the request headers in json format
    json = response.json()
    data = json["form"]
    assert data["custname"] == "Philip J. Fry"
    assert data["custtel"] == ""  # web browser submits "" for input left blank
    assert data["size"] == "medium"
    assert data["topping"] == ["cheese", "onion"]
    assert data["comments"] == "freezer"


@pytest.mark.parametrize("expected_post", [
    pytest.param(
        [
            ('text', 'Setting some text!'),
            ('comment', 'Testing preview page'),
            ('preview', 'Preview Page'),
        ], id='preview'),
    pytest.param(
        [
            ('text', '= Heading =\n\nNew page here!\n'),
            ('comment', 'Created new page'),
            ('save', 'Submit changes'),
        ], id='save'),
    pytest.param(
        [
            ('text', '= Heading =\n\nNew page here!\n'),
            ('comment', 'Testing choosing cancel button'),
            ('cancel', 'Cancel'),
        ], id='cancel'),
])
def test_choose_submit(expected_post):
    browser, url = setup_mock_browser(expected_post=expected_post)
    browser.open(url)
    form = browser.select_form('#choose-submit-form')
    browser['text'] = dict(expected_post)['text']
    browser['comment'] = dict(expected_post)['comment']
    submit = form.choose_submit(expected_post[2][0])
    assert submit == form.submit_chosen
    res = browser.submit_selected()
    assert res.status_code == 200 and res.text == 'Success!'


@pytest.mark.parametrize("value", [
    pytest.param('continue', id='first'),
    pytest.param('cancel', id='second'),
])
def test_choose_submit_from_selector(value):
    """Test choose_submit by passing a CSS selector argument."""
    text = """
    <form method="post" action="mock://form.com/post">
      <input type="submit" name="do" value="continue" />
      <input type="submit" name="do" value="cancel" />
    </form>"""
    browser, url = setup_mock_browser(expected_post=[('do', value)], text=text)
    browser.open(url)
    form = browser.select_form()
    submits = form.form.select(f'input[value="{value}"]')
    assert len(submits) == 1
    form.choose_submit(submits[0])
    res = browser.submit_selected()
    assert res.status_code == 200 and res.text == 'Success!'


choose_submit_fail_form = '''
<html>
  <form id="choose-submit-form">
    <input type="submit" name="test_submit" value="Test Submit" />
  </form>
</html>
'''


@pytest.mark.parametrize("select_name", [
    pytest.param({'name': 'does_not_exist', 'fails': True}, id='not found'),
    pytest.param({'name': 'test_submit', 'fails': False}, id='found'),
])
def test_choose_submit_fail(select_name):
    browser = mechanicalsoup.StatefulBrowser()
    browser.open_fake_page(choose_submit_fail_form)
    form = browser.select_form('#choose-submit-form')
    if select_name['fails']:
        with pytest.raises(mechanicalsoup.utils.LinkNotFoundError):
            form.choose_submit(select_name['name'])
    else:
        assert form.form.input == form.choose_submit(select_name['name'])


def test_choose_submit_twice():
    """Test that choose_submit can be used multiple times."""
    text = '''
    <form>
      <input type="submit" name="test1" value="Test1" />
      <input type="submit" name="test2" value="Test2" />
    </form>
    '''
    soup = bs4.BeautifulSoup(text, 'lxml')
    form = mechanicalsoup.Form(soup.form)
    test1 = form.choose_submit('test1')
    test2 = form.choose_submit('test2')
    assert test1 != test2


choose_submit_multiple_match_form = '''
<html>
  <form id="choose-submit-form">
    <input type="submit" name="test_submit" value="First Submit" />
    <input type="submit" name="test_submit" value="Second Submit" />
  </form>
</html>
'''


def test_choose_submit_multiple_match():
    browser = mechanicalsoup.StatefulBrowser()
    browser.open_fake_page(choose_submit_multiple_match_form)
    form = browser.select_form('#choose-submit-form')
    with pytest.raises(mechanicalsoup.utils.LinkNotFoundError):
        form.choose_submit('test_submit')


submit_form_noaction = '''
<html>
  <body>
    <form id="choose-submit-form">
      <input type="text" name="text1" value="someValue1" />
      <input type="text" name="text2" value="someValue2" />
      <input type="submit" name="save" />
    </form>
  </body>
</html>
'''


def test_form_noaction():
    browser, url = setup_mock_browser()
    browser.open_fake_page(submit_form_noaction, url=url)
    form = browser.select_form('#choose-submit-form')
    form['text1'] = 'newText1'
    res = browser.submit_selected()
    assert res.status_code == 200 and browser.url == url


submit_form_action = '''
<html>
  <body>
    <form id="choose-submit-form" action="mock://form.com">
      <input type="text" name="text1" value="someValue1" />
      <input type="text" name="text2" value="someValue2" />
      <input type="submit" name="save" />
    </form>
  </body>
</html>
'''


def test_form_action():
    browser, url = setup_mock_browser()
    # for info about example.com see: https://tools.ietf.org/html/rfc2606
    browser.open_fake_page(submit_form_action,
                           url="http://example.com/invalid/")
    form = browser.select_form('#choose-submit-form')
    form['text1'] = 'newText1'
    res = browser.submit_selected()
    assert res.status_code == 200 and browser.url == url


set_select_form = '''
<html>
  <form method="post" action="mock://form.com/post">
    <select name="entree">
      <option value="tofu" selected="selected">Tofu Stir Fry</option>
      <option value="curry">Red Curry</option>
      <option value="tempeh">Tempeh Tacos</option>
    </select>
    <input type="submit" value="Select" />
  </form>
</html>
'''


@pytest.mark.parametrize("option", [
    pytest.param({'result': [('entree', 'tofu')], 'default': True},
                 id='default'),
    pytest.param({'result': [('entree', 'curry')], 'default': False},
                 id='selected'),
])
def test_set_select(option):
    '''Test the branch of Form.set that finds "select" elements.'''
    browser, url = setup_mock_browser(expected_post=option['result'],
                                      text=set_select_form)
    browser.open(url)
    browser.select_form('form')
    if not option['default']:
        browser[option['result'][0][0]] = option['result'][0][1]
    res = browser.submit_selected()
    assert res.status_code == 200 and res.text == 'Success!'


set_select_multiple_form = '''
<form method="post" action="mock://form.com/post">
  <select name="instrument" multiple>
    <option value="piano">Piano</option>
    <option value="bass">Bass</option>
    <option value="violin">Violin</option>
  </select>
  <input type="submit" value="Select Multiple" />
</form>
'''


@pytest.mark.parametrize("options", [
    pytest.param('bass', id='select one (str)'),
    pytest.param(('bass',), id='select one (tuple)'),
    pytest.param(('piano', 'violin'), id='select two'),
])
def test_set_select_multiple(options):
    """Test a <select multiple> element."""
    # When a browser submits multiple selections, the qsl looks like:
    #  name=option1&name=option2
    if not isinstance(options, list) and not isinstance(options, tuple):
        expected = [('instrument', options)]
    else:
        expected = [('instrument', option) for option in options]
    browser, url = setup_mock_browser(expected_post=expected,
                                      text=set_select_multiple_form)
    browser.open(url)
    form = browser.select_form('form')
    form.set_select({'instrument': options})
    res = browser.submit_selected()
    assert res.status_code == 200 and res.text == 'Success!'


def test_form_not_found():
    browser = mechanicalsoup.StatefulBrowser()
    browser.open_fake_page(page_with_various_fields)
    form = browser.select_form('form')
    with pytest.raises(mechanicalsoup.utils.LinkNotFoundError):
        form.input({'foo': 'bar', 'nosuchname': 'nosuchval'})
    with pytest.raises(mechanicalsoup.utils.LinkNotFoundError):
        form.check({'foo': 'bar', 'nosuchname': 'nosuchval'})
    with pytest.raises(mechanicalsoup.utils.LinkNotFoundError):
        form.check({'entree': 'cheese'})
    with pytest.raises(mechanicalsoup.utils.LinkNotFoundError):
        form.check({'topping': 'tofu'})
    with pytest.raises(mechanicalsoup.utils.LinkNotFoundError):
        form.textarea({'bar': 'value', 'foo': 'nosuchval'})
    with pytest.raises(mechanicalsoup.utils.LinkNotFoundError):
        form.set_radio({'size': 'tiny'})
    with pytest.raises(mechanicalsoup.utils.LinkNotFoundError):
        form.set_select({'entree': ('no_multiple', 'no_multiple')})


def test_form_set_radio_checkbox(capsys):
    browser = mechanicalsoup.StatefulBrowser()
    browser.open_fake_page(page_with_various_fields,
                           url="http://example.com/invalid/")
    form = browser.select_form("form")
    form.set_radio({"size": "small"})
    form.set_checkbox({"topping": "cheese"})
    browser.form.print_summary()
    out, err = capsys.readouterr()
    # Different versions of bs4 show either <input></input> or
    # <input/>. Normalize before comparing.
    out = out.replace('></input>', '/>')
    assert out == """<input name="foo"/>
<textarea name="bar"></textarea>
<select name="entree">
<option selected="selected" value="tofu">Tofu Stir Fry</option>
<option value="curry">Red Curry</option>
<option value="tempeh">Tempeh Tacos</option>
</select>
<input name="topping" type="checkbox" value="bacon"/>
<input checked="" name="topping" type="Checkbox" value="cheese"/>
<input name="topping" type="checkbox" value="onion"/>
<input name="topping" type="checkbox" value="mushroom"/>
<input checked="" name="size" type="Radio" value="small"/>
<input name="size" type="radio" value="medium"/>
<input name="size" type="radio" value="large"/>
<button name="action" value="cancel">Cancel</button>
<input type="submit" value="Select"/>
"""
    assert err == ""


page_with_radio = '''
<html>
  <form method="post">
    <input type=checkbox name="foo" value="bacon"> This is a checkbox
  </form>
</html>
'''


def test_form_check_uncheck():
    browser = mechanicalsoup.StatefulBrowser()
    browser.open_fake_page(page_with_radio, url="http://example.com/invalid/")
    form = browser.select_form('form')
    assert "checked" not in form.form.find("input", {"name": "foo"}).attrs

    form["foo"] = True
    assert form.form.find("input", {"name": "foo"}).attrs["checked"] == ""

    # Test explicit unchecking (skipping the call to Form.uncheck_all)
    form.set_checkbox({"foo": False}, uncheck_other_boxes=False)
    assert "checked" not in form.form.find("input", {"name": "foo"}).attrs


page_with_various_fields = '''
<html>
  <form method="post">
    <input name="foo">
    <textarea name="bar">
    </textarea>
    <select name="entree">
      <option value="tofu" selected="selected">  Tofu Stir Fry </option>
      <option value="curry">    Red Curry</option>
      <option value="tempeh">Tempeh Tacos    </option>
    </select>
    <fieldset>
     <legend> Pizza Toppings </legend>
     <p><label> <input type=checkbox name="topping"
      value="bacon"> Bacon </label></p>
     <p><label> <input type=Checkbox name="topping"
      value="cheese" checked>Extra Cheese   </label></p>
     <p><label> <input type=checkbox name="topping"
      value="onion" checked> Onion </label></p>
     <p><label> <input type=checkbox name="topping"
      value="mushroom"> Mushroom </label></p>
    </fieldset>
    <p><input name="size" type=Radio value="small">Small</p>
    <p><input name="size" type=radio value="medium">Medium</p>
    <p><input name="size" type=radio value="large">Large</p>
    <button name="action"  value="cancel">Cancel</button>
    <input type="submit" value="Select" />
  </form>
</html>
'''


def test_form_print_summary(capsys):
    browser = mechanicalsoup.StatefulBrowser()
    browser.open_fake_page(page_with_various_fields,
                           url="http://example.com/invalid/")
    browser.select_form("form")
    browser.form.print_summary()
    out, err = capsys.readouterr()
    # Different versions of bs4 show either <input></input> or
    # <input/>. Normalize before comparing.
    out = out.replace('></input>', '/>')
    assert out == """<input name="foo"/>
<textarea name="bar"></textarea>
<select name="entree">
<option selected="selected" value="tofu">Tofu Stir Fry</option>
<option value="curry">Red Curry</option>
<option value="tempeh">Tempeh Tacos</option>
</select>
<input name="topping" type="checkbox" value="bacon"/>
<input checked="" name="topping" type="Checkbox" value="cheese"/>
<input checked="" name="topping" type="checkbox" value="onion"/>
<input name="topping" type="checkbox" value="mushroom"/>
<input name="size" type="Radio" value="small"/>
<input name="size" type="radio" value="medium"/>
<input name="size" type="radio" value="large"/>
<button name="action" value="cancel">Cancel</button>
<input type="submit" value="Select"/>
"""
    assert err == ""


def test_issue180():
    """Test that a KeyError is not raised when Form.choose_submit is called
    on a form where a submit element is missing its name-attribute."""
    browser = mechanicalsoup.StatefulBrowser()
    html = '''
<form>
  <input type="submit" value="Invalid" />
  <input type="submit" name="valid" value="Valid" />
</form>
'''
    browser.open_fake_page(html)
    form = browser.select_form()
    with pytest.raises(mechanicalsoup.utils.LinkNotFoundError):
        form.choose_submit('not_found')


def test_issue158():
    """Test that form elements are processed in their order on the page
    and that elements with duplicate name-attributes are not clobbered."""
    issue158_form = '''
<form method="post" action="mock://form.com/post">
  <input name="box" type="hidden" value="1"/>
  <input checked="checked" name="box" type="checkbox" value="2"/>
  <input name="box" type="hidden" value="0"/>
  <input type="submit" value="Submit" />
</form>
'''
    expected_post = [('box', '1'), ('box', '2'), ('box', '0')]
    browser, url = setup_mock_browser(expected_post=expected_post,
                                      text=issue158_form)
    browser.open(url)
    browser.select_form()
    res = browser.submit_selected()
    assert res.status_code == 200 and res.text == 'Success!'
    browser.close()


def test_duplicate_submit_buttons():
    """Tests that duplicate submits doesn't break form submissions
    See issue https://github.com/MechanicalSoup/MechanicalSoup/issues/264"""
    issue264_form = '''
<form method="post" action="mock://form.com/post">
  <input name="box" type="hidden" value="1"/>
  <input name="search" type="submit" value="Search"/>
  <input name="search" type="submit" value="Search"/>
</form>
'''
    expected_post = [('box', '1'), ('search', 'Search')]
    browser, url = setup_mock_browser(expected_post=expected_post,
                                      text=issue264_form)
    browser.open(url)
    browser.select_form()
    res = browser.submit_selected()
    assert res.status_code == 200 and res.text == 'Success!'
    browser.close()


@pytest.mark.parametrize("expected_post", [
    pytest.param([('sub2', 'val2')], id='submit button'),
    pytest.param([('sub4', 'val4')], id='typeless button'),
    pytest.param([('sub5', 'val5')], id='submit input'),
])
def test_choose_submit_buttons(expected_post):
    """Buttons of type reset and button are not valid submits"""
    text = """
    <form method="post" action="mock://form.com/post">
      <button type="butTon" name="sub1" value="val1">Val1</button>
      <button type="suBmit" name="sub2" value="val2">Val2</button>
      <button type="reset" name="sub3" value="val3">Val3</button>
      <button name="sub4" value="val4">Val4</button>
      <input type="subMit" name="sub5" value="val5">
    </form>
    """
    browser, url = setup_mock_browser(expected_post=expected_post, text=text)
    browser.open(url)
    browser.select_form()
    res = browser.submit_selected(btnName=expected_post[0][0])
    assert res.status_code == 200 and res.text == 'Success!'


@pytest.mark.parametrize("fail, selected, expected_post", [
    pytest.param(False, 'with_value', [('selector', 'with_value')],
                 id='Option with value'),
    pytest.param(False, 'Without value', [('selector', 'Without value')],
                 id='Option without value'),
    pytest.param(False, 'We have a value here', [('selector', 'with_value')],
                 id='Option with value selected by its text'),
    pytest.param(True, 'Unknown option', None,
                 id='Unknown option, must raise a LinkNotFound exception')
])
def test_option_without_value(fail, selected, expected_post):
    """Option tag in select can have no value option"""
    text = """
    <form method="post" action="mock://form.com/post">
      <select name="selector">
        <option value="with_value">We have a value here</option>
        <option>Without value</option>
      </select>
      <button type="submit">Submit</button>
    </form>
    """
    browser, url = setup_mock_browser(expected_post=expected_post,
                                      text=text)
    browser.open(url)
    browser.select_form()
    if fail:
        with pytest.raises(mechanicalsoup.utils.LinkNotFoundError):
            browser['selector'] = selected
    else:
        browser['selector'] = selected

        res = browser.submit_selected()
        assert res.status_code == 200 and res.text == 'Success!'


# ---------------------------------------------------------------------------
# Stateful forms: separate default/current values, dirty tracking and reset.
# ---------------------------------------------------------------------------

stateful_form_html = '''
<html>
  <form method="post" action="mock://form.com/post">
    <input type="text" name="text" value="text-default" />
    <input type="text" name="blank" />
    <input type="checkbox" name="cb" value="a" checked="checked" />
    <input type="checkbox" name="cb" value="b" />
    <input type="radio" name="radio" value="x" checked="checked" />
    <input type="radio" name="radio" value="y" />
    <textarea name="area">area-default</textarea>
    <select name="single">
      <option value="s1" selected="selected">one</option>
      <option value="s2">two</option>
    </select>
    <select name="multi" multiple="multiple">
      <option value="m1">m1</option>
      <option value="m2" selected="selected">m2</option>
      <option value="m3">m3</option>
    </select>
    <input type="file" name="upload" />
    <input type="text" name="disabled-field" value="kept"
      disabled="disabled" />
    <input type="submit" value="Go" />
  </form>
</html>
'''


def _form_data(form):
    """Extract the submitted data list the way Browser.submit would."""
    kwargs = mechanicalsoup.Browser.get_request_kwargs(
        form.form, "http://example.com/")
    return kwargs["data"]


def test_stateful_form_initial_defaults_match_dom():
    form = mechanicalsoup.Form(
        bs4.BeautifulSoup(stateful_form_html, "html.parser").form,
        stateful=True)
    assert form.stateful is True
    assert form.is_dirty() is False
    assert _form_data(form) == [
        ('text', 'text-default'),
        ('blank', ''),
        ('cb', 'a'),
        ('radio', 'x'),
        ('area', 'area-default'),
        ('single', 's1'),
        ('multi', 'm2'),
        ('upload', ''),
    ]


def test_stateful_two_fills_update_defaults_and_reset():
    """Drive one form through two fills, a default update and a reset,
    checking the actually submitted payload at every step."""
    browser, url = setup_mock_browser(text=stateful_form_html)
    browser.open(url)
    form = browser.select_form("form", stateful=True)

    # First set of conditions.
    form.set("text", "first")
    form.set("cb", ["b"])
    form.set("radio", "y")
    form.set("area", "first-area")
    form.set("single", "s2")
    form.set("multi", ["m1", "m3"])
    assert form.is_dirty() is True
    assert form.is_dirty("blank") is False
    assert _form_data(form) == [
        ('text', 'first'), ('blank', ''), ('cb', 'b'),
        ('radio', 'y'), ('area', 'first-area'),
        ('single', 's2'), ('multi', 'm1'), ('multi', 'm3'),
        ('upload', ''),
    ]

    # Second set of conditions on the same form.
    form.set("text", "second")
    form.set("cb", ["a", "b"])
    form.set("radio", "x")
    assert _form_data(form) == [
        ('text', 'second'), ('blank', ''),
        ('cb', 'a'), ('cb', 'b'),
        ('radio', 'x'), ('area', 'first-area'),
        ('single', 's2'), ('multi', 'm1'), ('multi', 'm3'),
        ('upload', ''),
    ]

    # Change defaults: dirty controls keep their edit, clean ones follow.
    form.update_defaults({
        "text": "new-text-default",
        "blank": "new-blank-default",
        "cb": ["a"],
        "radio": "y",
        "area": "new-area-default",
        "single": "s1",
        "multi": ["m2"],
    })
    assert form.is_dirty() is True
    # Dirty "text" still shows the user's second edit.
    assert form.form.find("input", {"name": "text"})["value"] == "second"
    # Clean "blank" follows its new default immediately.
    assert form.form.find("input", {"name": "blank"})["value"] == \
        "new-blank-default"

    # Reset restores the new defaults in-memory, without a page request.
    form.reset()
    assert form.is_dirty() is False
    assert _form_data(form) == [
        ('text', 'new-text-default'),
        ('blank', 'new-blank-default'),
        ('cb', 'a'),
        ('radio', 'y'),
        ('area', 'new-area-default'),
        ('single', 's1'),
        ('multi', 'm2'),
        ('upload', ''),
    ]


def test_stateful_reset_clears_file_but_keeps_disabled_and_order():
    form = mechanicalsoup.Form(
        bs4.BeautifulSoup(stateful_form_html, "html.parser").form,
        stateful=True)
    order_before = [
        tag.get("name") for tag in
        form.form.select("input[name], textarea[name], select[name]")
    ]
    upload = form.form.find("input", {"name": "upload"})
    upload["value"] = "/etc/secret"
    form.set("text", "edited")

    form.reset()

    # File selection must always be cleared on reset.
    assert upload.has_attr("value") is False
    # The disabled attribute is untouched.
    assert form.form.find(
        "input", {"name": "disabled-field"}).has_attr("disabled")
    # Control ordering is untouched.
    order_after = [
        tag.get("name") for tag in
        form.form.select("input[name], textarea[name], select[name]")
    ]
    assert order_after == order_before


def test_stateful_radio_exclusivity_and_multi_select_consistency():
    """The checked/selected sets visible through the DOM (read interface)
    and the submitted payload must never disagree, including radio
    exclusivity and multi-select collections."""
    form = mechanicalsoup.Form(
        bs4.BeautifulSoup(stateful_form_html, "html.parser").form,
        stateful=True)

    form.set("radio", "y")
    checked = [
        tag.get("value") for tag in
        form.form.select('input[type="radio" i][name="radio"]')
        if tag.has_attr("checked")
    ]
    assert checked == ["y"]
    assert [v for k, v in _form_data(form) if k == "radio"] == ["y"]

    form.set("multi", ["m1", "m3"])
    selected = [
        option.get("value") for option in
        form.form.find("select", {"name": "multi"}).find_all("option")
        if option.has_attr("selected")
    ]
    assert selected == ["m1", "m3"]
    assert [v for k, v in _form_data(form) if k == "multi"] == [
        "m1", "m3"]

    # Reset brings both back to the single original default.
    form.reset()
    assert [v for k, v in _form_data(form) if k == "radio"] == ["x"]
    assert [v for k, v in _form_data(form) if k == "multi"] == ["m2"]


def test_stateful_sync_defaults_after_external_dom_edit():
    form = mechanicalsoup.Form(
        bs4.BeautifulSoup(stateful_form_html, "html.parser").form,
        stateful=True)
    form.set("text", "user-edit")
    assert form.is_dirty() is True

    # Edit the soup directly, bypassing the Form setters.
    form.form.find("input", {"name": "text"})["value"] = "external-edit"
    # Without syncing, reset discards the external edit.
    form.reset()
    assert form.form.find("input", {"name": "text"})["value"] == \
        "text-default"

    form.form.find("input", {"name": "text"})["value"] = "external-edit"
    form.sync_defaults()
    assert form.is_dirty() is False
    form.reset()
    assert form.form.find("input", {"name": "text"})["value"] == \
        "external-edit"


def test_stateful_sync_defaults_registers_externally_added_controls():
    form = mechanicalsoup.Form(
        bs4.BeautifulSoup('<form><input name="x" value="1"></form>',
                          "html.parser").form,
        stateful=True)
    soup = bs4.BeautifulSoup("", "html.parser")
    new_input = soup.new_tag("input")
    new_input["name"] = "y"
    new_input["value"] = "y0"
    form.form.append(new_input)

    form.sync_defaults()
    assert form.is_dirty() is False

    form.set("y", "changed")
    assert form.form.find("input", {"name": "y"})["value"] == "changed"
    form.reset()
    assert form.form.find("input", {"name": "y"})["value"] == "y0"


def test_stateful_submit_uses_current_state():
    # Note: requests drops entries whose value is an empty string, so the
    # blank text input and the cleared file input are not transmitted.
    expected_post = [
        ('text', 'submitted'),
        ('cb', 'b'),
        ('radio', 'y'),
        ('area', 'submitted-area'),
        ('single', 's2'),
        ('multi', 'm1'), ('multi', 'm3'),
    ]
    browser, url = setup_mock_browser(
        expected_post=expected_post, text=stateful_form_html)
    browser.open(url)
    form = browser.select_form("form", stateful=True)
    form.set("text", "submitted")
    form.set("cb", ["b"])
    form.set("radio", "y")
    form.set("area", "submitted-area")
    form.set("single", "s2")
    form.set("multi", ["m1", "m3"])
    response = browser.submit_selected()
    assert response.status_code == 200 and response.text == 'Success!'


def test_non_stateful_form_keeps_dom_mutation_behavior():
    soup = bs4.BeautifulSoup(stateful_form_html, "html.parser")
    form = mechanicalsoup.Form(soup.form)
    assert form.stateful is False
    form.set("text", "in-place")
    # Historical behavior: the DOM tag itself is mutated.
    assert soup.form.find("input", {"name": "text"})["value"] == "in-place"
    # State-management APIs are unavailable in default mode.
    with pytest.raises(RuntimeError):
        form.reset()
    with pytest.raises(RuntimeError):
        form.update_defaults({"text": "x"})
    with pytest.raises(RuntimeError):
        form.sync_defaults()
    with pytest.raises(RuntimeError):
        form.is_dirty()


def test_stateful_update_defaults_unknown_choice_raises():
    form = mechanicalsoup.Form(
        bs4.BeautifulSoup(stateful_form_html, "html.parser").form,
        stateful=True)
    form.set("radio", "y")  # make it dirty
    with pytest.raises(mechanicalsoup.LinkNotFoundError):
        form.update_defaults({"radio": "no-such-choice"})
    # The current edit is untouched after the failed update.
    assert form.form.find(
        "input", {"value": "y"}).has_attr("checked")


if __name__ == '__main__':
    pytest.main(sys.argv)
