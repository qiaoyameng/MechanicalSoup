import copy
import json

import pytest
import setpath  # noqa:F401, must come before 'import mechanicalsoup'

import mechanicalsoup


ORIGINAL_FORM = """
<form>
  <input type="hidden" name="csrf_token" value="token-v1" />
  <input type="date" name="export_date" value="2026-01-01" />
  <input type="text" name="region" value="default-region" />
  <textarea name="notes">default notes</textarea>
  <input type="radio" name="format" value="csv" checked />
  <input type="radio" name="format" value="pdf" />
  <input type="checkbox" name="sections" value="summary" checked />
  <input type="checkbox" name="sections" value="detail" />
  <input type="checkbox" name="sections" value="pdf" />
  <select name="layout">
    <option value="compact" selected>Compact</option>
    <option value="wide">Wide</option>
  </select>
  <select name="columns" multiple>
    <option value="a">A</option>
    <option value="b">B</option>
    <option value="c">C</option>
  </select>
  <button type="submit" name="save" value="save">Save</button>
  <button type="submit" name="preview" value="preview">Preview</button>
</form>
"""


def make_form(html=ORIGINAL_FORM):
    browser = mechanicalsoup.StatefulBrowser()
    browser.open_fake_page(html, url="file:///export-page-v1.html")
    return browser.select_form()


def filled_form():
    form = make_form()
    form.set_input({"region": "default-region"})
    form.set_textarea({"notes": "my notes"})
    form.set_radio({"format": "pdf"})
    form.set_checkbox({"sections": ("detail", "pdf")})
    form.set_select({"layout": "wide", "columns": ("b", "c")})
    form.choose_submit("save")
    return form


def test_export_records_only_explicit_supported_edits():
    form = filled_form()
    record = form.export_form_edits()

    assert record["version"] == 1
    assert record["format"] == "mechanicalsoup-form-edits"
    assert len(record["edits"]) == 7
    assert json.dumps(record)
    assert all("csrf_token" not in json.dumps(edit)
               for edit in record["edits"])
    assert record["edits"][0]["value"] == "default-region"
    assert record["edits"][2]["candidate_values"] == ["csv", "pdf"]
    assert record["edits"][3]["candidate_values"] == [
        "summary", "detail", "pdf"]
    assert record["edits"][-1] == {
        "type": "submit", "name": "save", "selected": True,
        "tag": "button", "index": 0, "group_count": 2,
        "value": "save",
    }


def test_apply_edits_preserves_new_defaults_and_hidden_token():
    form = filled_form()
    record = form.export_form_edits(as_json=True)

    new_html = (
        ORIGINAL_FORM
        .replace("token-v1", "token-v2")
        .replace("2026-01-01", "2026-02-02")
        .replace("default-region", "server-region")
        .replace("default notes", "server notes")
    )
    target = make_form(new_html)
    result = target.apply_form_edits(record)

    assert result.success
    assert result.applied_edits == 7
    assert target.form.find(
        "input", {"name": "csrf_token"})["value"] == "token-v2"
    assert target.form.find(
        "input", {"name": "export_date"})["value"] == "2026-02-02"
    assert target.form.find(
        "input", {"name": "region"})["value"] == "default-region"
    assert target.form.find(
        "textarea", {"name": "notes"}).string == "my notes"
    assert target.form.find(
        "input", {"name": "format", "value": "pdf"}).has_attr("checked")
    checked = [
        control["value"] for control in target.form.select(
            'input[type="checkbox"][name="sections"]')
        if control.has_attr("checked")
    ]
    assert set(checked) == {"detail", "pdf"}
    assert target.submit_chosen == target.form.find(
        "button", {"name": "save"})
    assert target.export_form_edits()["edits"] == []


def test_apply_edits_uses_values_after_candidate_reordering():
    form = filled_form()
    record = form.export_form_edits()
    reordered = ORIGINAL_FORM.replace(
        '<input type="checkbox" name="sections" value="summary" checked />\n'
        '  <input type="checkbox" name="sections" value="detail" />\n'
        '  <input type="checkbox" name="sections" value="pdf" />',
        '<input type="checkbox" name="sections" value="pdf" />\n'
        '  <input type="checkbox" name="sections" value="detail" />\n'
        '  <input type="checkbox" name="sections" value="summary" checked />'
    )
    target = make_form(reordered)

    assert target.apply_form_edits(record).success
    checked = [
        control["value"] for control in target.form.select(
            'input[type="checkbox"][name="sections"]')
        if control.has_attr("checked")
    ]
    assert set(checked) == {"detail", "pdf"}


def test_conflict_applies_nothing_and_preserves_submit_choice():
    form = filled_form()
    record = form.export_form_edits()
    conflict_html = ORIGINAL_FORM.replace(
        '<input type="checkbox" name="sections" value="pdf" />', '')
    target = make_form(conflict_html)
    target.choose_submit("preview")
    before = copy.deepcopy(target.form)
    before_submit = target.submit_chosen

    result = target.apply_form_edits(record)

    assert not result.success
    assert {conflict.code for conflict in result.conflicts} >= {
        "group_count_changed", "candidate_values_changed",
        "candidate_missing"}
    assert target.form.decode() == before.decode()
    assert target.submit_chosen is before_submit


def test_repeated_application_is_idempotent():
    form = filled_form()
    record = form.export_form_edits()
    target = make_form()

    first = target.apply_form_edits(record)
    first_html = copy.deepcopy(target.form)
    second = target.apply_form_edits(record)

    assert first.success and second.success
    assert target.form.decode() == first_html.decode()


def test_duplicate_same_type_controls_use_group_position():
    html = """
    <form>
      <input type="text" name="label" value="one" />
      <input type="text" name="label" value="two" />
      <select name="choice">
        <option value="a" selected>A</option>
        <option value="b">B</option>
      </select>
      <select name="choice">
        <option value="x" selected>X</option>
        <option value="y">Y</option>
      </select>
    </form>
    """
    source = make_form(html)
    labels = source.form.select('input[name="label"]')
    labels[1]["value"] = "manual"
    source._record_edit({
        "type": "text", "name": "label", "input_type": "text",
        "index": 1, "group_count": 2, "value": "updated-two",
    })
    source._record_edit({
        "type": "select", "name": "choice", "index": 1, "group_count": 2,
        "multiple": False,
        "candidate_values": ["x", "y"], "values": ["y"],
    })
    record = source.export_form_edits()

    target = make_form(html)
    result = target.apply_form_edits(record)

    assert result.success
    labels = target.form.select('input[name="label"]')
    assert labels[0]["value"] == "one"
    assert labels[1]["value"] == "updated-two"
    selected = [
        option["value"]
        for option in target.form.select('select[name="choice"] option')
        if option.has_attr("selected")
    ]
    assert selected == ["a", "y"]


def test_load_does_not_apply_edits():
    record = filled_form().export_form_edits()
    target = make_form()
    original_region = target.form.find(
        "input", {"name": "region"})["value"]

    result = target.load_form_edits(record)

    assert result.success
    assert target.form.find(
        "input", {"name": "region"})["value"] == original_region
    assert target.edit_history.edits == record["edits"]


def test_structured_conflicts_for_missing_and_type_changes():
    record = filled_form().export_form_edits()

    missing = make_form(ORIGINAL_FORM.replace(
        '<input type="text" name="region" value="default-region" />', ''))
    result = missing.apply_form_edits(record)
    assert not result.success
    assert result.conflicts[0].name == "region"
    assert result.conflicts[0].code == "type_changed"


def test_invalid_record_returns_structured_conflict():
    target = make_form()
    result = target.apply_form_edits({
        "version": 1,
        "format": "mechanicalsoup-form-edits",
        "edits": [{
            "type": "text", "name": "region", "input_type": "text",
            "index": "bad", "group_count": 1, "value": "x",
        }],
    })

    assert not result.success
    assert result.conflicts[0].code == "invalid_record"


def test_selected_submit_conflict_preserves_current_choice():
    record = filled_form().export_form_edits()
    target = make_form(ORIGINAL_FORM.replace(
        '<button type="submit" name="save" value="save">Save</button>', ''))
    target.choose_submit("preview")
    before = copy.deepcopy(target.form)

    result = target.apply_form_edits(record)

    assert not result.success
    assert "group_count_changed" in {
        conflict.code for conflict in result.conflicts}
    assert target.form.decode() == before.decode()
    assert target.submit_chosen.get("name") == "preview"


def test_submit_reordering_is_resolved_by_value():
    record = filled_form().export_form_edits()
    reordered = ORIGINAL_FORM.replace(
        '<button type="submit" name="save" value="save">Save</button>\n'
        '  <button type="submit" name="preview" value="preview">'
        'Preview</button>',
        '<button type="submit" name="preview" value="preview">'
        'Preview</button>\n'
        '  <button type="submit" name="save" value="save">Save</button>'
    )
    target = make_form(reordered)

    result = target.apply_form_edits(record)

    assert result.success
    assert target.submit_chosen.get("name") == "save"


def test_select_reordering_is_resolved_by_value():
    record = filled_form().export_form_edits()
    reordered = ORIGINAL_FORM.replace(
        '<option value="a">A</option>\n    <option value="b">B</option>\n'
        '    <option value="c">C</option>',
        '<option value="c">C</option>\n    <option value="a">A</option>\n'
        '    <option value="b">B</option>'
    )
    target = make_form(reordered)

    assert target.apply_form_edits(record).success
    assert [
        option["value"]
        for option in target.form.select(
            'select[name="columns"] option[selected]')
    ] == ["c", "b"]

    changed = make_form(ORIGINAL_FORM.replace(
        '<input type="text" name="region" value="default-region" />',
        '<textarea name="region">default-region</textarea>'))
    result = changed.apply_form_edits(record)
    assert not result.success
    assert result.conflicts[0].code == "type_changed"


def test_group_count_change_returns_structured_conflict():
    record = filled_form().export_form_edits()
    changed = make_form(ORIGINAL_FORM.replace(
        '<input type="radio" name="format" value="pdf" />',
        '<input type="radio" name="format" value="pdf" />\n'
        '  <input type="radio" name="format" value="xlsx" />'))

    result = changed.apply_form_edits(record)

    assert not result.success
    assert any(
        conflict.code == "group_count_changed" and
        conflict.name == "format"
        for conflict in result.conflicts)


def test_select_multiple_type_change_returns_conflict():
    record = filled_form().export_form_edits()
    changed = make_form(ORIGINAL_FORM.replace(
        '<select name="columns" multiple>',
        '<select name="columns">'))

    result = changed.apply_form_edits(record)

    assert not result.success
    assert any(
        conflict.code == "type_changed" and conflict.name == "columns"
        for conflict in result.conflicts)
    assert any(
        conflict.code == "type_changed" and
        conflict.name == "columns"
        for conflict in result.conflicts)


def test_hidden_and_file_controls_are_unsupported():
    form = make_form("""
    <form>
      <input type="hidden" name="token" value="one" />
      <input type="file" name="document" />
    </form>
    """)

    with pytest.raises(mechanicalsoup.UnsupportedFormEditError):
        form["token"] = "two"
    with pytest.raises(mechanicalsoup.UnsupportedFormEditError):
        form["document"] = "document.txt"
    with pytest.raises(mechanicalsoup.UnsupportedFormEditError):
        form.new_control("file", "other", "other.txt")

    assert form.export_form_edits()["edits"] == []


def test_manual_soup_edits_are_not_recorded():
    form = make_form()
    form.form.find("input", {"name": "region"})["value"] = "manual"
    assert form.export_form_edits()["edits"] == []


def test_checkbox_false_records_entire_group():
    form = make_form()
    form.set_checkbox({"sections": False}, uncheck_other_boxes=False)

    edit = form.export_form_edits()["edits"][0]
    assert edit["actions"] == [
        {"type": "all", "index": 0, "selected": False},
        {"type": "all", "index": 1, "selected": False},
        {"type": "all", "index": 2, "selected": False},
    ]

    target = make_form()
    assert target.apply_form_edits(form.export_form_edits()).success
    assert not [
        control for control in target.form.select(
            'input[type="checkbox"][name="sections"]')
        if control.has_attr("checked")
    ]


def test_stateful_browser_proxies_record_edits():
    browser = mechanicalsoup.StatefulBrowser()
    browser.open_fake_page(ORIGINAL_FORM)
    browser.select_form()
    browser["region"] = "browser-region"
    browser.set_textarea({"notes": "browser notes"})
    browser.set_checkbox({"sections": "detail"})
    browser.set_radio({"format": "csv"})
    browser.set_select({"layout": "compact"})

    record = browser.export_form_edits()
    assert [edit["name"] for edit in record["edits"]] == [
        "region", "notes", "sections", "format", "layout"]


def test_stateful_browser_export_page_demo():
    browser = mechanicalsoup.StatefulBrowser()
    browser.open_fake_page(ORIGINAL_FORM)
    browser.select_form()
    browser["region"] = "default-region"
    browser.set_checkbox({"sections": ("detail", "pdf")})
    record = browser.export_form_edits(as_json=True)

    refreshed = (
        ORIGINAL_FORM
        .replace("token-v1", "token-v2")
        .replace("2026-01-01", "2026-02-02")
        .replace("default-region", "server-region")
    )
    browser.open_fake_page(refreshed)
    browser.select_form()
    result = browser.apply_form_edits(record)

    assert result.success
    assert browser.form.form.find(
        "input", {"name": "csrf_token"})["value"] == "token-v2"
    assert browser.form.form.find(
        "input", {"name": "export_date"})["value"] == "2026-02-02"
    assert browser.form.form.find(
        "input", {"name": "region"})["value"] == "default-region"
    assert {
        control["value"]
        for control in browser.form.form.select(
            'input[type="checkbox"][name="sections"]')
        if control.has_attr("checked")
    } == {"detail", "pdf"}

    stale_page = refreshed.replace(
        '<input type="checkbox" name="sections" value="pdf" />', '')
    browser.open_fake_page(stale_page)
    browser.select_form()
    browser.choose_submit("preview")
    before = copy.deepcopy(browser.form.form)
    before_submit = browser.form.submit_chosen
    conflict_result = browser.apply_form_edits(record)

    assert not conflict_result.success
    assert browser.form.form.decode() == before.decode()
    assert browser.form.submit_chosen is before_submit
