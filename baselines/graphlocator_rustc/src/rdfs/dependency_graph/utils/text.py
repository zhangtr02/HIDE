def slice_text_around(
    text: str, start_line: int, start_column: int, end_line: int, end_column: int
) -> tuple[str, str, str]:
    """
    Slice the text around the specified portion of the text.
    :param text: The text to slice
    :param start_line: The line number of the start of the desired portion of the text (1-based index)
    :param start_column: The column number of the start of the desired portion of the text (1-based index)
    :param end_line: The line number of the end of the desired portion of the text (1-based index)
    :param end_column: The column number of the end of the desired portion of the text (1-based index)
    :returns The text before the desired portion, the desired portion, and the text after the desired portion
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        return "", "", ""

    start_line = max(1, start_line)
    end_line = max(start_line, end_line)
    start_column = max(1, start_column)
    end_column = max(1, end_column)

    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    if start_line > len(lines):
        return text, "", ""

    start_base = offsets[start_line - 1]
    start_index = min(start_base + start_column - 1, offsets[start_line])

    if end_line > len(lines):
        end_index = len(text)
    else:
        end_base = offsets[end_line - 1]
        end_index = min(end_base + end_column - 1, offsets[end_line])
    end_index = max(start_index, end_index)
    return text[:start_index], text[start_index:end_index], text[end_index:]


def slice_text(
    text: str, start_line: int, start_column: int, end_line: int, end_column: int
) -> str:
    """
    Slice the text inside the specified portion of the text.
    :param text: The text to slice
    :param start_line: The line number of the start of the desired portion of the text (1-based index)
    :param start_column: The column number of the start of the desired portion of the text (1-based index)
    :param end_line: The line number of the end of the desired portion of the text (1-based index)
    :param end_column: The column number of the end of the desired portion of the text (1-based index)
    """
    _before, sliced_text, _after = slice_text_around(
        text, start_line, start_column, end_line, end_column
    )
    return sliced_text
