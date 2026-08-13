"""Prompt and limits for the graph extractor LLM call."""

from __future__ import annotations


GRAPH_EXTRACTOR_MAX_TOKENS = 300
GRAPH_EXTRACTOR_TEMPERATURE = 0.0

# Closed starter vocabulary — anything else is normalized to relates_to.
RELATION_VOCABULARY = (
    "prefers",
    "uses",
    "works_on",
    "located_at",
    "states",
    "relates_to",
)

# Names that would flood the graph with meaningless nodes. The user node
# is the single allowed generic subject ("person | user").
FORBIDDEN_ENTITY_NAMES = (
    "system",
    "assistant",
    "conversation",
    "dialog",
    "message",
    "система",
    "ассистент",
    "разговор",
    "диалог",
    "сообщение",
)

GRAPH_EXTRACTOR_SYSTEM_PROMPT = (
    "You are a knowledge extraction agent. From the conversation records, "
    "extract durable entities and assertions.\n"
    "Output ONLY lines in these two formats:\n"
    "ENTITY: <type> | <name>\n"
    'ASSERTION: <subject name> | <relation> | object_entity="<name>" OR '
    'value="<literal>" | <one-sentence statement> | <long_term or '
    "short_term> | <high, medium or low>\n"
    "Rules:\n"
    "- At most 3 ENTITY lines per batch; every ASSERTION subject must "
    "have its own ENTITY line (exception: the user, declare as "
    "'ENTITY: person | user').\n"
    "- relation must be one of: prefers, uses, works_on, located_at, "
    "states, relates_to.\n"
    "- Never create entities named: system, assistant, conversation, "
    "dialog, message (any language).\n"
    "- Extract only durable facts worth remembering; if nothing "
    "qualifies, output the single line NONE.\n"
    "- Answer in the language of the records for names and statements."
)
