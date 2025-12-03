"""
form_tools.py
================

This module defines custom function tools for the Digital Paperwork Butler.  Each
function is designed to be wrapped by the Agent Development Kit (ADK) as a
tool.  The docstrings and type annotations are written so that Gemini and
ADK can infer parameter names and return values for tool invocation.

Functions:

* `parse_form` – Extracts form fields from a PDF and returns a mapping of field
  names to their current values.
* `autofill_form` – Fills blank fields in a PDF using supplied user data and
  writes a new PDF file.
* `validate_form` – Validates a dictionary of field values for emptiness and
  basic formats.
* `explain_field` – Uses Gemini (via the agent) to explain the meaning of a
  form field in plain language.  Although this function itself does not
  interface with Gemini, its signature allows the agent to call it as a tool
  and then interpret the return using the LLM.

Notes:
  - These tools rely on the `pypdf` library for PDF manipulation.
  - No sensitive data is stored in code.  User data is passed in from the
    agent state or from the caller.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from pypdf import PdfReader, PdfWriter

###############################################################################
# Helper functions
###############################################################################

def _load_user_data(user_data_path: str) -> Dict[str, str]:
    """Load user metadata from a JSON file.

    Parameters
    ----------
    user_data_path: str
        Path to a JSON file containing key–value pairs for autofill.

    Returns
    -------
    Dict[str, str]
        A dictionary of user data.
    """
    with open(user_data_path, "r", encoding="utf-8") as f:
        return json.load(f)


###############################################################################
# Tool functions
###############################################################################

def parse_form(pdf_path: str) -> Dict[str, Optional[str]]:
    """Extract form fields and their values from a PDF form.

    Given a path to a PDF file containing AcroForm fields, this function
    extracts all field names and their current values.  If a field is empty or
    unset, its value will be ``None``.

    Parameters
    ----------
    pdf_path: str
        Path to the PDF file to analyse.  The file must exist and contain
        form fields.

    Returns
    -------
    Dict[str, Optional[str]]
        A mapping of field names (strings) to their current values.  Missing or
        blank fields are represented as ``None``.

    Raises
    ------
    FileNotFoundError
        If the specified PDF does not exist.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    reader = PdfReader(pdf_path)
    # get_form_text_fields returns only text fields; get_fields returns all
    try:
        fields = reader.get_fields()
    except Exception:
        fields = {}
    result: Dict[str, Optional[str]] = {}
    for name, field in (fields or {}).items():
        value = field.get("/V")
        if value == "" or value is None:
            result[name] = None
        else:
            result[name] = str(value)
    return result


def autofill_form(
    pdf_path: str,
    user_data: Optional[Dict[str, str]] = None,
    output_dir: Optional[str] = None,
    flatten: bool = False,
) -> str:
    """Autofill blank fields in a PDF form using user data.

    This tool reads the PDF at ``pdf_path`` and fills any empty fields whose
    names match keys in ``user_data``.  If an output directory is provided, a
    new PDF is written there; otherwise, the filled form is written to a file
    next to the original PDF with suffix ``_filled``.  The function returns the
    path to the newly written PDF.

    Parameters
    ----------
    pdf_path: str
        Path to the original PDF file.
    user_data: Optional[Dict[str, str]], default None
        A dictionary of values used for autofill.  Keys should correspond to
        form field names (case‑insensitive match).  If omitted, the tool will
        attempt to load default values from ``metadata/user_data.json`` in the
        project root.  This makes it possible for the agent to autofill
        without explicit parameters.
    output_dir: Optional[str], default None
        Directory in which to write the new PDF.  If omitted, the output is
        written in the same directory as ``pdf_path``.
    flatten: bool, default False
        If True, the form fields are flattened (converted to static text) in the
        output PDF.  Flattening removes interactive fields and is useful when
        you no longer need the form to be editable.

    Returns
    -------
    str
        The path to the filled PDF file.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    writer.append(reader)

    # Build a mapping of field names to fill values (case‑insensitive)
    # If no user_data provided, attempt to load defaults.
    if user_data is None:
        # Determine the default metadata path relative to this file
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        default_path = os.path.join(repo_root, "metadata", "user_data.json")
        try:
            user_data = _load_user_data(default_path)
        except Exception:
            user_data = {}

    user_data_lower = {k.lower(): v for k, v in user_data.items()}
    fields = reader.get_fields() or {}
    fill_values: Dict[str, str] = {}
    for name, field in fields.items():
        value = field.get("/V")
        # If field is empty or None, attempt to autofill
        if value in ("", None):
            key_lower = name.lower()
            if key_lower in user_data_lower:
                fill_values[name] = user_data_lower[key_lower]

    # Fill the fields on the first page (works for most simple forms)
    if fill_values:
        writer.update_page_form_field_values(writer.pages[0], fill_values, auto_regenerate=False)

    if flatten:
        # Flatten the form by regenerating appearances and removing annotations
        writer.update_page_form_field_values(writer.pages[0], fill_values, auto_regenerate=False, flatten=True)
        # Remove widget annotations
        writer.remove_annotations(subtypes="/Widget")

    # Determine output path
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    out_dir = output_dir or os.path.dirname(pdf_path)
    os.makedirs(out_dir, exist_ok=True)
    output_path = os.path.join(out_dir, f"{base_name}_filled.pdf")
    with open(output_path, "wb") as f_out:
        writer.write(f_out)
    return output_path


def validate_form(fields: Dict[str, Optional[str]]) -> Dict[str, List[str]]:
    """Validate field values and return missing or invalid fields.

    This tool inspects a dictionary mapping field names to values and returns
    two lists: ``missing`` for fields that are required but empty, and
    ``invalid`` for fields that do not match simple patterns (e.g., date
    strings).

    Parameters
    ----------
    fields: Dict[str, Optional[str]]
        A mapping of form field names to their current values.  ``None`` or
        empty strings are considered missing.

    Returns
    -------
    Dict[str, List[str]]
        A dictionary with two keys: ``missing`` (list of field names with no
        values) and ``invalid`` (list of field names with values that failed
        validation).  Validation rules are simple: dates must match
        ``YYYY-MM-DD``; phone numbers must contain only digits, spaces, dashes,
        or plus signs; postal codes must be alphanumeric.
    """
    missing: List[str] = []
    invalid: List[str] = []

    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    phone_pattern = re.compile(r"^[\+\d\s\-]+$")
    postal_pattern = re.compile(r"^[\w\s-]+$")

    for name, value in fields.items():
        if value is None or str(value).strip() == "":
            missing.append(name)
            continue
        val = str(value).strip()
        # Example simple validation rules
        if "date" in name.lower():
            if not date_pattern.match(val):
                invalid.append(name)
        elif "phone" in name.lower():
            if not phone_pattern.match(val):
                invalid.append(name)
        elif "postal" in name.lower() or "zip" in name.lower():
            if not postal_pattern.match(val):
                invalid.append(name)
        # Add more rules as needed

    return {"missing": missing, "invalid": invalid}


def explain_field(field_name: str) -> str:
    """Explain the meaning of a form field in plain language.

    This is a simple wrapper function that returns a prompt asking Gemini to
    explain the given field.  It does not call Gemini directly; instead, the
    agent will receive this return value and use the language model to
    generate a helpful explanation for the user.  For example, if the field
    name is ``address_line_2``, the agent might reply:

        "Address Line 2 is used for additional location information such as
        apartment or suite numbers.  Leave it blank if you don't have one."

    Parameters
    ----------
    field_name: str
        The name of the form field that needs explanation.

    Returns
    -------
    str
        A human‑readable string describing the field and inviting the agent
        (Gemini) to provide further clarification.
    """
    return (
        f"Please explain the meaning of the form field '{field_name}' in plain "
        "language so that a non‑expert can understand what information belongs "
        "in this field."
    )


__all__ = [
    "parse_form",
    "autofill_form",
    "validate_form",
    "explain_field",
]