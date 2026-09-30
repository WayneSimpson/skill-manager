"""Surgical JSONC member edits that preserve unrelated bytes and comments."""
from __future__ import annotations

import json
from typing import Any


class JsoncEditError(ValueError):
    """The requested member or section could not be located safely."""


def _skip(text: str, index: int) -> int:
    """Skip whitespace and both comment forms; never enters strings."""
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        if text.startswith("//", index):
            index = text.find("\n", index)
            if index < 0:
                return len(text)
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise JsoncEditError("Unterminated block comment")
            index = end + 2
            continue
        return index
    return index


def _string_end(text: str, index: int) -> int:
    """Return the index after the closing quote of the string starting at index."""
    if index >= len(text) or text[index] != '"':
        raise JsoncEditError("Expected a string")
    index += 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == '"':
            return index + 1
        index += 1
    raise JsoncEditError("Unterminated string")


def _value_end(text: str, index: int) -> int:
    """Return the index after the value starting at index (objects, arrays, scalars)."""
    start = _skip(text, index)
    if start >= len(text):
        raise JsoncEditError("Missing value")
    character = text[start]
    if character in "{[":
        depth = 0
        i = start
        while i < len(text):
            i = _skip(text, i)
            if i >= len(text):
                break
            if text[i] == '"':
                i = _string_end(text, i)
                continue
            if text[i] in "{[":
                depth += 1
            elif text[i] in "}]":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        raise JsoncEditError("Unterminated container")
    if character == '"':
        return _string_end(text, start)
    i = start
    while i < len(text):
        i = _skip(text, i)
        if i >= len(text) or text[i] in ",}]":
            return i
        i += 1
    return len(text)


def _root_span(text: str) -> tuple[int, int]:
    start = _skip(text, 0)
    if start >= len(text) or text[start] != "{":
        raise JsoncEditError("Configuration root is not an object")
    return start, _value_end(text, start)


def _iter_members(text: str, span: tuple[int, int]):
    """Yield (name, key_start, value_end) for each member of the object span."""
    i = _skip(text, span[0] + 1)
    while True:
        i = _skip(text, i)
        if i >= len(text) or text[i] == "}":
            return
        if text[i] == ",":
            i += 1
            continue
        if text[i] != '"':
            raise JsoncEditError("Unexpected token in configuration object")
        key_start = i
        key_end = _string_end(text, i)
        name = json.loads(text[key_start:key_end])
        colon = _skip(text, key_end)
        if colon >= len(text) or text[colon] != ":":
            raise JsoncEditError("Configuration member is missing a value")
        value_start = _skip(text, colon + 1)
        value_end = _value_end(text, value_start)
        yield name, key_start, value_end
        i = value_end


def _section_members(text: str, section: str):
    """Return (key_start, value_start, value_end) for a top-level section, or None."""
    root = _root_span(text)
    for name, key_start, value_end in _iter_members(text, root):
        if name == section:
            value_start = _skip(text, _find_colon(text, key_start) + 1)
            return key_start, value_start, value_end
    return None


def _find_colon(text: str, key_start: int) -> int:
    key_end = _string_end(text, key_start)
    colon = _skip(text, key_end)
    if colon >= len(text) or text[colon] != ":":
        raise JsoncEditError("Configuration member is missing a value")
    return colon


def _line_indent(text: str, index: int) -> str:
    line_start = text.rfind("\n", 0, index) + 1
    prefix = text[line_start:index]
    return prefix if prefix.strip() == "" else prefix[: len(prefix) - len(prefix.lstrip())]


def _serialize_member(name: str, definition: Any, indent: str) -> str:
    body = json.dumps(definition, indent=2, ensure_ascii=False)
    padded = body.replace("\n", "\n" + indent)
    return f'{json.dumps(name)}: {padded}'


def _existing_trailing_comma(text: str, value_end: int) -> bool:
    i = _skip(text, value_end)
    return i < len(text) and text[i] == ","


def patch_agent(section: str, name: str, definition: Any, *, rename_to: str | None = None):
    """Return a function replacing exactly one section member, preserving the rest."""
    def apply(text: str) -> str:
        found = _section_members(text, section)
        if found is None:
            raise JsoncEditError(f"Section {section} not found")
        _key, value_start, _end = found
        for member, key_start, value_end in _iter_members(text, (value_start, _end)):
            if member != name:
                continue
            indent = _line_indent(text, key_start)
            member_text = _serialize_member(rename_to or name, definition, indent + "  ")
            return text[:key_start] + member_text + text[value_end:]
        raise JsoncEditError(f"Agent {name} not found in {section}")
    return apply


def _insert_member_after(text: str, insert_at: int, member_text: str, indent: str) -> str:
    """Insert a member after the given index, respecting any existing trailing comma."""
    prefix = "" if _existing_trailing_comma(text, insert_at) else ","
    return text[:insert_at] + prefix + "\n" + indent + member_text + text[insert_at:]


def insert_agent(section: str, name: str, definition: Any):
    """Return a function inserting one member into a section, creating it if absent."""
    def apply(text: str) -> str:
        found = _section_members(text, section)
        if found is None:
            return insert_section(section, name, definition)(text)
        _key, value_start, _end = found
        members = list(_iter_members(text, (value_start, _end)))
        if members:
            _last, _member_key, last_end = members[-1]
            last_indent = _line_indent(text, _member_key)
            member = _serialize_member(name, definition, last_indent)
            return _insert_member_after(text, last_end, member, last_indent)
        # Empty section object: insert directly after its opening brace.
        inner = _skip(text, value_start + 1)
        section_indent = _line_indent(text, found[0])
        indent = section_indent + "  "
        member = _serialize_member(name, definition, indent)
        return text[:inner] + "\n" + indent + member + "\n" + section_indent + text[inner:]
    return apply


def insert_section(section: str, name: str, definition: Any):
    """Return a function inserting a member into a newly created top-level section."""
    def apply(text: str) -> str:
        root = _root_span(text)
        members = list(_iter_members(text, root))
        section_value = {name: definition}
        if not members:
            inner = _skip(text, root[0] + 1)
            if inner >= len(text) or text[inner] != "}":
                raise JsoncEditError("Configuration root is not an object")
            section_text = f'{json.dumps(section)}: ' + \
                json.dumps(section_value, indent=2, ensure_ascii=False)
            return text[:root[0] + 1] + "\n  " + section_text + "\n" + text[inner:]
        _last, key_start, last_end = members[-1]
        indent = _line_indent(text, key_start)
        section_text = f'{json.dumps(section)}: ' + \
            json.dumps(section_value, indent=2, ensure_ascii=False).replace("\n", "\n" + indent)
        return _insert_member_after(text, last_end, section_text, indent)
    return apply
