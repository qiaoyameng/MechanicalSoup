from collections.abc import Mapping
import copy
import json
from typing import Any, Callable, Literal
import warnings

import bs4

from .form_edits import (
    FormEditApplyResult,
    FormEditConflict,
    FormEditHistory,
    UnsupportedFormEditError,
    parse_form_edit_record,
)
from .utils import LinkNotFoundError


class InvalidFormMethod(LinkNotFoundError):
    """This exception is raised when a method of :class:`Form` is used
    for an HTML element that is of the wrong type (or is malformed).
    It is caught within :func:`Form.set` to perform element type deduction.

    It is derived from :class:`LinkNotFoundError` so that a single base class
    can be used to catch all exceptions specific to this module.
    """
    pass


class Form:
    """Build a fillable form.

    :param form: A bs4.element.Tag corresponding to an HTML form element.

    The Form class is responsible for preparing HTML forms for submission.
    It handles the following types of elements:
    input (text, checkbox, radio), select, and textarea.

    Each type is set by a method named after the type (e.g.
    :func:`~Form.set_select`), and then there are convenience methods (e.g.
    :func:`~Form.set`) that do type-deduction and set the value using the
    appropriate method.

    It also handles submit-type elements using :func:`~Form.choose_submit`.
    """

    def __init__(self, form: bs4.element.Tag):
        if form.name != 'form':
            warnings.warn(
                f"Constructed a Form from a '{form.name}' instead of a 'form' "
                " element. This may be an error in a future version of "
                "MechanicalSoup.", FutureWarning)

        self.form = form
        self._submit_chosen: bs4.element.Tag | Literal[False] | None = None
        self._edit_history = FormEditHistory()

        # Aliases for backwards compatibility
        # (Included specifically in __init__ to suppress them in Sphinx docs)
        self.attach = self.set_input
        self.input = self.set_input
        self.textarea = self.set_textarea

    @property
    def edit_history(self) -> FormEditHistory:
        """Get the explicit edits recorded through Form's fill methods."""
        return self._edit_history

    def export_form_edits(self, as_json: bool = False, **kwargs: Any) -> (
            dict[str, Any] | str):
        """Export explicit edits as a versioned JSON-serializable record."""
        if as_json:
            return self._edit_history.to_json(**kwargs)
        return self._edit_history.to_dict()

    def load_form_edits(
        self,
        record: Mapping[str, Any] | str,
    ) -> FormEditApplyResult:
        """Validate and store a form edit record without applying it."""
        try:
            history = FormEditHistory.from_dict(record)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            conflict = FormEditConflict("invalid_record", str(exc))
            return FormEditApplyResult(False, [conflict])

        self._edit_history = history
        return FormEditApplyResult(True, applied_edits=len(history.edits))

    def apply_form_edits(
        self,
        record: Mapping[str, Any] | str | None = None,
    ) -> FormEditApplyResult:
        """Apply a record to this form, or the record already loaded on it."""
        if record is None:
            edits = self._edit_history.edits
        else:
            try:
                edits = parse_form_edit_record(record)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                conflict = FormEditConflict("invalid_record", str(exc))
                return FormEditApplyResult(False, [conflict])

        conflicts: list[FormEditConflict] = []
        for index, edit in enumerate(edits):
            try:
                conflicts.extend(self._validate_edit(edit, index))
            except (TypeError, ValueError) as exc:
                conflicts.append(FormEditConflict(
                    "invalid_record", str(exc), index,
                    edit.get("name") if isinstance(edit, Mapping) else None))

        if conflicts:
            return FormEditApplyResult(False, conflicts)

        pending = [(self._build_edit_applier(edit), edit) for edit in edits]
        previous_submit = self._submit_chosen
        form_before = copy.deepcopy(self.form)
        try:
            for applier, _ in pending:
                applier()
        except Exception as exc:
            self.form.replace_with(form_before)
            self.form = form_before
            self._submit_chosen = previous_submit
            conflict = FormEditConflict("apply_failed", str(exc))
            return FormEditApplyResult(False, [conflict])

        return FormEditApplyResult(True, applied_edits=len(pending))

    @staticmethod
    def _input_type(tag: bs4.element.Tag) -> str:
        return str(tag.get("type", "text")).lower()

    @staticmethod
    def _control_value(tag: bs4.element.Tag) -> str:
        if tag.name == "button":
            return str(tag.get("value", ""))
        if Form._input_type(tag) in ("checkbox", "radio"):
            return str(tag.get("value", "on"))
        return str(tag.get("value", ""))

    @staticmethod
    def _option_value(option: bs4.element.Tag) -> str:
        value = option.get("value")
        if value is None:
            return option.get_text(strip=True)
        return str(value)

    def _group_index(
        self,
        tags: list[bs4.element.Tag],
        tag: bs4.element.Tag,
    ) -> int:
        return next(index for index, candidate in enumerate(tags)
                    if candidate is tag)

    def _find_group(
        self,
        tag_name: str,
        name: str,
        selector_suffix: str = "",
    ) -> list[bs4.element.Tag]:
        selector = f'{tag_name}[name="{name}"]{selector_suffix}'
        return self.form.select(selector)

    def _find_input_group(
        self,
        name: str,
        input_type: str,
    ) -> list[bs4.element.Tag]:
        if input_type == "text":
            return [
                control for control in self._find_group("input", name)
                if self._input_type(control) == "text"
            ]
        return self._find_group(
            "input", name, f'[type="{input_type}" i]')

    def _submit_controls(self) -> list[bs4.element.Tag]:
        return [
            control for control in
            self.form.select('input[type="submit" i], button')
            if (
                not isinstance(control_type := control.get("type"), str) or
                control_type.lower() not in ("button", "reset")
            )
        ]

    def _assert_editable_input(self, tag: bs4.element.Tag,
                               name: str) -> None:
        control_type = self._input_type(tag)
        if control_type == "hidden":
            raise UnsupportedFormEditError(
                "Recording edits to hidden field " + name +
                " is not supported"
            )
        if control_type == "file":
            raise UnsupportedFormEditError(
                "Recording file uploads for field " + name +
                " is not supported"
            )

    def _record_edit(self, edit: Mapping[str, Any]) -> None:
        self._edit_history.add(edit)

    def _validate_edit(
        self,
        edit: Mapping[str, Any],
        operation: int,
    ) -> list[FormEditConflict]:
        edit_type = edit.get("type")
        name = edit.get("name")
        if not isinstance(name, str):
            return [FormEditConflict(
                "invalid_edit", "Edit name must be a string",
                operation, None)]

        if edit_type in ("text", "textarea"):
            return self._validate_text_edit(edit, operation, name, edit_type)
        if edit_type == "checkbox":
            return self._validate_checkbox_edit(edit, operation, name)
        if edit_type == "radio":
            return self._validate_radio_edit(edit, operation, name)
        if edit_type == "select":
            return self._validate_select_edit(edit, operation, name)
        if edit_type == "submit":
            return self._validate_submit_edit(edit, operation, name)
        return [FormEditConflict(
            "unknown_edit_type", f"Unknown edit type: {edit_type}",
            operation, name)]

    def _group_conflict(
        self,
        operation: int,
        name: str,
        expected_type: str,
        expected_count: int,
        actual: list[bs4.element.Tag],
        check_count: bool = True,
    ) -> FormEditConflict | None:
        if not actual:
            return FormEditConflict(
                "control_missing",
                f"No {expected_type} control named {name}",
                operation, name)
        if check_count and len(actual) != expected_count:
            return FormEditConflict(
                "group_count_changed",
                f"{expected_type} group {name} changed from "
                f"{expected_count} to {len(actual)} controls",
                operation, name,
                {"expected_count": expected_count,
                 "actual_count": len(actual)})
        return None

    def _group_count_conflict(
        self,
        operation: int,
        name: str,
        expected_type: str,
        expected_count: int,
        actual: list[bs4.element.Tag],
    ) -> FormEditConflict | None:
        if len(actual) != expected_count:
            return FormEditConflict(
                "group_count_changed",
                f"{expected_type} group {name} changed from "
                f"{expected_count} to {len(actual)} controls",
                operation, name,
                {"expected_count": expected_count,
                 "actual_count": len(actual)})
        return None

    def _validate_text_edit(
        self,
        edit: Mapping[str, Any],
        operation: int,
        name: str,
        edit_type: str,
    ) -> list[FormEditConflict]:
        if edit_type == "text":
            expected_type = f'input[type="{edit.get("input_type", "text")}" i]'
            group = self._find_input_group(
                name, str(edit.get("input_type", "text")))
            if not group:
                return [FormEditConflict(
                    "type_changed",
                    f"Text control {name} changed type or disappeared",
                    operation, name)]
        else:
            expected_type = "textarea"
            group = self._find_group("textarea", name)
            if not group:
                return [FormEditConflict(
                    "type_changed",
                    f"Textarea {name} changed type or disappeared",
                    operation, name)]
        conflict = self._group_conflict(
            operation, name, expected_type,
            int(edit.get("group_count", 0)), group)
        if conflict is not None:
            return [conflict]
        if int(edit.get("index", 0)) >= len(group):
            return [FormEditConflict(
                "control_missing", "Control index is out of range",
                operation, name)]
        if edit.get("value") != str(edit.get("value")):
            return [FormEditConflict(
                "invalid_value", "Text value must be a string",
                operation, name)]
        return []

    def _validate_choice_values(
        self,
        operation: int,
        name: str,
        expected_values: Any,
        actual_values: list[str],
    ) -> FormEditConflict | None:
        if not isinstance(expected_values, list) or not all(
                isinstance(value, str) for value in expected_values):
            return FormEditConflict(
                "invalid_candidate_values",
                "Candidate values must be a list of strings",
                operation, name)
        if set(expected_values) != set(actual_values):
            return FormEditConflict(
                "candidate_values_changed",
                f"Candidate values for {name} changed",
                operation, name,
                {"expected_values": list(expected_values),
                 "actual_values": actual_values})
        return None

    def _validate_checkbox_edit(
        self,
        edit: Mapping[str, Any],
        operation: int,
        name: str,
    ) -> list[FormEditConflict]:
        group = self._find_group(
            "input", name, '[type="checkbox" i]')
        conflict = self._group_conflict(
            operation, name, "checkbox",
            int(edit.get("group_count", 0)), group,
            check_count=False)
        if conflict is not None:
            return [conflict]
        conflicts = []
        count_conflict = self._group_count_conflict(
            operation, name, "checkbox",
            int(edit.get("group_count", 0)), group)
        if count_conflict is not None:
            conflicts.append(count_conflict)

        values = [self._control_value(control) for control in group]
        conflict = self._validate_choice_values(
            operation, name, edit.get("candidate_values"), values)
        if conflict is not None:
            conflicts.append(conflict)

        actions = edit.get("actions")
        if not isinstance(actions, list):
            return conflicts + [FormEditConflict(
                "invalid_edit", "Checkbox actions must be a list",
                operation, name)]

        for action in actions:
            if not isinstance(action, dict):
                return conflicts + [FormEditConflict(
                    "invalid_edit", "Each checkbox action must be an object",
                    operation, name)]
            selected = action.get("selected")
            action_type = action.get("type")
            if action_type == "all":
                if not isinstance(selected, bool):
                    conflicts.append(FormEditConflict(
                        "invalid_value",
                        "Checkbox all-action selection must be boolean",
                        operation, name))
                if int(action.get("index", -1)) >= len(group):
                    conflicts.append(FormEditConflict(
                        "control_missing", "Checkbox index is out of range",
                        operation, name))
            elif action_type == "value":
                if action.get("value") not in values:
                    conflicts.append(FormEditConflict(
                        "candidate_missing",
                        f"Checkbox candidate {action.get('value')} is gone",
                        operation, name))
            else:
                conflicts.append(FormEditConflict(
                    "invalid_edit",
                    f"Unknown checkbox action: {action_type}",
                    operation, name))
        return conflicts

    def _validate_radio_edit(
        self,
        edit: Mapping[str, Any],
        operation: int,
        name: str,
    ) -> list[FormEditConflict]:
        group = self._find_group("input", name, '[type="radio" i]')
        conflict = self._group_conflict(
            operation, name, "radio",
            int(edit.get("group_count", 0)), group,
            check_count=False)
        if conflict is not None:
            return [conflict]
        conflicts = []
        count_conflict = self._group_count_conflict(
            operation, name, "radio",
            int(edit.get("group_count", 0)), group)
        if count_conflict is not None:
            conflicts.append(count_conflict)
        values = [self._control_value(control) for control in group]
        conflict = self._validate_choice_values(
            operation, name, edit.get("candidate_values"), values)
        if conflict is not None:
            conflicts.append(conflict)
        if edit.get("value") not in values:
            conflicts.append(FormEditConflict(
                "candidate_missing",
                f"Radio candidate {edit.get('value')} is gone",
                operation, name))
        return conflicts

    def _validate_select_edit(
        self,
        edit: Mapping[str, Any],
        operation: int,
        name: str,
    ) -> list[FormEditConflict]:
        group = self._find_group("select", name)
        conflict = self._group_conflict(
            operation, name, "select",
            int(edit.get("group_count", 0)), group,
            check_count=False)
        if conflict is not None:
            return [conflict]
        conflicts = []
        count_conflict = self._group_count_conflict(
            operation, name, "select",
            int(edit.get("group_count", 0)), group)
        if count_conflict is not None:
            conflicts.append(count_conflict)
        if int(edit.get("index", 0)) >= len(group):
            return conflicts + [FormEditConflict(
                "control_missing", "Select index is out of range",
                operation, name)]
        select = group[int(edit.get("index", 0))]
        actual_multiple = "multiple" in select.attrs
        if bool(edit.get("multiple")) != actual_multiple:
            return [FormEditConflict(
                "type_changed",
                f"Select {name} multiple attribute changed",
                operation, name,
                {"expected_multiple": bool(edit.get("multiple")),
                 "actual_multiple": actual_multiple})]
        values = [self._option_value(option)
                  for option in select.find_all("option")]
        conflict = self._validate_choice_values(
            operation, name, edit.get("candidate_values"), values)
        if conflict is not None:
            conflicts.append(conflict)
        selected = edit.get("values")
        if not isinstance(selected, list) or any(
                value not in values for value in selected):
            conflicts.append(FormEditConflict(
                "candidate_missing",
                "One or more selected options are gone",
                operation, name))
        return conflicts

    def _validate_submit_edit(
        self,
        edit: Mapping[str, Any],
        operation: int,
        name: str,
    ) -> list[FormEditConflict]:
        if edit.get("selected") is False:
            return []
        controls = self._submit_controls()
        conflicts = []
        conflict = self._group_conflict(
            operation, name, "submit",
            int(edit.get("group_count", 0)), controls,
            check_count=False)
        if conflict is not None:
            conflicts.append(conflict)
            return conflicts
        count_conflict = self._group_count_conflict(
            operation, name, "submit",
            int(edit.get("group_count", 0)), controls)
        if count_conflict is not None:
            conflicts.append(count_conflict)
        matching = [
            control for control in controls
            if control.name == edit.get("tag") and
            control.get("name") == name and
            self._control_value(control) == edit.get("value")
        ]
        if not matching:
            conflicts.append(FormEditConflict(
                "candidate_missing", "Submit candidate value changed",
                operation, name))
        return conflicts

    def _build_edit_applier(
        self,
        edit: Mapping[str, Any],
    ) -> Callable[[], None]:
        edit_type = edit["type"]
        if edit_type == "text":
            group = self._find_input_group(
                str(edit["name"]), str(edit["input_type"]))
            control = group[int(edit["index"])]
            return lambda: control.__setitem__("value", edit["value"])
        if edit_type == "textarea":
            group = self._find_group("textarea", str(edit["name"]))
            control = group[int(edit["index"])]
            return lambda: setattr(control, "string", edit["value"])
        if edit_type == "checkbox":
            group = self._find_group(
                "input", str(edit["name"]), '[type="checkbox" i]')
            actions = list(edit["actions"])
            uncheck_other = bool(edit.get("uncheck_other_boxes"))
            return lambda: self._apply_recorded_checkbox(
                group, actions, uncheck_other)
        if edit_type == "radio":
            group = self._find_group(
                "input", str(edit["name"]), '[type="radio" i]')
            value = edit["value"]
            return lambda: self._apply_radio(group, value)
        if edit_type == "select":
            group = self._find_group("select", str(edit["name"]))
            select = group[int(edit["index"])]
            values = list(edit["values"])
            return lambda: self._apply_select(select, values)
        if edit_type == "submit":
            if edit.get("selected") is False:
                return lambda: setattr(self, "_submit_chosen", False)
            controls = self._submit_controls()
            control = next(
                candidate for candidate in controls
                if candidate.name == edit["tag"] and
                candidate.get("name") == edit["name"] and
                self._control_value(candidate) == edit["value"])
            return lambda: setattr(self, "_submit_chosen", control)
        raise UnsupportedFormEditError(f"Unknown edit type: {edit_type}")

    @staticmethod
    def _apply_recorded_checkbox(
        group: list[bs4.element.Tag],
        actions: list[Mapping[str, Any]],
        uncheck_other_boxes: bool,
    ) -> None:
        if uncheck_other_boxes:
            Form._uncheck_controls(group)
        Form._apply_checkbox_actions(group, actions)

    @staticmethod
    def _apply_checkbox_actions(
        group: list[bs4.element.Tag],
        actions: list[Mapping[str, Any]],
    ) -> None:
        for action in actions:
            if action["type"] == "all":
                control = group[int(action["index"])]
                selected = bool(action["selected"])
            else:
                control = next(
                    candidate for candidate in group
                    if Form._control_value(candidate) == action["value"])
                selected = bool(action["selected"])
            if selected:
                control["checked"] = ""
            elif "checked" in control.attrs:
                del control.attrs["checked"]

    @staticmethod
    def _apply_radio(
        group: list[bs4.element.Tag],
        value: str,
    ) -> None:
        for control in group:
            if "checked" in control.attrs:
                del control.attrs["checked"]
        for control in group:
            if Form._control_value(control) == value:
                control["checked"] = ""
                break

    @staticmethod
    def _apply_select(
        select: bs4.element.Tag,
        values: list[str],
    ) -> None:
        for option in select.find_all("option"):
            if "selected" in option.attrs:
                del option.attrs["selected"]
        for value in values:
            for option in select.find_all("option"):
                if Form._option_value(option) == value:
                    option["selected"] = "selected"
                    break

    @property
    def submit_chosen(self) -> bs4.element.Tag | Literal[False] | None:
        """Get the currently selected submit element."""
        return self._submit_chosen

    def set_input(self, data: Mapping[str, Any]) -> None:
        """Fill-in a set of fields in a form.

        Example: filling-in a login/password form

        .. code-block:: python

           form.set_input({"login": username, "password": password})

        This will find the input element named "login" and give it the
        value ``username``, and the input element named "password" and
        give it the value ``password``.
        """

        planned: list[tuple[bs4.element.Tag, Any, dict[str, Any]]] = []
        for (name, value) in data.items():
            control = self.form.find("input", {"name": name})
            if not control:
                raise InvalidFormMethod("No input field named " + name)
            self._assert_editable_input(control, name)
            if self._input_type(control) == "file":
                raise UnsupportedFormEditError(
                    "Recording file uploads for field " + name +
                    " is not supported"
                )
            input_type = self._input_type(control)
            group = self._find_input_group(name, input_type)
            planned.append((control, value, {
                "type": "text",
                "name": name,
                "input_type": input_type,
                "index": self._group_index(group, control),
                "group_count": len(group),
                "value": str(value),
            }))

        for control, value, edit in planned:
            control["value"] = value
            self._record_edit(edit)

    def uncheck_all(self, name: str) -> None:
        """Remove the *checked*-attribute of all input elements with
        a *name*-attribute given by ``name``.
        """
        checkboxes = self._find_group(
            "input", name, '[type="checkbox" i]')
        if not checkboxes:
            raise InvalidFormMethod("No input checkbox named " + name)
        self._uncheck_controls(checkboxes)
        self._record_edit({
            "type": "checkbox",
            "name": name,
            "index": 0,
            "group_count": len(checkboxes),
            "candidate_values": [self._control_value(control)
                                  for control in checkboxes],
            "actions": [],
            "uncheck_other_boxes": True,
        })

    @staticmethod
    def _uncheck_controls(controls: list[bs4.element.Tag]) -> None:
        for option in controls:
            if "checked" in option.attrs:
                del option.attrs["checked"]

    def check(self, data: Mapping[str, Any]) -> None:
        """For backwards compatibility, this method handles checkboxes
        and radio buttons in a single call. It will not uncheck any
        checkboxes unless explicitly specified by ``data``, in contrast
        with the default behavior of :func:`~Form.set_checkbox`.
        """
        for (name, value) in data.items():
            try:
                self.set_checkbox({name: value}, uncheck_other_boxes=False)
                continue
            except InvalidFormMethod:
                pass
            try:
                self.set_radio({name: value})
                continue
            except InvalidFormMethod:
                pass
            raise LinkNotFoundError("No input checkbox/radio named " + name)

    def set_checkbox(
        self,
        data: Mapping[str, Any],
        uncheck_other_boxes: bool = True,
    ) -> None:
        """Set the *checked*-attribute of input elements of type "checkbox"
        specified by ``data`` (i.e. check boxes).

        :param data: Dict of ``{name: value, ...}``.
            In the family of checkboxes whose *name*-attribute is ``name``,
            check the box whose *value*-attribute is ``value``. All boxes in
            the family can be checked (unchecked) if ``value`` is True (False).
            To check multiple specific boxes, let ``value`` be a tuple or list.
        :param uncheck_other_boxes: If True (default), before checking any
            boxes specified by ``data``, uncheck the entire checkbox family.
            Consider setting to False if some boxes are checked by default when
            the HTML is served.
        """
        planned: list[tuple[list[bs4.element.Tag], list[Any],
                            dict[str, Any]]] = []
        for (name, value) in data.items():
            checkboxes = self._find_group(
                "input", name, '[type="checkbox" i]')
            if not checkboxes:
                raise InvalidFormMethod("No input checkbox named " + name)

            actions = self._plan_checkbox_actions(checkboxes, name, value)
            planned.append((checkboxes, actions, {
                "type": "checkbox",
                "name": name,
                "index": 0,
                "group_count": len(checkboxes),
                "candidate_values": [self._control_value(control)
                                      for control in checkboxes],
                "actions": actions,
                "uncheck_other_boxes": uncheck_other_boxes,
            }))

        for checkboxes, actions, edit in planned:
            if uncheck_other_boxes:
                self._uncheck_controls(checkboxes)
            self._apply_checkbox_actions(checkboxes, actions)
            self._record_edit(edit)

    def _plan_checkbox_actions(
        self,
        checkboxes: list[bs4.element.Tag],
        name: str,
        value: Any,
    ) -> list[dict[str, Any]]:
        choices = value
        if not isinstance(choices, (list, tuple)):
            choices = (choices,)

        actions: list[dict[str, Any]] = []
        for choice in choices:
            if choice is False:
                actions.extend({
                    "type": "all",
                    "index": index,
                    "selected": False,
                } for index in range(len(checkboxes)))
                continue
            if choice is True:
                actions.append({
                    "type": "all",
                    "index": 0,
                    "selected": True,
                })
                continue

            choice_str = str(choice)
            checkbox = next((
                candidate for candidate in checkboxes
                if self._control_value(candidate) == choice_str
            ), None)
            if checkbox is None:
                raise LinkNotFoundError(
                    "No input checkbox named %s with choice %s" %
                    (name, choice)
                )
            actions.append({
                "type": "value",
                "value": choice_str,
                "selected": True,
            })
        return actions

    def set_radio(self, data: Mapping[str, Any]) -> None:
        """Set the *checked*-attribute of input elements of type "radio"
        specified by ``data`` (i.e. select radio buttons).

        :param data: Dict of ``{name: value, ...}``.
            In the family of radio buttons whose *name*-attribute is ``name``,
            check the radio button whose *value*-attribute is ``value``.
            Only one radio button in the family can be checked.
        """
        planned: list[tuple[list[bs4.element.Tag], str, dict[str, Any]]] = []
        for (name, value) in data.items():
            radios = self._find_group("input", name, '[type="radio" i]')
            if not radios:
                raise InvalidFormMethod("No input radio named " + name)
            value_str = str(value)
            if not any(self._control_value(radio) == value_str
                       for radio in radios):
                raise LinkNotFoundError(
                    f"No input radio named {name} with choice {value}"
                )
            planned.append((radios, value_str, {
                "type": "radio",
                "name": name,
                "index": 0,
                "group_count": len(radios),
                "candidate_values": [self._control_value(radio)
                                      for radio in radios],
                "value": value_str,
            }))

        for radios, value_str, edit in planned:
            self._uncheck_controls(radios)
            self._apply_radio(radios, value_str)
            self._record_edit(edit)

    def set_textarea(self, data: Mapping[str, Any]) -> None:
        """Set the *string*-attribute of the first textarea element
        specified by ``data`` (i.e. set the text of a textarea).

        :param data: Dict of ``{name: value, ...}``.
            The textarea whose *name*-attribute is ``name`` will have
            its *string*-attribute set to ``value``.
        """
        planned: list[tuple[bs4.element.Tag, Any, dict[str, Any]]] = []
        for (name, value) in data.items():
            group = self._find_group("textarea", name)
            if not group:
                raise InvalidFormMethod("No textarea named " + name)
            control = group[0]
            planned.append((control, value, {
                "type": "textarea",
                "name": name,
                "index": 0,
                "group_count": len(group),
                "value": str(value),
            }))

        for control, value, edit in planned:
            control.string = value
            self._record_edit(edit)

    def set_select(self, data: Mapping[str, Any]) -> None:
        """Set the *selected*-attribute of the first option element
        specified by ``data`` (i.e. select an option from a dropdown).

        :param data: Dict of ``{name: value, ...}``.
            Find the select element whose *name*-attribute is ``name``.
            Then select from among its children the option element whose
            *value*-attribute is ``value``. If no matching *value*-attribute
            is found, this will search for an option whose text matches
            ``value``. If the select element's *multiple*-attribute is set,
            then ``value`` can be a list or tuple to select multiple options.
        """
        planned: list[tuple[bs4.element.Tag, list[str], dict[str, Any]]] = []
        for (name, value) in data.items():
            group = self._find_group("select", name)
            if not group:
                raise InvalidFormMethod("No select named " + name)
            select = group[0]
            choices = value
            if not isinstance(choices, (list, tuple)):
                choices = (choices,)
            elif "multiple" not in select.attrs:
                raise LinkNotFoundError("Cannot select multiple options!")

            values: list[str] = []
            for choice in choices:
                option = select.find("option", {"value": choice})
                if option is None:
                    option = select.find("option", string=choice)
                if option is None:
                    raise LinkNotFoundError(
                        f'Option {choice} not found for select {name}'
                    )
                values.append(self._option_value(option))

            planned.append((select, values, {
                "type": "select",
                "name": name,
                "index": 0,
                "group_count": len(group),
                "multiple": "multiple" in select.attrs,
                "candidate_values": [
                    self._option_value(option)
                    for option in select.find_all("option")
                ],
                "values": values,
            }))

        for select, values, edit in planned:
            self._apply_select(select, values)
            self._record_edit(edit)

    def __setitem__(self, name: str, value: Any) -> None:
        """Forwards arguments to :func:`~Form.set`. For example,
        :code:`form["name"] = "value"` calls :code:`form.set("name", "value")`.
        """
        return self.set(name, value)

    def set(self, name: str, value: Any, force: bool = False) -> None:
        """Set a form element identified by ``name`` to a specified ``value``.
        The type of element (input, textarea, select, ...) does not
        need to be given; it is inferred by the following methods:
        :func:`~Form.set_checkbox`,
        :func:`~Form.set_radio`,
        :func:`~Form.set_input`,
        :func:`~Form.set_textarea`,
        :func:`~Form.set_select`.
        If none of these methods find a matching element, then if ``force``
        is True, a new element (``<input type="text" ...>``) will be
        added using :func:`~Form.new_control`.

        Example: filling-in a login/password form with EULA checkbox

        .. code-block:: python

            form.set("login", username)
            form.set("password", password)
            form.set("eula-checkbox", True)

        """
        for func in ("checkbox", "radio", "input", "textarea", "select"):
            try:
                getattr(self, "set_" + func)({name: value})
                return
            except InvalidFormMethod:
                pass
        if force:
            self.new_control('text', name, value=value)
            return
        raise LinkNotFoundError("No valid element named " + name)

    def new_control(
        self,
        type: str,
        name: str,
        value: Any,
        **kwargs: Any,
    ) -> bs4.element.Tag:
        """Add a new input element to the form.

        The arguments set the attributes of the new element.
        """
        # Remove existing input-like elements with the same name
        for tag in ('input', 'textarea', 'select'):
            for old in self.form.find_all(tag, {'name': name}):
                old.decompose()
        # We don't have access to the original soup object (just the
        # Tag), so we instantiate a new BeautifulSoup() to call
        # new_tag(). We're only building the soup object, not parsing
        # anything, so the parser doesn't matter. Specify the one
        # included in Python to avoid having dependency issue.
        control_type = type.lower()
        if control_type == "hidden":
            raise UnsupportedFormEditError(
                "Recording hidden controls is not supported"
            )
        if control_type == "file":
            raise UnsupportedFormEditError(
                "Recording file controls is not supported"
            )

        control = bs4.BeautifulSoup("", "html.parser").new_tag('input')
        control['type'] = type
        control['name'] = name
        control['value'] = value
        for k, v in kwargs.items():
            control[k] = v
        self.form.append(control)
        if control_type not in ("checkbox", "radio", "textarea", "select",
                                "button", "reset", "submit", "image"):
            group = self._find_input_group(name, control_type)
            self._record_edit({
                "type": "text",
                "name": name,
                "input_type": control_type,
                "index": self._group_index(group, control),
                "group_count": len(group),
                "value": str(value),
            })
        return control

    def choose_submit(
        self,
        submit: bs4.element.Tag | str | Literal[False] | None,
    ) -> bs4.element.Tag | Literal[False] | None:
        """Selects the input (or button) element to use for form submission.

        :param submit: The :class:`bs4.element.Tag` (or just its
            *name*-attribute) that identifies the submit element to use. If
            ``None``, will choose the first valid submit element in the form,
            if one exists. If ``False``, will not use any submit element;
            this is useful for simulating AJAX requests, for example.

        To simulate a normal web browser, only one submit element must be
        sent. Therefore, this does not need to be called if there is only
        one submit element in the form.

        If the element is not found or if multiple elements match, raise a
        :class:`LinkNotFoundError` exception.

        Example: ::

            browser = mechanicalsoup.StatefulBrowser()
            browser.open(url)
            form = browser.select_form()
            form.choose_submit('form_name_attr')
            browser.submit_selected()
        """
        if submit is False:
            self._submit_chosen = False
            self._record_edit({
                "type": "submit",
                "name": "",
                "selected": False,
            })
            return self._submit_chosen

        # All buttons NOT of type (button,reset) are valid submits
        # Case-insensitive search for type=submit
        inps = self._submit_controls()

        submit_chosen = None

        # If no submit specified, choose the first one
        if submit is None and inps:
            submit_chosen = inps[0]

        for inp in inps:
            if (inp.has_attr('name') and inp['name'] == submit):
                # Found the tag element by name
                if submit_chosen is not None:
                    raise LinkNotFoundError(
                        f"Multiple submit elements match: {submit}"
                    )
                submit_chosen = inp
            elif inp == submit:
                # Found the exact input tag element
                submit_chosen = inp
                break

        if submit_chosen is None and submit not in (None, False):
            raise LinkNotFoundError(
                f"Specified submit element not found: {submit}"
            )
        self._submit_chosen = submit_chosen
        if submit_chosen is not None:
            self._record_edit({
                "type": "submit",
                "name": submit_chosen.get("name", ""),
                "selected": True,
                "tag": submit_chosen.name,
                "index": self._group_index(inps, submit_chosen),
                "group_count": len(inps),
                "value": self._control_value(submit_chosen),
            })

        return self._submit_chosen

    def print_summary(self) -> None:
        """Print a summary of the form.

        May help finding which fields need to be filled-in.
        """
        for input in self.form.find_all(
                ("input", "textarea", "select", "button")):
            input_copy = copy.copy(input)
            # Text between the opening tag and the closing tag often
            # contains a lot of spaces that we don't want here.
            for subtag in input_copy.find_all() + [input_copy]:
                if subtag.string:
                    subtag.string = subtag.string.strip()
            print(input_copy)
