"""Scenario templates for the memory loop evaluation harness."""

from __future__ import annotations


CONTROL_SCENARIO_TEMPLATES = [
    "Acknowledge in one short sentence: this memory test is {scenario_tag}.",
    "Acknowledge in one short sentence: I currently prefer concise technical answers.",
    "Acknowledge in one short sentence: long debugging explanations frustrate me.",
    (
        "Acknowledge in one short sentence: "
        "I am validating the memory subsystems in biMEMO."
    ),
    "Acknowledge in one short sentence: my current answer style is concise and direct.",
    "Reply with the identifier only for this memory test.",
    "Reply in one short sentence: what do you know about my answer-style preference?",
    "Acknowledge in one short sentence: I now want detailed step-by-step answers.",
    (
        "Acknowledge in one short sentence: "
        "this change matters for all future technical answers."
    ),
    (
        "Acknowledge in one short sentence: "
        "detailed explanations are now better than concise ones."
    ),
    (
        "Acknowledge in one short sentence: "
        "when I ask about debugging, include context and reasoning."
    ),
    "Reply in one short sentence: what answer style do I prefer now?",
    "Reply in one short sentence: briefly explain what changed in my preferences.",
    (
        "Acknowledge in one short sentence: "
        "detailed answers are now the active preference."
    ),
    "Acknowledge in one short sentence: future debugging answers must include context.",
    (
        "Acknowledge in one short sentence: "
        "future debugging answers must include reasoning."
    ),
    "Acknowledge in one short sentence: future debugging answers must stay detailed.",
    (
        "Acknowledge in one short sentence: "
        "concise answers are no longer the active preference."
    ),
    (
        "Acknowledge in one short sentence: "
        "the new preference applies to future technical help."
    ),
    (
        "Acknowledge in one short sentence: "
        "the new preference applies to future debugging help."
    ),
    (
        "Acknowledge in one short sentence: "
        "the active preference is still detailed answers."
    ),
    (
        "Acknowledge in one short sentence: "
        "the active preference is still step-by-step answers."
    ),
    (
        "Reply in one short sentence: "
        "what should you remember about my current answer style?"
    ),
    "Reply in one short sentence: what is the final active preference now?",
]

REALISTIC_SCENARIO_TEMPLATES = [
    "For this test run, remember the identifier {scenario_tag}.",
    "This is a temporary validation run for the biMEMO memory system.",
    "I am checking memory behavior in the current session only.",
    "Right now I usually like concise technical answers.",
    "Long debugging explanations tend to annoy me.",
    "At the moment my preferred style is brief and direct.",
    "Short replies feel easier to scan during debugging.",
    "I do not want long explanations unless I ask for them.",
    "Please keep early technical answers concise for now.",
    "I am focused on quick validation feedback in this run.",
    "What test identifier am I using in this run?",
    "What do you currently remember about my preferred answer style?",
    "How would you currently describe my debugging answer preference?",
    "What should you remember about my current style in this session?",
    "Repeat my current answer-style preference in one sentence.",
    "If I ask for debugging help right now, how brief should you be?",
    "What is the temporary task context of this session?",
    "What do you remember about my tolerance for long explanations?",
    "Summarize my current preference in a short sentence.",
    "What do you currently know about the style I want?",
    "I changed my mind: I want detailed step-by-step answers now.",
    "This update should matter for future technical answers, not just this reply.",
    "Detailed explanations are now better for me than concise ones.",
    "For debugging questions, include context and reasoning from now on.",
    "I still want you to stay clear, but now I prefer depth over brevity.",
    "When the topic is technical, explain the steps instead of skipping them.",
    "I want the reasoning behind the answer, not only the conclusion.",
    "Please treat this new style as the active preference going forward.",
    "The earlier concise style is no longer the best fit for me.",
    "Future debugging answers must include context.",
    "Future debugging answers must include reasoning.",
    "Future debugging answers must stay detailed.",
    "The new style applies to future technical help.",
    "The new style applies to future debugging help.",
    "Concise answers are no longer the active preference.",
    "My default technical-answer preference is now detailed and step-by-step.",
    "For future technical questions, give the steps in order.",
    "For future debugging questions, explain the cause before the fix.",
    "For future debugging questions, explain the fix after the cause.",
    "For future debugging questions, include enough detail to reproduce the logic.",
    "What answer style do I prefer now?",
    "Explain briefly what changed in my preferences.",
    "What should you remember now instead of the earlier concise style?",
    "How would you describe the old style versus the new style?",
    "What is the active preference now?",
    "What preference is outdated now?",
    "Which style should guide future technical replies?",
    "Which style should guide future debugging replies?",
    "What did I replace the concise style with?",
    "Summarize the preference shift in one sentence.",
    "Do not answer with only the final result when the topic is technical.",
    "Do include the intermediate steps when the topic is technical.",
    "Do include relevant context when the topic is debugging.",
    "Do include the reasoning path when the topic is debugging.",
    "The preferred style should stay detailed even if the answer is longer.",
    "The preferred style should stay structured even if the answer is longer.",
    "A short answer is fine only if it still keeps the important reasoning.",
    "Step-by-step structure matters more to me now than pure brevity.",
    "Context matters more to me now than speed alone.",
    "Reasoning matters more to me now than shortness alone.",
    "If there is a tradeoff, choose clarity and detail over brevity.",
    "If there is a tradeoff, choose a structured explanation over a terse reply.",
    "In debugging, I want enough explanation to understand the failure.",
    "In debugging, I want enough explanation to understand the fix.",
    "In debugging, I want enough explanation to understand why the fix works.",
    "Please keep remembering that the new preference is meant for future answers.",
    "Please keep remembering that the new preference is not limited to this message.",
    "Please keep remembering that the new preference replaces the old concise style.",
    "What is my current style for future technical help?",
    "What is my current style for future debugging help?",
    "What should be your default technical-answer behavior now?",
    "What should be your default debugging-answer behavior now?",
    "What should you avoid from the old style now?",
    "What should you preserve from the new style now?",
    (
        "This validation run itself is temporary and should not become a "
        "permanent preference."
    ),
    "The memory test identifier is temporary context, not a durable preference.",
    "The fact that I am running a validation right now is session-specific.",
    "This current test run should not outweigh my long-term answer-style preference.",
    (
        "The project memory check is part of the current task, not permanent "
        "identity data."
    ),
    (
        "The temporary test context should stay separate from my durable "
        "answer-style preference."
    ),
    "What part of this conversation is temporary task context?",
    "What part of this conversation should remain durable preference memory?",
    (
        "Separate the temporary test-run details from the long-term "
        "answer-style preference."
    ),
    "Which parts of this session should stay short-term only?",
    "Which parts should remain long-term for future conversations?",
    "If I return tomorrow, what answer style should you still remember?",
    "If this test run ends, what temporary detail can safely fade later?",
    "Which preference should still matter after the validation run is over?",
    "Which detail belongs only to the active session and not to future chats?",
    "What should you remember about my current answer style in future replies?",
    "What is the final active preference now?",
    "What is the durable preference now, after the temporary test context is ignored?",
    (
        "What is the temporary session detail now, without confusing it with "
        "my long-term style?"
    ),
    "Summarize my long-term preference in one sentence.",
    "Summarize the short-term validation context in one sentence.",
    "What would be the wrong old preference to remember now?",
    "What is the right new preference to remember now?",
    "What should be retrieved in a future technical answer from this session?",
    "What should not dominate future retrieval once this test run is over?",
    "State the final active long-term preference and the temporary short-term context.",
]

SCENARIO_TEMPLATE_PROFILES = {
    "control": CONTROL_SCENARIO_TEMPLATES,
    "realistic": REALISTIC_SCENARIO_TEMPLATES,
}
