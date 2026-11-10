"""Opt-in HTML form constraint validation.

This module implements a first version of client-side constraint
validation, modeled on the HTML standard's "constraint validation"
rules. It is purely observational: it never modifies the DOM and never
triggers any network request.

Covered constraints:

* ``required`` for text-like inputs, ``number``, ``date``, ``checkbox``,
  ``radio`` (group semantics), ``select`` and ``textarea``.
* ``minlength`` / ``maxlength`` for text-like inputs and ``textarea``.
* ``min`` / ``max`` / ``step`` for ``input[type=number]`` and
  ``input[type=date]``. Step arithmetic uses :class:`decimal.Decimal`
  (and integer day counts for dates) so that, e.g., ``0.3`` is correctly
  recognized as a multiple of step ``0.1``.

Controls that are barred from constraint validation are never reported:
disabled controls (including descendants of a disabled ``<fieldset>``,
except inside its first ``<legend>``), ``readonly`` controls, and
non-submittable button-like input types (``hidden``, ``submit``,
``reset``, ``button``, ``image``).

Not covered yet (these controls/attributes are silently ignored):
``input`` types ``email``/``url`` format checking, ``pattern``, ``range``,
``color``, ``file``, and the date-like types ``month``, ``week``,
``time`` and ``datetime-local`` (including their ``min``/``max``/``step``).
"""

import dataclasses
import datetime
from decimal import Decimal, InvalidOperation

import bs4

from .utils import LinkNotFoundError, is_disabled


# Input types that are barred from constraint validation.
_BARRED_TYPES = frozenset({"hidden", "submit", "reset", "button", "image"})

# Input types for which minlength/maxlength apply.
_TEXT_TYPES = frozenset({"text", "search", "tel", "url", "email", "password"})


@dataclasses.dataclass
class ConstraintViolation:
    """A single form constraint violation.

    :ivar control: The :class:`bs4.element.Tag` of the offending control.
    :ivar constraint: The violated constraint, named after the HTML
        ``ValidityState`` properties: ``"valueMissing"``, ``"tooShort"``,
        ``"tooLong"``, ``"typeMismatch"``, ``"rangeUnderflow"``,
        ``"rangeOverflow"`` or ``"stepMismatch"``.
    :ivar value: The control's actual (offending) value.
    """
    control: bs4.element.Tag
    constraint: str
    value: str

    @property
    def control_description(self) -> str:
        """A human-readable identity of the control, e.g.
        ``input[type="radio"][name="size"]``."""
        tag = self.control
        desc = tag.name
        if tag.name == "input":
            desc += '[type="{}"]'.format(tag.get("type", "text"))
        for attr in ("name", "id"):
            if tag.has_attr(attr):
                desc += f'[{attr}="{tag[attr]}"]'
        return desc

    def __str__(self) -> str:
        return (f'{self.control_description}: constraint '
                f'"{self.constraint}" violated with value {self.value!r}')


class FormValidationError(LinkNotFoundError):
    """Raised by :func:`Browser.submit` when constraint validation is
    enabled and the form contains invalid controls. Carries the full
    list of problems in :attr:`violations`, in document order, so the
    caller can fix everything in one pass.

    It is derived from :class:`LinkNotFoundError` so that a single base
    class can be used to catch all exceptions specific to MechanicalSoup.
    """
    def __init__(self, violations):
        self.violations = list(violations)
        message = "Form constraint validation failed:\n" + "\n".join(
            str(v) for v in self.violations)
        super().__init__(message)


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_decimal(value):
    try:
        result = Decimal(value)
    except (TypeError, InvalidOperation):
        return None
    return result if result.is_finite() else None


def _parse_date(value):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _option_value(option):
    return option.get("value") if option.has_attr("value") else option.text


def _check_text_constraints(tag, value, violations):
    """required/minlength/maxlength for text-like inputs and textarea."""
    if value == "":
        if tag.has_attr("required"):
            violations.append(ConstraintViolation(tag, "valueMissing", value))
        return
    minlength = _parse_int(tag.get("minlength"))
    if minlength is not None and len(value) < minlength:
        violations.append(ConstraintViolation(tag, "tooShort", value))
    maxlength = _parse_int(tag.get("maxlength"))
    if maxlength is not None and len(value) > maxlength:
        violations.append(ConstraintViolation(tag, "tooLong", value))


def _check_min_max_step(tag, parsed, parse, base_default, violations):
    """min/max/step checks common to number and date inputs."""
    min_value = parse(tag.get("min"))
    max_value = parse(tag.get("max"))
    if min_value is not None and parsed < min_value:
        violations.append(
            ConstraintViolation(tag, "rangeUnderflow", tag.get("value", "")))
    if max_value is not None and parsed > max_value:
        violations.append(
            ConstraintViolation(tag, "rangeOverflow", tag.get("value", "")))

    step_attr = tag.get("step")
    if step_attr is not None and step_attr.strip().lower() == "any":
        return
    step = _parse_decimal(step_attr) if step_attr is not None else Decimal(1)
    if step is None or step <= 0:
        return
    base = min_value if min_value is not None else base_default
    difference = parsed - base
    if isinstance(difference, datetime.timedelta):
        difference = Decimal(difference.days)
    # Decimal (resp. integer day) arithmetic: no binary float modulo, so
    # 0.3 is correctly a multiple of step 0.1.
    if difference % step != 0:
        violations.append(
            ConstraintViolation(tag, "stepMismatch", tag.get("value", "")))


def _validate_number(tag, value, violations):
    if value == "":
        if tag.has_attr("required"):
            violations.append(ConstraintViolation(tag, "valueMissing", value))
        return
    number = _parse_decimal(value)
    if number is None:
        violations.append(ConstraintViolation(tag, "typeMismatch", value))
        return
    _check_min_max_step(tag, number, _parse_decimal, Decimal(0), violations)


def _validate_date(tag, value, violations):
    if value == "":
        if tag.has_attr("required"):
            violations.append(ConstraintViolation(tag, "valueMissing", value))
        return
    date = _parse_date(value)
    if date is None:
        violations.append(ConstraintViolation(tag, "typeMismatch", value))
        return
    _check_min_max_step(tag, date, _parse_date,
                        datetime.date(1970, 1, 1), violations)


def _validate_radio_group(form, tag, violations):
    """required semantics for a radio button group: if any enabled member
    of the group is required, exactly one member must be checked."""
    name = tag.get("name")
    if name is None:
        group = [tag]
    else:
        group = [t for t in form.find_all("input", {"name": name})
                 if t.get("type", "text").lower() == "radio"]
    candidates = [t for t in group if not is_disabled(t)]
    required = [t for t in candidates if t.has_attr("required")]
    if required and not any(t.has_attr("checked") for t in candidates):
        violations.append(ConstraintViolation(required[0], "valueMissing", ""))


def _validate_select(tag, violations):
    if not tag.has_attr("required"):
        return
    options = tag.find_all("option")
    selected = [o for o in options if o.has_attr("selected")]
    if "multiple" in tag.attrs:
        if not selected:
            violations.append(ConstraintViolation(tag, "valueMissing", ""))
        return
    if not selected and options:
        # Browsers select the first option when none is selected.
        selected = [options[0]]
    if not selected or all(_option_value(o) == "" for o in selected):
        violations.append(ConstraintViolation(tag, "valueMissing", ""))


def _validate_input(form, tag, validated_radio_groups, violations):
    input_type = tag.get("type", "text").lower()
    if input_type in _BARRED_TYPES:
        return
    if input_type == "radio":
        name = tag.get("name")
        if name is None:
            _validate_radio_group(form, tag, violations)
        elif name not in validated_radio_groups:
            validated_radio_groups.add(name)
            _validate_radio_group(form, tag, violations)
        return
    if input_type == "checkbox":
        if tag.has_attr("required") and not tag.has_attr("checked"):
            violations.append(ConstraintViolation(tag, "valueMissing", ""))
        return
    value = tag.get("value", "")
    if input_type in _TEXT_TYPES:
        _check_text_constraints(tag, value, violations)
    elif input_type == "number":
        _validate_number(tag, value, violations)
    elif input_type == "date":
        _validate_date(tag, value, violations)
    # Other input types are not covered by this version of validation.


def validate_form(form, submit=None):
    """Validate a form's controls against their HTML constraints.

    :param form: A bs4.element.Tag corresponding to an HTML form element.
    :param submit: The submit element used for submission, if any. Its
        ``formnovalidate`` attribute takes precedence over the form's
        ``novalidate`` attribute: if either is present, validation is
        skipped and an empty list is returned.

    :return: A list of :class:`ConstraintViolation`, in document order.
        Empty if the form is valid. The DOM is never modified.
    """
    if submit is not None and submit.has_attr("formnovalidate"):
        return []
    if form.has_attr("novalidate"):
        return []

    violations = []
    validated_radio_groups = set()
    for tag in form.select("input, textarea, select"):
        # Disabled controls (including the disabled <fieldset> rule) and
        # readonly controls are barred from constraint validation.
        if is_disabled(tag) or tag.has_attr("readonly"):
            continue
        if tag.name == "input":
            _validate_input(form, tag, validated_radio_groups, violations)
        elif tag.name == "textarea":
            _check_text_constraints(tag, tag.text, violations)
        elif tag.name == "select":
            _validate_select(tag, violations)
    return violations
