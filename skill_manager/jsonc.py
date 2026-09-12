from __future__ import annotations

import json

def strip_jsonc(text: str) -> str:
    """Remove JSONC comments and trailing commas without changing strings."""
    without_comments: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        character = text[index]
        if in_string:
            without_comments.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue

        if character == '"':
            in_string = True
            without_comments.append(character)
            index += 1
            continue
        if character == "/" and index + 1 < len(text) and text[index + 1] == "/":
            without_comments.extend((" ", " "))
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                without_comments.append(" ")
                index += 1
            continue
        if character == "/" and index + 1 < len(text) and text[index + 1] == "*":
            comment_start = index
            without_comments.extend((" ", " "))
            index += 2
            terminated = False
            while index < len(text):
                character = text[index]
                if character in "\r\n":
                    without_comments.append(character)
                else:
                    without_comments.append(" ")
                if character == "*" and index + 1 < len(text) and text[index + 1] == "/":
                    without_comments.append(" ")
                    index += 2
                    terminated = True
                    break
                index += 1
            if not terminated:
                raise json.JSONDecodeError("Unterminated block comment", text, comment_start)
            continue

        without_comments.append(character)
        index += 1

    stripped: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(without_comments):
        character = without_comments[index]
        if in_string:
            stripped.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue

        if character == '"':
            in_string = True
            stripped.append(character)
            index += 1
            continue
        if character == ",":
            lookahead = index + 1
            while lookahead < len(without_comments) and without_comments[lookahead].isspace():
                lookahead += 1
            if lookahead < len(without_comments) and without_comments[lookahead] in "]}":
                index += 1
                continue

        stripped.append(character)
        index += 1

    return "".join(stripped)


__all__ = ["strip_jsonc"]
