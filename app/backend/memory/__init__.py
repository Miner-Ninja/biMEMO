"""Memory system for context compression.

Implements three-tier context management: recent messages, structured
records, and higher-level summaries. Includes vector and full-text search
within the encrypted SQLCipher database.
"""
