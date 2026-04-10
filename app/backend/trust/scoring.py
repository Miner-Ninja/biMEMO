"""Dynamic trust scoring with parameter hashing.

Tracks tool usage history and computes trust scores
per tool+parameter combination using SHA-256 hashing.
"""

import hashlib
import logging
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)

# Null byte separator prevents field boundary ambiguity
_SEPARATOR = b"\x00"

# Score threshold for suggesting zone downgrade
DOWNGRADE_THRESHOLD = 10


@dataclass
class TrustScore:
    """Trust score for a tool+parameter combination.

    Attributes:
        count: Number of successful executions.
    """

    count: int = 0


@dataclass
class TrustScorer:
    """Tracks dynamic trust scores for tools.

    Maintains per-hash execution counts without storing
    raw parameter values.

    Attributes:
        scores: Map of parameter hash to trust score.
    """

    scores: dict[str, TrustScore] = field(default_factory=dict)

    @staticmethod
    def _hash_input(
        tool_name: str,
        param_name: str,
        param_value: str,
    ) -> bytes:
        """Build the raw byte input used for trust-score hashing."""
        return (
            tool_name.encode()
            + _SEPARATOR
            + param_name.encode()
            + _SEPARATOR
            + param_value.encode()
        )

    @staticmethod
    def compute_hash(
        tool_name: str,
        param_name: str,
        param_value: str,
    ) -> str:
        """Compute SHA-256 hash for a tool+parameter.

        Args:
            tool_name: Name of the tool.
            param_name: Name of the key parameter.
            param_value: Value of the parameter.

        Returns:
            Hex-encoded SHA-256 hash string.
        """
        data = TrustScorer._hash_input(tool_name, param_name, param_value)
        return hashlib.sha256(data).hexdigest()

    def _score_key(
        self,
        tool_name: str,
        param_name: str,
        param_value: str,
    ) -> str:
        """Return the score-map key for one tool parameter value."""
        return self.compute_hash(tool_name, param_name, param_value)

    def _score_entry(
        self,
        tool_name: str,
        param_name: str,
        param_value: str,
    ) -> TrustScore | None:
        """Return the stored score entry, if any."""
        score_key = self._score_key(tool_name, param_name, param_value)
        return self.scores.get(score_key)

    def _score_entry_for_update(
        self,
        tool_name: str,
        param_name: str,
        param_value: str,
    ) -> TrustScore:
        """Return a mutable score entry, creating it if missing."""
        score_key = self._score_key(tool_name, param_name, param_value)
        return self.scores.setdefault(score_key, TrustScore())

    def record_success(
        self,
        tool_name: str,
        param_name: str,
        param_value: str,
    ) -> int:
        """Record a successful tool execution.

        Args:
            tool_name: Name of the tool.
            param_name: Key parameter name.
            param_value: Parameter value.

        Returns:
            Updated execution count.
        """
        score = self._score_entry_for_update(tool_name, param_name, param_value)
        score.count += 1
        return score.count

    def get_score(
        self,
        tool_name: str,
        param_name: str,
        param_value: str,
    ) -> int:
        """Get the trust score for a tool+parameter.

        Args:
            tool_name: Name of the tool.
            param_name: Key parameter name.
            param_value: Parameter value.

        Returns:
            Execution count (0 if never seen).
        """
        score = self._score_entry(tool_name, param_name, param_value)
        return score.count if score else 0

    def suggests_downgrade(
        self,
        tool_name: str,
        param_name: str,
        param_value: str,
    ) -> bool:
        """Check if trust score suggests a zone downgrade.

        A downgrade is only a suggestion; it requires
        explicit user approval.

        Args:
            tool_name: Name of the tool.
            param_name: Key parameter name.
            param_value: Parameter value.

        Returns:
            True if score exceeds downgrade threshold.
        """
        return self.get_score(tool_name, param_name, param_value) >= DOWNGRADE_THRESHOLD
