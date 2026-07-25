import os
import re
from dataclasses import dataclass

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
PARAGRAPH_RE = re.compile(r"\n\s*\n")

MAX_TOKENS = 800


@dataclass
class ProseSection:
    doc_type: str
    heading_path: list[str]
    embed_text: str
    chunk_text: str


def _doc_type_from_filename(filename: str) -> str:
    name = os.path.basename(filename).lower()
    if name.startswith("runbook"):
        return "runbook"
    if name.startswith("adr"):
        return "adr"
    return "readme"


def _approx_tokens(text: str) -> int:
    return int(len(text.split()) / 0.75)


def _split_sections(text: str) -> list[tuple[list[str], str]]:
    lines = text.splitlines()
    sections: list[tuple[list[str], str]] = []
    heading_stack: list[tuple[int, str]] = []
    current_lines: list[str] = []

    def flush():
        if current_lines and "\n".join(current_lines).strip():
            sections.append(([h for _, h in heading_stack], "\n".join(current_lines).strip()))

    for line in lines:
        match = HEADING_RE.match(line)
        if match:
            flush()
            current_lines = [line]
            level = len(match.group(1))
            title = match.group(2).strip()
            heading_stack = [h for h in heading_stack if h[0] < level]
            heading_stack.append((level, title))
        else:
            current_lines.append(line)
    flush()
    return sections


def _split_long_section(heading_path: list[str], body: str) -> list[tuple[list[str], str]]:
    """Each heading is its own chunk. Only split further if a single section
    alone exceeds the 800-token target, to keep any one chunk from ballooning."""
    if _approx_tokens(body) <= MAX_TOKENS:
        return [(heading_path, body)]

    parts: list[tuple[list[str], str]] = []
    buffer = ""
    for paragraph in PARAGRAPH_RE.split(body):
        candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
        if buffer and _approx_tokens(candidate) > MAX_TOKENS:
            parts.append((heading_path, buffer))
            buffer = paragraph
        else:
            buffer = candidate
    if buffer:
        parts.append((heading_path, buffer))
    return parts


def _first_body_line(body: str, heading_path: list[str]) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or HEADING_RE.match(stripped):
            continue
        return stripped
    return heading_path[-1] if heading_path else ""


def parse_prose_file(path: str) -> list[ProseSection]:
    with open(path) as f:
        text = f.read()

    doc_type = _doc_type_from_filename(path)
    sections = []
    for heading_path, body in _split_sections(text):
        sections.extend(_split_long_section(heading_path, body))

    result = []
    for heading_path, body in sections:
        breadcrumb = " > ".join(heading_path) if heading_path else os.path.basename(path)
        chunk_text = f"{breadcrumb}\n\n{body}"
        first_line = _first_body_line(body, heading_path)
        embed_text = f"{breadcrumb}: {first_line}"[:300]
        result.append(
            ProseSection(
                doc_type=doc_type,
                heading_path=heading_path,
                embed_text=embed_text,
                chunk_text=chunk_text,
            )
        )
    return result
