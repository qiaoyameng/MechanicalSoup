from collections.abc import Mapping, Iterator
from contextlib import contextmanager
import copy
import io
from dataclasses import dataclass, field
from typing import Any, Literal
import warnings

import bs4

from .utils import LinkNotFoundError, is_multipart_file_upload


class InvalidFormMethod(LinkNotFoundError):
    """This exception is raised when a method of :class:`Form` is used
    for an HTML element that is of the wrong type (or is malformed).
    It is caught within :func:`Form.set` to perform element type deduction.

    It is derived from :class:`LinkNotFoundError` so that a single base class
    can be used to catch all exceptions specific to this module.
    """
    pass


# Sentinel marking that a control has no value/selected/checked attribute in
# the DOM (as opposed to an attribute explicitly set to an empty string).
_MISSING = object()


@dataclass
class _ControlState:
    """State tracked for a single control in stateful mode.

    A control is one of: an ``<input>`` (other than checkboxes/radios), a
    ``<textarea>``, a ``<select>``, or a whole family of checkboxes/radios
    sharing the same name. ``tags`` holds every DOM element making up the
    control, ``default`` its current default value (a snapshot that can be
    re-applied to the DOM), and ``dirty`` whether the user edited it.
    """

    tags: list[bs4.element.Tag] = field(default_factory=list)
    default: Any = None
    dirty: bool = False

    def snapshot(self) -> Any:
        """Read a snapshot of the control's current value from the DOM."""
        tag = self.tags[0]
        if tag.name == "input":
            input_type = tag.get("type", "").lower()
            if input_type in ("checkbox", "radio"):
                return [
                    option.attrs.get("value", "on")
                    for option in self.tags
                    if "checked" in option.attrs
                ]
            if "value" in tag.attrs:
                return tag.attrs["value"]
            return _MISSING
        if tag.name == "textarea":
            return tag.text
        # <select>: the set of selected option values.
        return [
            option.get("value", option.text)
            for option in tag.find_all("option")
            if "selected" in option.attrs
        ]

    def apply(self, snapshot: Any) -> None:
        """Write a snapshot previously produced by :func:`snapshot` to DOM."""
        tag = self.tags[0]
        if tag.name == "input":
            input_type = tag.get("type", "").lower()
            if input_type in ("checkbox", "radio"):
                checked = set(snapshot)
                for option in self.tags:
                    if option.attrs.get("value", "on") in checked:
                        option["checked"] = ""
                    elif "checked" in option.attrs:
                        del option.attrs["checked"]
            elif snapshot is _MISSING and "value" in tag.attrs:
                del tag.attrs["value"]
            elif snapshot is not _MISSING:
                tag["value"] = snapshot
        elif tag.name == "textarea":
            tag.string = snapshot
        else:
            selected = set(snapshot)
            for option in tag.find_all("option"):
                if option.get("value", option.text) in selected:
                    option.attrs["selected"] = "selected"
                elif "selected" in option.attrs:
                    del option.attrs["selected"]


class _IdentityMap:
    """Map mutable DOM tags to their :class:`_ControlState`.

    BeautifulSoup's :class:`~bs4.element.Tag` implements ``__hash__`` from
    its contents, so it cannot be used as a dict key once attributes (such
    as ``value`` or ``checked``) change. Keying on ``id(tag)`` gives stable
    object-identity lookups regardless of DOM mutations.
    """

    def __init__(self) -> None:
        self._data: dict[int, tuple[bs4.element.Tag, _ControlState]] = {}

    def __setitem__(
        self, tag: bs4.element.Tag, state: _ControlState,
    ) -> None:
        self._data[id(tag)] = (tag, state)

    def get(self, tag: bs4.element.Tag) -> _ControlState | None:
        entry = self._data.get(id(tag))
        return entry[1] if entry is not None else None

    def values(self) -> Iterator[_ControlState]:
        for _tag, state in self._data.values():
            yield state

    def purge_decomposed(self) -> None:
        self._data = {
            identity: (tag, state)
            for identity, (tag, state) in self._data.items()
            if tag.parent is not None
        }


class Form:
    """Build a fillable form.

    :param form: A bs4.element.Tag corresponding to an HTML form element.
    :param stateful: When False (default), setting a field keeps the
        historical behavior of mutating the BeautifulSoup tag in place.
        When True, default and current values are maintained separately so
        that :func:`reset`, :func:`update_defaults` and
        :func:`sync_defaults` can manage the form without re-requesting
        the page.

    The Form class is responsible for preparing HTML forms for submission.
    It handles the following types of elements:
    input (text, checkbox, radio), select, and textarea.

    Each type is set by a method named after the type (e.g.
    :func:`~Form.set_select`), and then there are convenience methods (e.g.
    :func:`~Form.set`) that do type-deduction and set the value using the
    appropriate method.

    It also handles submit-type elements using :func:`~Form.choose_submit`.
    """

    def __init__(self, form: bs4.element.Tag, stateful: bool = False):
        if form.name != 'form':
            warnings.warn(
                f"Constructed a Form from a '{form.name}' instead of a 'form' "
                " element. This may be an error in a future version of "
                "MechanicalSoup.", FutureWarning)

        self.form = form
        self._submit_chosen: bs4.element.Tag | Literal[False] | None = None
        self._stateful = bool(stateful)

        # Stateful mode only. Maps every DOM element making up a control to
        # the shared _ControlState of that control, so dirty/default data can
        # be looked up from any tag in the control. Keyed by object identity
        # because Tag hashing follows its (mutable) contents.
        self._control_states = _IdentityMap()
        self._suspend_dirty = False
        if self._stateful:
            self._register_all_controls()

        # Aliases for backwards compatibility
        # (Included specifically in __init__ to suppress them in Sphinx docs)
        self.attach = self.set_input
        self.input = self.set_input
        self.textarea = self.set_textarea

    @property
    def stateful(self) -> bool:
        """Whether this form keeps separate default and current control
        values. When True, :func:`reset`, :func:`update_defaults` and
        :func:`sync_defaults` are available and the original DOM defaults are
        preserved instead of being overwritten in place.
        """
        return self._stateful

    def _register_all_controls(self) -> None:
        """Snapshot every control currently present in the form."""
        seen: set[tuple] = set()
        for tag in self.form.find_all(("input", "textarea", "select")):
            input_type = tag.get("type", "").lower()
            if input_type in ("checkbox", "radio"):
                name = tag.get("name")
                key = (input_type, name)
                if key in seen:
                    continue
                seen.add(key)
                selector = 'input[type="{}" i][name="{}"]'.format(
                    input_type, name)
                family = self.form.select(selector)
                self._register_control(family)
            else:
                self._register_control([tag])

    def _register_control(self, tags: list[bs4.element.Tag]) -> _ControlState:
        state = _ControlState(tags=list(tags))
        state.default = state.snapshot()
        for tag in tags:
            self._control_states[tag] = state
        return state

    def _purge_decomposed_controls(self) -> None:
        """Drop state for controls whose DOM elements left the form."""
        self._control_states.purge_decomposed()

    def _state_for(self, tag: bs4.element.Tag) -> _ControlState | None:
        return self._control_states.get(tag)

    def _mark_dirty(self, tags: list[bs4.element.Tag]) -> None:
        """Flag the control(s) containing ``tags`` as edited by the user."""
        if not self._stateful or self._suspend_dirty:
            return
        for tag in tags:
            state = self._control_states.get(tag)
            if state is not None:
                state.dirty = True

    @contextmanager
    def _without_dirty_marking(self) -> Iterator[None]:
        """Run DOM mutations without touching dirty flags.

        Used by :func:`update_defaults`, which re-applies defaults to
        non-dirty controls through the regular setters.
        """
        previous = self._suspend_dirty
        self._suspend_dirty = True
        try:
            yield
        finally:
            self._suspend_dirty = previous

    def _require_stateful(self, operation: str) -> None:
        if not self._stateful:
            raise RuntimeError(
                f"Form.{operation} is only available for stateful forms; "
                "construct the Form with stateful=True (or call "
                "select_form(..., stateful=True))."
            )

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

        for (name, value) in data.items():
            i = self.form.find("input", {"name": name})
            if not i:
                raise InvalidFormMethod("No input field named " + name)
            self._assert_valid_file_upload(i, value)
            i["value"] = value
            self._mark_dirty([i])

    def uncheck_all(self, name: str) -> None:
        """Remove the *checked*-attribute of all input elements with
        a *name*-attribute given by ``name``.
        """
        options = self.form.find_all("input", {"name": name})
        for option in options:
            if "checked" in option.attrs:
                del option.attrs["checked"]
        self._mark_dirty(options)

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
        for (name, value) in data.items():
            # Case-insensitive search for type=checkbox
            selector = 'input[type="checkbox" i][name="{}"]'.format(name)
            checkboxes = self.form.select(selector)
            if not checkboxes:
                raise InvalidFormMethod("No input checkbox named " + name)

            # uncheck if requested
            if uncheck_other_boxes:
                self.uncheck_all(name)
            self._mark_dirty(checkboxes)

            # Wrap individual values (e.g. int, str) in a 1-element tuple.
            if not isinstance(value, list) and not isinstance(value, tuple):
                value = (value,)

            # Check or uncheck one or more boxes
            for choice in value:
                choice_str = str(choice)  # Allow for example literal numbers
                for checkbox in checkboxes:
                    if checkbox.attrs.get("value", "on") == choice_str:
                        checkbox["checked"] = ""
                        break
                    # Allow specifying True or False to check/uncheck
                    elif choice is True:
                        checkbox["checked"] = ""
                        break
                    elif choice is False:
                        if "checked" in checkbox.attrs:
                            del checkbox.attrs["checked"]
                        break
                else:
                    raise LinkNotFoundError(
                        "No input checkbox named %s with choice %s" %
                        (name, choice)
                    )

    def set_radio(self, data: Mapping[str, Any]) -> None:
        """Set the *checked*-attribute of input elements of type "radio"
        specified by ``data`` (i.e. select radio buttons).

        :param data: Dict of ``{name: value, ...}``.
            In the family of radio buttons whose *name*-attribute is ``name``,
            check the radio button whose *value*-attribute is ``value``.
            Only one radio button in the family can be checked.
        """
        for (name, value) in data.items():
            # Case-insensitive search for type=radio
            selector = 'input[type="radio" i][name="{}"]'.format(name)
            radios = self.form.select(selector)
            if not radios:
                raise InvalidFormMethod("No input radio named " + name)

            # only one radio button can be checked
            self.uncheck_all(name)
            self._mark_dirty(radios)

            # Check the appropriate radio button (value cannot be a list/tuple)
            for radio in radios:
                if radio.attrs.get("value", "on") == str(value):
                    radio["checked"] = ""
                    break
            else:
                raise LinkNotFoundError(
                    f"No input radio named {name} with choice {value}"
                )

    def set_textarea(self, data: Mapping[str, Any]) -> None:
        """Set the *string*-attribute of the first textarea element
        specified by ``data`` (i.e. set the text of a textarea).

        :param data: Dict of ``{name: value, ...}``.
            The textarea whose *name*-attribute is ``name`` will have
            its *string*-attribute set to ``value``.
        """
        for (name, value) in data.items():
            t = self.form.find("textarea", {"name": name})
            if not t:
                raise InvalidFormMethod("No textarea named " + name)
            t.string = value
            self._mark_dirty([t])

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
        for (name, value) in data.items():
            select = self.form.find("select", {"name": name})
            if not select:
                raise InvalidFormMethod("No select named " + name)
            self._mark_dirty([select])

            # Deselect all options first
            for option_ in select.find_all("option"):
                if "selected" in option_.attrs:
                    del option_.attrs["selected"]

            # Wrap individual values in a 1-element tuple.
            # If value is a list/tuple, select must be a <select multiple>.
            if not isinstance(value, list) and not isinstance(value, tuple):
                value = (value,)
            elif "multiple" not in select.attrs:
                raise LinkNotFoundError("Cannot select multiple options!")

            for choice in value:
                option = select.find("option", {"value": choice})

                # try to find with text instead of value
                if not option:
                    option = select.find("option", string=choice)

                if not option:
                    raise LinkNotFoundError(
                        f'Option {choice} not found for select {name}'
                    )

                option.attrs["selected"] = "selected"

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

        Example: uploading a file through a ``<input type="file"
        name="tagname">`` field (provide an open file object,
        and its content will be uploaded):

        .. code-block:: python

            form.set("tagname", open(path_to_local_file, "rb"))

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
        if self._stateful:
            self._purge_decomposed_controls()
        # We don't have access to the original soup object (just the
        # Tag), so we instantiate a new BeautifulSoup() to call
        # new_tag(). We're only building the soup object, not parsing
        # anything, so the parser doesn't matter. Specify the one
        # included in Python to avoid having dependency issue.
        control = bs4.BeautifulSoup("", "html.parser").new_tag('input')
        control['type'] = type
        control['name'] = name
        control['value'] = value
        for k, v in kwargs.items():
            control[k] = v
        self._assert_valid_file_upload(control, value)
        self.form.append(control)
        if self._stateful:
            self._register_control([control])
        return control

    def reset(self) -> None:
        """Restore every control to its current default value.

        Only available for a stateful form (``stateful=True``). This works
        purely from the in-memory state, without re-requesting the page:

        * text-like inputs and textareas get their default value back;
        * checkboxes and radios return to their default checked set;
        * single- and multi-selects return to their default selection;
        * file inputs are always cleared (a browser cannot pre-select a
          file), even if a value was present in the original HTML;
        * the ``disabled`` attribute and the ordering of controls are left
          untouched.

        All dirty flags are cleared afterwards.
        """
        self._require_stateful("reset")
        for state in self._iter_unique_states():
            tag = state.tags[0]
            if (
                tag.name == "input"
                and tag.get("type", "").lower() == "file"
            ):
                # Browsers never restore a file selection on reset.
                tag.attrs.pop("value", None)
            else:
                state.apply(state.default)
            state.dirty = False

    def update_defaults(self, data: Mapping[str, Any] | None = None) -> None:
        """Change the default values used by :func:`reset`.

        Only available for a stateful form.

        :param data: Dict of ``{name: value, ...}`` using the same value
            conventions as :func:`set` (e.g. a list/tuple for checkboxes and
            ``<select multiple>``). If omitted or empty, the defaults are
            refreshed from every control's current DOM value.

        Controls the user has not edited (not dirty) are updated in place:
        their current value follows the new default. Dirty controls keep
        their in-progress edit and only remember the new default for the
        next :func:`reset`.
        """
        self._require_stateful("update_defaults")
        if not data:
            self.sync_defaults()
            return

        for name, value in data.items():
            state = self._resolve_control_state(name)
            if state.dirty:
                # Remember the new default, but preserve the user's edit.
                state.default = self._compute_default(name, value)
            else:
                # Reuse the exact setter path so read/write/submission can
                # never diverge; this must not flip the dirty flag.
                with self._without_dirty_marking():
                    self.set(name, value)
                state.default = state.snapshot()

    def sync_defaults(self) -> None:
        """Take the current DOM values as the new defaults.

        Only available for a stateful form. Call this after modifying the
        underlying BeautifulSoup tags directly (outside the ``set_*``
        methods), including controls added straight to the DOM: the
        external edit becomes the baseline that :func:`reset` restores,
        and every control's dirty flag is cleared.
        """
        self._require_stateful("sync_defaults")
        self._register_unseen_controls()
        for state in self._iter_unique_states():
            state.default = state.snapshot()
            state.dirty = False

    def _register_unseen_controls(self) -> None:
        """Register controls that were added to the DOM after construction."""
        for tag in self.form.find_all(("input", "textarea", "select")):
            if self._control_states.get(tag) is not None:
                continue
            self._register_control(self._family_of(tag))

    def is_dirty(self, name: str | None = None) -> bool:
        """Report whether controls hold user edits.

        :param name: If given, report the state of the control (or
            checkbox/radio family) with that name. Otherwise report whether
            any control in the form is dirty.
        """
        self._require_stateful("is_dirty")
        states = list(self._iter_unique_states())
        if name is None:
            return any(state.dirty for state in states)
        matching = [
            state for state in states
            if any(tag.get("name") == name for tag in state.tags)
        ]
        if not matching:
            raise LinkNotFoundError("No control named " + name)
        return any(state.dirty for state in matching)

    def _iter_unique_states(self) -> Iterator[_ControlState]:
        seen: set[int] = set()
        for state in self._control_states.values():
            if id(state) in seen:
                continue
            seen.add(id(state))
            yield state

    def _primary_tag(self, name: str) -> bs4.element.Tag:
        """Find the representative tag of the control named ``name``."""
        for func in ("checkbox", "radio", "input", "textarea", "select"):
            try:
                return getattr(self, "_find_" + func)(name)
            except InvalidFormMethod:
                continue
        raise LinkNotFoundError("No valid element named " + name)

    def _resolve_control_state(
        self, name: str,
    ) -> _ControlState:
        tag = self._primary_tag(name)
        state = self._state_for(tag)
        if state is None:
            # A control added directly to the DOM after construction.
            state = self._register_control(self._family_of(tag))
        return state

    def _family_of(self, tag: bs4.element.Tag) -> list[bs4.element.Tag]:
        input_type = tag.get("type", "").lower()
        if tag.name == "input" and input_type in ("checkbox", "radio"):
            selector = 'input[type="{}" i][name="{}"]'.format(
                input_type, tag.get("name"))
            return self.form.select(selector)
        return [tag]

    def _find_checkbox(self, name: str) -> bs4.element.Tag:
        tag = self.form.select_one(
            'input[type="checkbox" i][name="{}"]'.format(name))
        if not tag:
            raise InvalidFormMethod("No input checkbox named " + name)
        return tag

    def _find_radio(self, name: str) -> bs4.element.Tag:
        tag = self.form.select_one(
            'input[type="radio" i][name="{}"]'.format(name))
        if not tag:
            raise InvalidFormMethod("No input radio named " + name)
        return tag

    def _find_input(self, name: str) -> bs4.element.Tag:
        tag = self.form.find("input", {"name": name})
        if not tag:
            raise InvalidFormMethod("No input field named " + name)
        return tag

    def _find_textarea(self, name: str) -> bs4.element.Tag:
        tag = self.form.find("textarea", {"name": name})
        if not tag:
            raise InvalidFormMethod("No textarea named " + name)
        return tag

    def _find_select(self, name: str) -> bs4.element.Tag:
        tag = self.form.find("select", {"name": name})
        if not tag:
            raise InvalidFormMethod("No select named " + name)
        return tag

    def _compute_default(self, name: str, value: Any) -> Any:
        """Translate a user-supplied value into a default snapshot.

        This applies the same value conventions and validation as the
        ``set_*`` methods, without touching the current DOM, so a dirty
        control keeps its edit while its future reset baseline changes.
        """
        tag = self._primary_tag(name)
        if tag.name == "input":
            input_type = tag.get("type", "").lower()
            if input_type in ("checkbox", "radio"):
                selector = 'input[type="{}" i][name="{}"]'.format(
                    input_type, name)
                family = self.form.select(selector)
                available = [
                    option.attrs.get("value", "on") for option in family
                ]
                if input_type == "radio":
                    choice = str(value)
                    if choice not in available:
                        raise LinkNotFoundError(
                            f"No input radio named {name} with choice {value}"
                        )
                    return [choice]
                choices = value
                if not isinstance(choices, (list, tuple)):
                    choices = (choices,)
                checked: list[str] = []
                for choice in choices:
                    choice_str = str(choice)
                    for option, option_value in zip(family, available):
                        if option_value == choice_str or choice is True:
                            if option_value not in checked:
                                checked.append(option_value)
                            break
                        if choice is False:
                            break
                    else:
                        raise LinkNotFoundError(
                            "No input checkbox named %s with choice %s" %
                            (name, choice)
                        )
                return checked
            if input_type == "file":
                # A reset always clears file inputs; no default value exists.
                return _MISSING
            return value
        if tag.name == "textarea":
            return value
        # <select>
        choices = value
        if not isinstance(choices, (list, tuple)):
            choices = (choices,)
        elif "multiple" not in tag.attrs:
            raise LinkNotFoundError("Cannot select multiple options!")
        selected: list[str] = []
        for choice in choices:
            option = tag.find("option", {"value": choice})
            if not option:
                option = tag.find("option", string=choice)
            if not option:
                raise LinkNotFoundError(
                    f'Option {choice} not found for select {name}'
                )
            option_value = option.get("value", option.text)
            if option_value not in selected:
                selected.append(option_value)
        return selected

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
            return self._submit_chosen

        # All buttons NOT of type (button,reset) are valid submits
        # Case-insensitive search for type=submit
        inps = [
            inp for inp in self.form.select('input[type="submit" i], button')
            if (
                not isinstance(inp_type := inp.get("type"), str)
                or inp_type.lower() not in ('button', 'reset')
            )
        ]

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

    def _assert_valid_file_upload(
        self,
        tag: bs4.element.Tag,
        value: Any,
    ) -> None:
        """Raise an exception if a multipart file input is not an open file."""
        if (
            is_multipart_file_upload(self.form, tag) and
            not isinstance(value, io.IOBase)
        ):
            raise ValueError(
                "From v1.3.0 onwards, you must pass an open file object "
                'directly, e.g. `form["name"] = open("/path/to/file", "rb")`. '
                "This change is to remediate a security vulnerability where "
                "a malicious web server could read arbitrary files from the "
                "client (CVE-2023-34457)."
            )
