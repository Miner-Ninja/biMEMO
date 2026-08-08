"""Tolerant line-based parsing of graph-extractor model output."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


_OBJECT_ENTITY_RE = re.compile(r'object_entity\s*=\s*"([^"]*)"')
_OBJECT_VALUE_RE = re.compile(r'value\s*=\s*"([^"]*)"')


@dataclass(frozen=True)
class ExtractedEntity:
    """One entity declared by the model."""

    entity_type: str
    name: str


@dataclass(frozen=True)
class ExtractedAssertion:
    """One assertion declared by the model."""

    subject_name: str
    relation: str
    object_entity_name: str | None
    object_value: str | None
    text: str
    scope: str
    trust: str


@dataclass
class GraphExtraction:
    """Parsed model output plus lines that could not be understood."""

    entities: list[ExtractedEntity] = field(default_factory=list)
    assertions: list[ExtractedAssertion] = field(default_factory=list)
    discarded_lines: list[str] = field(default_factory=list)


def _parse_entity_line(body: str) -> ExtractedEntity | None:
    """Parse the payload of one ENTITY line."""
    parts = [part.strip() for part in body.split("|")]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return ExtractedEntity(entity_type=parts[0], name=parts[1])


def _parse_assertion_line(body: str) -> ExtractedAssertion | None:
    """Parse the payload of one ASSERTION line."""
    parts = [part.strip() for part in body.split("|")]
    if len(parts) < 4:
        return None
    subject, relation, object_part = parts[0], parts[1], parts[2]
    text = parts[3]
    scope = parts[4] if len(parts) > 4 else "long_term"
    trust = parts[5] if len(parts) > 5 else "medium"
    if not subject or not relation or not text:
        return None
    entity_match = _OBJECT_ENTITY_RE.search(object_part)
    value_match = _OBJECT_VALUE_RE.search(object_part)
    object_entity_name = entity_match.group(1).strip() if entity_match else None
    object_value = value_match.group(1).strip() if value_match else None
    if object_entity_name is None and object_value is None:
        # Treat a bare object field as a literal value.
        object_value = object_part.strip() or None
    if object_entity_name is not None and object_value is not None:
        object_value = None
    if scope not in ("long_term", "short_term"):
        scope = "long_term"
    if trust not in ("high", "medium", "low"):
        trust = "medium"
    return ExtractedAssertion(
        subject_name=subject,
        relation=relation,
        object_entity_name=object_entity_name,
        object_value=object_value,
        text=text,
        scope=scope,
        trust=trust,
    )


def parse_graph_extraction(raw_output: str) -> GraphExtraction:
    """Parse model output into entities, assertions, and discarded lines."""
    extraction = GraphExtraction()
    for raw_line in raw_output.splitlines():
        line = raw_line.strip()
        if not line or line.upper() == "NONE":
            continue
        if line.upper().startswith("ENTITY:"):
            entity = _parse_entity_line(line[len("ENTITY:") :])
            if entity is not None:
                extraction.entities.append(entity)
            else:
                extraction.discarded_lines.append(line)
            continue
        if line.upper().startswith("ASSERTION:"):
            assertion = _parse_assertion_line(line[len("ASSERTION:") :])
            if assertion is not None:
                extraction.assertions.append(assertion)
            else:
                extraction.discarded_lines.append(line)
            continue
        extraction.discarded_lines.append(line)
    return extraction
