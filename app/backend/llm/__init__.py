"""LLM provider for local inference via Ollama.

Handles hardware detection, profile selection, model management, and
chat completion with streaming. Supports Qwen3 model family exclusively
with GPU-only default and config-based compute mode switching.
"""
