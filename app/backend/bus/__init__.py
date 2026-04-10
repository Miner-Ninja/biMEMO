"""Message bus for intra-process and inter-process communication.

Provides an abstract publish/subscribe interface with two implementations:
asyncio.Queue for Core intra-process messaging and Unix domain sockets
with JSON serialization for isolated Tool Worker subprocess IPC.
"""
