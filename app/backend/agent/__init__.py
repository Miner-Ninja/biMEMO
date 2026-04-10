"""Gateway orchestrator for the agentic loop.

Manages the LLM-tool execution cycle, streaming responses, stop handling,
and safety limits. Coordinates between LLM provider, tool broker, memory,
and channel adapters.
"""
