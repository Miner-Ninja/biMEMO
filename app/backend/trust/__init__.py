"""Trust evaluator for tool execution authorization.

Manages static trust zones (GREEN/YELLOW/RED), dynamic trust scoring
based on usage history, and confirmation flows for sensitive operations.
All outbound operations require explicit user confirmation.
"""
