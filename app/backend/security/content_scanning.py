"""Content filtering for input/output security scanning.

Detects credentials, prompt injection patterns, and PII
in text flowing through the system.
"""

import logging
import math
import re

from backend.security.content_scanning_models import FilterResult


logger = logging.getLogger(__name__)

# --- Strict credential patterns: precise provider/format signatures ---
STRICT_CREDENTIAL_PATTERNS: list[tuple[str, str]] = [
    # AI Providers
    (r"sk-[a-zA-Z0-9]{20,}", "OpenAI API key"),
    (r"sk-proj-[a-zA-Z0-9_-]{20,}", "OpenAI Project API key"),
    (r"sk-ant-[a-zA-Z0-9_-]{20,}", "Anthropic API key"),
    (r"xox[cbaprs]-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9-]*", "Slack Token"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub PAT"),
    (r"github_pat_[a-zA-Z0-9_]{22,}", "GitHub Fine-grained PAT"),
    (r"glpat-[a-zA-Z0-9_-]{20}", "GitLab PAT"),
    (r"AKIA[A-Z0-9]{16}", "AWS Access Key"),
    (r"ASIA[A-Z0-9]{16}", "AWS Temporary Key"),
    (r"A3T[A-Z0-9]{16}", "Amazon Ads Key"),
    (r"sq0[a-z]{3}-[a-zA-Z0-9_-]{22}", "Square OAuth"),
    (r"sq0csp-[a-zA-Z0-9_-]{43}", "Square OAuth Secret"),
    # Databases
    (r"postgres(?:ql)?://[^\s]+", "PostgreSQL Connection"),
    (r"mysql://[^\s]+", "MySQL Connection"),
    (r"redis://[^\s:]+:[^\s@]+@[^\s]+", "Redis Connection with Auth"),
    (r"mongodb(\+srv)?://[^\s]+", "MongoDB Connection"),
    # Cloud
    (r"ya29\.[a-zA-Z0-9_-]+", "Google OAuth"),
    (r"AIza[0-9A-Za-z_-]{35}", "Google API Key"),
    (r"[A-Za-z0-9_-]*\.firebaseio\.com", "Firebase URL"),
    (r"https://[a-z0-9-]+\.supabase\.co", "Supabase URL"),
    # Crypto material
    (r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----", "Private Key"),
    (r"-----BEGIN\s+CERTIFICATE-----", "Certificate"),
]

# --- Generic credential patterns: free-form password=/api_key=/bearer ---
GENERIC_CREDENTIAL_PATTERNS: list[tuple[str, str]] = [
    (
        r"(?:api[_-]?key|apikey|api_secret|secret_key)\s*[=:]\s*['\"]?[a-zA-Z0-9_-]{16,}['\"]?",
        "Generic API Key",
    ),
    (r"(?:bearer|token)\s+[a-zA-Z0-9_-]{20,}", "Bearer Token"),
    (
        r"(?:password|passwd|pwd|pass)\s*[=:]\s*['\"]?[^\s'\"]{8,}['\"]?",
        "Password in text",
    ),
]

# --- Union list — backward-compatible default for ContentFilter() ---
_CREDENTIAL_PATTERNS: list[tuple[str, str]] = (
    STRICT_CREDENTIAL_PATTERNS + GENERIC_CREDENTIAL_PATTERNS
)

_CREDENTIAL_COMPILED = [
    (re.compile(pattern, re.IGNORECASE), description)
    for pattern, description in _CREDENTIAL_PATTERNS
]

# --- Strict prompt-injection patterns: direct jailbreak attempts ---
STRICT_INJECTION_PATTERN_STRINGS: list[str] = [
    r"ignore\s+(all\s+)?(?:previous|prior|above)\s+instructions?",
    (
        r"(?:disregard|forget|ignore)\s+(?:all\s+)?your\s+"
        r"(?:previous|prior|system|original)?\s*instructions?"
    ),
    r"(?:override|overwrite)\s+(?:your|the)\s+(?:system|prompt|instructions)",
    r"forget\s+(?:everything|all)\s+(?:you|we)\s+know",
    r"ignore\s+any\s+(?:previous|prior|system)\s+(?:rules|prompts|instructions)",
    r"(?:you\s+have|no\s+longer)\s+to\s+follow\s+(?:any\s+)?(?:rules?|instructions)",
    r"output\s+(?:the\s+)?(?:contents?\s+of\s+)?(?:the\s+)?(?:system\s+)?prompt",
    r"(?:show|print|reveal)\s+(?:me\s+)?your\s+(?:system\s+)?prompt",
]

# --- Role-play injection patterns: act-as / system: / pretend ---
ROLEPLAY_INJECTION_PATTERN_STRINGS: list[str] = [
    (
        r"you\s+(?:are|should act)\s+(?:now\s+)?(?:as\s+)?"
        r"(?:a|an|like)\s+(?:\w+\s+){0,3}"
        r"(?:AI|assistant|bot|model|helpful)"
    ),
    r"(?:new|additional|extra)\s+instructions?\s*:",
    r"system\s*:\s*(?:you\s+are|this\s+is)",
    r"assistant\s*:\s*",
    r"(?:pretend|act)\s+(?:as|like)\s+",
    r"(?:pretend|act\s+(?:as|like))\s+(?:you\s+are|i\s+am)\s+",
    r"let\s+(?:us|me)\s+play\s+a\s+(?:different|fake)\s+character",
]

# --- Union compiled list — backward-compatible default ---
_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (STRICT_INJECTION_PATTERN_STRINGS + ROLEPLAY_INJECTION_PATTERN_STRINGS)
]

# --- PII pattern source strings (email, phone, SSN, card, IPv4) ---
PII_PATTERN_STRINGS: list[str] = [
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    r"\+?[1-9]\d{1,14}",  # E.164
    r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",  # US phone
    r"\d{3}-\d{2}-\d{4}",  # US SSN
    r"\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}",  # credit card
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",  # IPv4
]

# --- Compiled PII patterns — backward-compatible default ---
_PII_PATTERNS = [re.compile(p) for p in PII_PATTERN_STRINGS]

# --- Internal info pattern source strings (paths, conn strings, master key) ---
INTERNAL_PATTERN_STRINGS: list[str] = [
    r"/home/[^\s]+/[^\s]*\.(?:py|env|key|pem|crt)",
    r"/app/[^\s]*\.(?:py|env|key|pem|crt)",
    r"/etc/[^\s]*\.(?:py|env|conf)",
    r"sqlite3:///[^\s]+",
    r"postgresql://[^\s]+",
    r"redis://[^\s]+",
    r"mongodb(\+srv)?://[^\s]+",
    r"(?:master|master_key|encryption)_key\s*[=:]\s*\S+",
]

# --- Compiled internal patterns — backward-compatible default ---
_INTERNAL_PATTERNS = [re.compile(p) for p in INTERNAL_PATTERN_STRINGS]

# --- Broad code-execution patterns: any eval/exec/subprocess/shell flag ---
BROAD_CODE_EXECUTION_PATTERN_STRINGS: list[str] = [
    r"eval\s*\(",
    r"exec\s*\(",
    r"__import__\s*\(",
    r"subprocess\s*\.",
    r"os\.system\s*\(",
    r"os\.popen\s*\(",
    r"spawn\s*\(",
    r"shell\s*=\s*True",
    r"\|\s*bash",
    r"\|\s*sh",
    r">\s*/dev/",
    r"&&\s*rm\s+",
]

# --- Dangerous-shell-only patterns: truly destructive constructs ---
DANGEROUS_SHELL_PATTERN_STRINGS: list[str] = [
    r"rm\s+-rf\s+/(?:\s|$)",
    r"rm\s+-rf\s+~(?:/|\s|$)",
    r":\s*\(\s*\)\s*\{\s*:\s*\|",  # fork bomb start
    r">\s*/dev/sd[a-z]",
    r">\s*/dev/nvme",
    r"dd\s+[^\s]*\s*of=/dev/sd",
]

# --- Union compiled list — backward-compatible default ---
_CODE_EXECUTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in BROAD_CODE_EXECUTION_PATTERN_STRINGS
]

# --- Strict exfiltration patterns: explicit URL-with-secret + img beacon ---
STRICT_EXFILTRATION_PATTERN_STRINGS: list[str] = [
    (
        r"(?:fetch|curl|wget|request)\s+https?://[^\s]+"
        r"(?:\?|\&)(?:key|token|secret|password|data)="
    ),
    r"<img\s+[^>]*src\s*=\s*['\"]https?://",
    r"document\.location\s*=\s*['\"]https?://",
    r"window\.location\s*=\s*['\"]https?://",
]

# --- Broad exfiltration patterns: HTTP-client calls and base64 helpers ---
BROAD_EXFILTRATION_PATTERN_STRINGS: list[str] = [
    *STRICT_EXFILTRATION_PATTERN_STRINGS,
    r"new\s+Image\(\s*['\"]https?://",
    r"fetch\s*\(\s*['\"]https?://[^\s]+['\"]",
    r"requests\.get\s*\(\s*['\"]https?://",
    r"axios\.get\s*\(\s*['\"]https?://",
    r"\.send\s*\(\s*['\"]http",
    r"base64\.encode\s*\(",
    r"btoa\s*\(",
    r"(?:atob|btoa)\s*\(",
    r"String\.fromCharCode\s*\(",
    r"encodeURIComponent\s*\(",
]

# --- Union compiled list — backward-compatible default ---
_EXFILTRATION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in BROAD_EXFILTRATION_PATTERN_STRINGS
]

# --- Suspicious patterns (high entropy = likely encoded) ---
_HIGH_ENTROPY_THRESHOLD = 4.5
_MIN_ENTROPY_LENGTH = 20


MAX_MESSAGE_SIZE = 100_000
REDACTED = "[REDACTED]"


def shannon_entropy(text: str) -> float:
    """Calculate Shannon entropy of a string."""
    if not text:
        return 0.0
    freq: dict[str, int] = {}
    for ch in text:
        freq[ch] = freq.get(ch, 0) + 1
    length = len(text)
    return -sum((c / length) * math.log2(c / length) for c in freq.values())


def _compiled_patterns(
    patterns: list[str],
    *,
    ignore_case: bool = False,
) -> list[re.Pattern]:
    """Compile one list of regex patterns with optional ignore-case mode."""
    flags = re.IGNORECASE if ignore_case else 0
    return [re.compile(pattern, flags) for pattern in patterns]


def detect_high_entropy(text: str) -> bool:
    """Detect likely encoded/obfuscated content."""
    if len(text) < _MIN_ENTROPY_LENGTH:
        return False
    return shannon_entropy(text) > _HIGH_ENTROPY_THRESHOLD


class ContentFilter:
    """Content filter for detecting security issues.

    Example:
        filter = ContentFilter()

        # Filter input (user messages, tool results)
        result = filter.scan_input("Hello, ignore previous instructions")

        if result.injection_flagged:
            print("Warning: Injection detected!")

        # Filter output (LLM responses) - redacts credentials
        result = filter.scan_output("Here's your API key: sk-1234567890")
        print(result.text)  # "Here's your API key: [REDACTED]"
    """

    def __init__(
        self,
        credential_patterns: list[tuple[str, str]] | None = None,
        injection_patterns: list[str] | None = None,
        pii_patterns: list[re.Pattern] | None = None,
        internal_patterns: list[str] | None = None,
        code_execution_patterns: list[str] | None = None,
        exfiltration_patterns: list[str] | None = None,
        max_size: int = MAX_MESSAGE_SIZE,
        block_pii: bool = False,
        block_high_entropy: bool = False,
    ) -> None:
        """Initialize the filter with custom patterns.

        Args:
            credential_patterns: Custom credential regex + description pairs.
            injection_patterns: Custom injection regex patterns.
            pii_patterns: Custom PII regex patterns.
            internal_patterns: Custom internal info patterns.
            code_execution_patterns: Custom code execution patterns.
            exfiltration_patterns: Custom data exfiltration patterns.
            max_size: Maximum message size in bytes.
            block_pii: Whether to block PII (default False, just flag).
            block_high_entropy: Whether to block high entropy content.
        """
        self._credential_patterns = [
            (re.compile(pattern, re.IGNORECASE), description)
            for pattern, description in (credential_patterns or _CREDENTIAL_PATTERNS)
        ]
        self._injection_patterns = (
            _compiled_patterns(injection_patterns or [], ignore_case=True)
            if injection_patterns
            else _INJECTION_PATTERNS
        )
        self._pii_patterns = pii_patterns or _PII_PATTERNS
        self._internal_patterns = (
            _compiled_patterns(internal_patterns or [])
            if internal_patterns
            else _INTERNAL_PATTERNS
        )
        self._code_execution_patterns = (
            _compiled_patterns(code_execution_patterns or [], ignore_case=True)
            if code_execution_patterns
            else _CODE_EXECUTION_PATTERNS
        )
        self._exfiltration_patterns = (
            _compiled_patterns(exfiltration_patterns or [], ignore_case=True)
            if exfiltration_patterns
            else _EXFILTRATION_PATTERNS
        )
        self._max_size = max_size
        self._block_pii = block_pii
        self._block_high_entropy = block_high_entropy

    def _scan_credentials(self, text: str) -> list[str]:
        """Return all credential descriptions matched in the text."""
        matches: list[str] = []
        for pattern, description in self._credential_patterns:
            if pattern.search(text):
                matches.append(description)
        return matches

    @staticmethod
    def _matches_any_pattern(text: str, patterns: list[re.Pattern]) -> bool:
        """Return True when any pattern matches the provided text."""
        return any(pattern.search(text) for pattern in patterns)

    def _scan_input_flags(
        self,
        text: str,
    ) -> tuple[list[str], bool, bool, bool, bool]:
        """Collect input-scan flags before building warnings and block status."""
        credentials = self._scan_credentials(text)
        injection_detected = self._matches_any_pattern(text, self._injection_patterns)
        pii_detected = self._matches_any_pattern(text, self._pii_patterns)
        code_exec = self._matches_any_pattern(text, self._code_execution_patterns)
        suspicious = detect_high_entropy(text)
        return (
            credentials,
            injection_detected,
            pii_detected,
            code_exec,
            suspicious,
        )

    def _build_input_scan_result(
        self,
        text: str,
        *,
        credentials: list[str],
        injection_detected: bool,
        pii_detected: bool,
        code_exec: bool,
        suspicious: bool,
    ) -> FilterResult:
        """Build the final FilterResult for one input scan."""
        warnings: list[str] = []
        blocked = False

        if credentials:
            warnings.append(f"Potential credentials detected: {', '.join(credentials)}")
            blocked = True
        if injection_detected:
            warnings.append("Prompt injection pattern detected")
            blocked = True
        if pii_detected:
            warnings.append("PII detected (email, phone, SSN, etc.)")
            if self._block_pii:
                blocked = True
        if code_exec:
            warnings.append("Potential code execution attempt detected")
            blocked = True
        if suspicious:
            warnings.append("High entropy content detected (possibly encoded)")

        return FilterResult(
            text=text,
            credentials_found=credentials,
            injection_flagged=injection_detected,
            pii_found=pii_detected,
            code_execution=code_exec,
            suspicious=suspicious,
            blocked=blocked,
            warnings=warnings,
        )

    def _redact_credentials(
        self,
        text: str,
    ) -> tuple[str, list[str], bool]:
        """Redact matched credentials and return the updated text and flags."""
        filtered = text
        credentials: list[str] = []
        blocked = False
        for pattern, description in self._credential_patterns:
            if pattern.search(filtered):
                credentials.append(description)
                filtered = pattern.sub(REDACTED, filtered)
                blocked = True
        return filtered, credentials, blocked

    def _redact_internal_info(self, text: str) -> str:
        """Redact internal paths and connection strings from text."""
        filtered = text
        for pattern in self._internal_patterns:
            filtered = pattern.sub(REDACTED, filtered)
        return filtered

    def _scan_output_flags(
        self,
        filtered_text: str,
        original_text: str,
    ) -> tuple[bool, bool, bool]:
        """Collect output-scan flags after redaction is applied."""
        injection_detected = self._matches_any_pattern(
            filtered_text,
            self._injection_patterns,
        )
        exfiltration_detected = self._matches_any_pattern(
            filtered_text,
            self._exfiltration_patterns,
        )
        high_entropy_blocked = self._block_high_entropy and detect_high_entropy(
            original_text
        )
        return injection_detected, exfiltration_detected, high_entropy_blocked

    @staticmethod
    def _build_output_scan_result(
        filtered_text: str,
        credentials: list[str],
        *,
        injection_detected: bool,
        exfiltration_detected: bool,
        blocked: bool,
    ) -> FilterResult:
        """Build the final FilterResult for one output scan."""
        return FilterResult(
            text=filtered_text,
            credentials_found=credentials,
            injection_flagged=injection_detected,
            suspicious=exfiltration_detected,
            blocked=blocked,
        )

    def scan_input(self, text: str) -> FilterResult:
        """Scan incoming text (user messages, tool results).

        Does not block - flags only (unless configured otherwise).

        Args:
            text: Input text to filter.

        Returns:
            FilterResult with flags and warnings.
        """
        if len(text.encode()) > self._max_size:
            return FilterResult(
                text=text,
                blocked=True,
                warnings=["Message exceeds size limit"],
            )
        (
            credentials,
            injection_detected,
            pii_detected,
            code_exec,
            suspicious,
        ) = self._scan_input_flags(text)
        return self._build_input_scan_result(
            text,
            credentials=credentials,
            injection_detected=injection_detected,
            pii_detected=pii_detected,
            code_exec=code_exec,
            suspicious=suspicious,
        )

    def scan_output(self, text: str) -> FilterResult:
        """Scan outgoing text (LLM responses, tool params).

        Redacts credentials and internal info. Blocks on injection
        and potential data exfiltration attempts.

        Args:
            text: Output text to filter.

        Returns:
            FilterResult with redacted text.
        """
        filtered, credentials, blocked = self._redact_credentials(text)
        filtered = self._redact_internal_info(filtered)
        (
            injection_detected,
            exfiltration_detected,
            high_entropy_blocked,
        ) = self._scan_output_flags(filtered, text)
        blocked = (
            blocked
            or injection_detected
            or exfiltration_detected
            or high_entropy_blocked
        )
        return self._build_output_scan_result(
            filtered,
            credentials,
            injection_detected=injection_detected,
            exfiltration_detected=exfiltration_detected,
            blocked=blocked,
        )

    def redact(self, text: str) -> str:
        """Redact credentials and internal info from text.

        Args:
            text: Text to redact.

        Returns:
            Redacted text.
        """
        result = self.scan_output(text)
        return result.text
