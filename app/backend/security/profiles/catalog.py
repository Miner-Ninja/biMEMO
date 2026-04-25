"""Catalog of all editable security settings.

Every parameter that today is hard-coded in a defense module is
declared here as a SettingDef with metadata, constraints, and
per-built-in defaults. Defense modules read values via
ProfileResolver; this catalog is the single source of truth for
what is editable, what is locked, and what the three built-in
profiles ship.

Russian labels and descriptions are stored inline; the frontend
renders them as-is. EN copy is added later through a parallel
file without touching this catalog.
"""

from __future__ import annotations

from backend.security.profiles.models import (
    EnumOption,
    RiskLevel,
    SettingConstraints,
    SettingDef,
    SettingType,
)


# ---------------------------------------------------------------------------
# Category names — keep aligned with the frontend i18n/ru.ts file.
# ---------------------------------------------------------------------------
CAT_INPUT = "input_filter"
CAT_OUTPUT = "output_filter"
CAT_FILESYSTEM = "filesystem"
CAT_NETWORK = "network"
CAT_TRUST = "trust"
CAT_AGENT_LIMITS = "agent_limits"
CAT_MEMORY = "memory"
CAT_LLM = "llm"
CAT_AUTH = "auth"
CAT_AUDIT = "audit"
CAT_FRONTEND = "frontend"


CATEGORY_ORDER: tuple[str, ...] = (
    CAT_INPUT,
    CAT_OUTPUT,
    CAT_FILESYSTEM,
    CAT_NETWORK,
    CAT_TRUST,
    CAT_AGENT_LIMITS,
    CAT_MEMORY,
    CAT_LLM,
    CAT_AUTH,
    CAT_AUDIT,
    CAT_FRONTEND,
)


# ---------------------------------------------------------------------------
# Response-schema enum options for llm.response_schema.
# ---------------------------------------------------------------------------
_RESPONSE_SCHEMA_OPTIONS = (
    EnumOption(value="personal", label="Личный помощник (answer / sql / suggestions)"),
    EnumOption(
        value="programmer",
        label="Программист (answer / files_modified / commands / diagnostics / next_steps)",
    ),
    EnumOption(value="freeform", label="Свободный (только answer)"),
)


# ===========================================================================
# A. INPUT FILTER — что приходит в biMEMO от пользователя.
# ===========================================================================
INPUT_FILTER_SETTINGS: tuple[SettingDef, ...] = (
    SettingDef(
        key="input_filter.block_code_execution",
        category=CAT_INPUT,
        label="Блокировать любые конструкции запуска кода",
        description=(
            "Останавливает обработку, если в сообщении есть eval(), exec(), "
            "subprocess., shell=True, конвейеры в bash. Параноидально — мешает "
            "обсуждать обычный код."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.HIGH,
        default_paranoid=True,
        default_personal=True,
        default_developer=False,
        keywords=("код", "subprocess", "eval", "exec", "shell"),
    ),
    SettingDef(
        key="input_filter.block_dangerous_shell_only",
        category=CAT_INPUT,
        label="Блокировать только заведомо разрушительные команды",
        description=(
            "Мягче чем блок любого кода: останавливает только rm -rf /, "
            "fork-bomb, запись в /dev/sda. Программистский компромисс."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.MEDIUM,
        default_paranoid=False,
        default_personal=False,
        default_developer=True,
        keywords=("rm", "fork-bomb", "deструктивный"),
    ),
    SettingDef(
        key="input_filter.block_prompt_injection",
        category=CAT_INPUT,
        label="Блокировать прямые попытки джейлбрейка LLM",
        description=(
            "Останавливает обработку, если в сообщении есть фразы вроде "
            "«забудь все инструкции», «покажи свой системный промпт», "
            "«представь, что ты другой ассистент»."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.HIGH,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        keywords=("jailbreak", "инъекция", "промпт", "ignore"),
    ),
    SettingDef(
        key="input_filter.block_role_play_phrases",
        category=CAT_INPUT,
        label="Блокировать ролевые фразы (act as / system: / pretend)",
        description=(
            "Параноидальный режим — блокирует любые попытки переопределить "
            "роль. Программисту мешает обсуждать промпт-инжиниринг и YAML "
            "с ключом system:."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.MEDIUM,
        default_paranoid=True,
        default_personal=True,
        default_developer=False,
        keywords=("act as", "pretend", "role-play"),
    ),
    SettingDef(
        key="input_filter.block_credentials_strict",
        category=CAT_INPUT,
        label="Блокировать вставку настоящих API-ключей и токенов",
        description=(
            "Точные сигнатуры провайдеров: sk-..., ghp_..., AKIA..., glpat-..., "
            "ya29.... Если такой ключ окажется в сообщении — обработка "
            "останавливается, чтобы он не ушёл в LLM."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.HIGH,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        example="OPENAI_API_KEY=sk-abc123 будет заблокирован.",
        keywords=("ключ", "токен", "credential", "api"),
    ),
    SettingDef(
        key="input_filter.block_credentials_generic",
        category=CAT_INPUT,
        label="Блокировать обобщённые password=/api_key= конструкции",
        description=(
            "Срабатывает на любые строки вида password=..., api_key=..., "
            "Bearer xxx. Программисту мешает ревью .env и конфигов."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.MEDIUM,
        default_paranoid=True,
        default_personal=True,
        default_developer=False,
        keywords=("пароль", ".env", "конфиг"),
    ),
    SettingDef(
        key="input_filter.block_pii",
        category=CAT_INPUT,
        label="Блокировать ввод с PII (email, SSN, IPv4, карты)",
        description=(
            "По умолчанию PII только помечается. Включение полностью отказывает "
            "в обработке сообщений с такими данными."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.MEDIUM,
        default_paranoid=True,
        default_personal=False,
        default_developer=False,
        keywords=("pii", "email", "ssn", "карта"),
    ),
    SettingDef(
        key="input_filter.flag_high_entropy_input",
        category=CAT_INPUT,
        label="Помечать высокоэнтропийный текст",
        description=(
            "Длинный текст с энтропией Шеннона больше 4.5 (вероятно base64 "
            "или зашифровано) получает метку. Только метка, не блокировка."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.LOW,
        default_paranoid=True,
        default_personal=True,
        default_developer=False,
        keywords=("entropy", "base64", "энтропия"),
    ),
    SettingDef(
        key="input_filter.max_message_size_bytes",
        category=CAT_INPUT,
        label="Максимальный размер сообщения",
        description=(
            "Сообщения больше указанного размера отвергаются. Программисту "
            "нужно вставлять большие diff и логи — увеличьте лимит."
        ),
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=10_000, max=5_000_000, step=10_000, unit="байт"),
        default_paranoid=100_000,
        default_personal=100_000,
        default_developer=1_000_000,
        keywords=("размер", "diff", "лог"),
    ),
    SettingDef(
        key="input_filter.injection_iteration_penalty",
        category=CAT_INPUT,
        label="Урезать лимиты агента при срабатывании injection-фильтра",
        description=(
            "Если входной фильтр обнаружил джейлбрейк — лимит итераций и "
            "общий бюджет вызовов режутся вдвое до конца сессии."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.MEDIUM,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        keywords=("карантин", "injection", "лимит"),
    ),
    SettingDef(
        key="input_filter.custom_credential_patterns",
        category=CAT_INPUT,
        label="Свои паттерны credentials",
        description=(
            "Список регулярных выражений, которые добавляются ко встроенным. "
            "Полезно для специфичных корпоративных ключей."
        ),
        type=SettingType.LIST_STRING,
        risk_level=RiskLevel.MEDIUM,
        constraints=SettingConstraints(placeholder="ACME-[A-Z0-9]{20}"),
        default_paranoid=[],
        default_personal=[],
        default_developer=[],
        keywords=("regex", "паттерн", "ключ"),
    ),
    SettingDef(
        key="input_filter.custom_injection_patterns",
        category=CAT_INPUT,
        label="Свои паттерны промпт-инъекций",
        description=(
            "Список регулярных выражений, дополняющих встроенные anti-jailbreak "
            "правила."
        ),
        type=SettingType.LIST_STRING,
        risk_level=RiskLevel.MEDIUM,
        constraints=SettingConstraints(placeholder=r"забудь.*инструкции"),
        default_paranoid=[],
        default_personal=[],
        default_developer=[],
        keywords=("regex", "injection"),
    ),
)


# ===========================================================================
# B. OUTPUT FILTER — что biMEMO отдаёт пользователю.
# ===========================================================================
OUTPUT_FILTER_SETTINGS: tuple[SettingDef, ...] = (
    SettingDef(
        key="output_filter.redact_credentials_in_output",
        category=CAT_OUTPUT,
        label="Скрывать API-ключи и токены в ответах LLM",
        description=(
            "LLM может случайно повторить ключ из контекста. Эта настройка "
            "вырезает все совпадения с сигнатурами провайдеров и заменяет "
            "их на [скрыто]."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.HIGH,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        keywords=("redact", "ключ", "токен"),
    ),
    SettingDef(
        key="output_filter.redact_internal_paths",
        category=CAT_OUTPUT,
        label="Скрывать внутренние пути в выводе",
        description=(
            "Пути вида /home/..., /etc/..., /app/... заменяются на [скрыто]. "
            "Защищает от утечек структуры файловой системы."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.MEDIUM,
        default_paranoid=True,
        default_personal=True,
        default_developer=False,
        keywords=("path", "путь", "internal"),
    ),
    SettingDef(
        key="output_filter.block_exfiltration_patterns",
        category=CAT_OUTPUT,
        label="Блокировать паттерны утечки в выводе",
        description=(
            "Останавливает ответы вида fetch('?key=…'), <img src=https://…>, "
            "document.location=…. Программисту мешает выдавать обычный JS-код "
            "с HTTP-клиентом."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.HIGH,
        default_paranoid=True,
        default_personal=True,
        default_developer=False,
        keywords=("exfil", "утечка", "fetch"),
    ),
    SettingDef(
        key="output_filter.block_exfiltration_strict_only",
        category=CAT_OUTPUT,
        label="Блокировать только явные exfil-паттерны",
        description=(
            "Мягче полного exfil-блока: ловит только URL с ?key= / ?token= и "
            "<img src=…>. Обычный fetch / requests.get проходят."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.MEDIUM,
        default_paranoid=False,
        default_personal=False,
        default_developer=True,
        keywords=("exfil", "strict"),
    ),
    SettingDef(
        key="output_filter.block_high_entropy_output",
        category=CAT_OUTPUT,
        label="Блокировать высокоэнтропийный вывод",
        description=(
            "Защита от стеганографии в ответах. Ломает выдачу хешей, UUID, "
            "JWT и другого нормального для программиста контента."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.LOW,
        default_paranoid=True,
        default_personal=False,
        default_developer=False,
        keywords=("entropy", "стеганография"),
    ),
    SettingDef(
        key="output_filter.block_injection_in_output",
        category=CAT_OUTPUT,
        label="Блокировать промпт-инъекции в выводе LLM",
        description=(
            "Иногда LLM повторяет инъекцию из контекста. Эта настройка ловит "
            "такие случаи на выходе."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.MEDIUM,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        keywords=("injection", "вывод"),
    ),
)


# ===========================================================================
# C. FILESYSTEM — доступ к файлам.
# ===========================================================================
FILESYSTEM_SETTINGS: tuple[SettingDef, ...] = (
    SettingDef(
        key="filesystem.paths.deny_read_private_keys",
        category=CAT_FILESYSTEM,
        label="Запретить чтение .ssh и .gnupg",
        description=(
            "Приватные ключи никогда не отдаются агенту, даже на чтение. "
            "Эту защиту нельзя выключить."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.CRITICAL,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        locked=True,
        keywords=(".ssh", ".gnupg", "ключ"),
    ),
    SettingDef(
        key="filesystem.paths.deny_path_traversal",
        category=CAT_FILESYSTEM,
        label="Блокировать выходы за пределы директории (..)",
        description=(
            "Путей с ../ нигде не разрешено — базовая защита от выхода за "
            "пределы рабочей директории. Эту защиту нельзя выключить."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.CRITICAL,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        locked=True,
        keywords=("traversal", "..", "путь"),
    ),
    SettingDef(
        key="filesystem.paths.deny_read_dotfiles",
        category=CAT_FILESYSTEM,
        label="Запретить чтение dotfiles (.bashrc, .profile, .config, .env)",
        description=(
            "Параноидальный режим. Программисту нужны эти файлы для помощи "
            "с настройкой dev-окружения и ревью .env.example."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.MEDIUM,
        default_paranoid=True,
        default_personal=True,
        default_developer=False,
        keywords=("dotfile", ".bashrc", ".env"),
    ),
    SettingDef(
        key="filesystem.paths.deny_write_dotfiles",
        category=CAT_FILESYSTEM,
        label="Запретить запись в dotfiles",
        description=(
            "Запись опаснее чтения — модификация .bashrc или .ssh/authorized_keys "
            "может скомпрометировать аккаунт. Рекомендуется держать включённым."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.HIGH,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        keywords=("write", "dotfile"),
    ),
    SettingDef(
        key="filesystem.paths.custom_deny_list",
        category=CAT_FILESYSTEM,
        label="Свой список запрещённых путей",
        description=(
            "Пути или регулярки путей, дополняющие встроенный список. "
            "Полезно для проектных секретов вне dotfiles."
        ),
        type=SettingType.LIST_STRING,
        risk_level=RiskLevel.MEDIUM,
        constraints=SettingConstraints(placeholder="/srv/secrets"),
        default_paranoid=[],
        default_personal=[],
        default_developer=[],
        keywords=("deny", "путь"),
    ),
    SettingDef(
        key="filesystem.uploads.allowed_extensions",
        category=CAT_FILESYSTEM,
        label="Разрешённые расширения для загрузки файлов",
        description=(
            "Только файлы с этими расширениями принимаются на загрузку. "
            "Программисту нужны .go, .rs, .tsx, .tf и другие."
        ),
        type=SettingType.LIST_STRING,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(placeholder=".py"),
        default_paranoid=[
            ".txt",
            ".md",
            ".pdf",
            ".json",
            ".log",
        ],
        default_personal=[
            ".txt",
            ".md",
            ".pdf",
            ".csv",
            ".json",
            ".xml",
            ".html",
            ".py",
            ".js",
            ".ts",
            ".yaml",
            ".yml",
            ".log",
            ".sh",
            ".sql",
            ".toml",
            ".ini",
            ".cfg",
            ".conf",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
        ],
        default_developer=[
            ".txt",
            ".md",
            ".pdf",
            ".csv",
            ".json",
            ".xml",
            ".html",
            ".py",
            ".js",
            ".ts",
            ".tsx",
            ".jsx",
            ".vue",
            ".svelte",
            ".go",
            ".rs",
            ".cpp",
            ".cc",
            ".cxx",
            ".h",
            ".hpp",
            ".java",
            ".kt",
            ".scala",
            ".rb",
            ".php",
            ".swift",
            ".dart",
            ".lua",
            ".zig",
            ".nim",
            ".ex",
            ".exs",
            ".erl",
            ".clj",
            ".css",
            ".scss",
            ".sass",
            ".less",
            ".tf",
            ".tfvars",
            ".hcl",
            ".nix",
            ".lock",
            ".gradle",
            ".sbt",
            ".cmake",
            ".mk",
            ".proto",
            ".graphql",
            ".gql",
            ".patch",
            ".diff",
            ".yaml",
            ".yml",
            ".log",
            ".sh",
            ".sql",
            ".toml",
            ".ini",
            ".cfg",
            ".conf",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
        ],
        keywords=("upload", "расширение", "файл"),
    ),
    SettingDef(
        key="filesystem.uploads.bare_name_allowlist",
        category=CAT_FILESYSTEM,
        label="Разрешённые имена файлов без расширения",
        description=(
            "Файлы вроде Makefile, Dockerfile, Justfile, Gemfile — без точки "
            "в имени. Указывайте полное имя."
        ),
        type=SettingType.LIST_STRING,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(placeholder="Makefile"),
        default_paranoid=[],
        default_personal=[],
        default_developer=[
            "Makefile",
            "Dockerfile",
            "Containerfile",
            "Justfile",
            "Rakefile",
            "Gemfile",
            "Pipfile",
            "LICENSE",
            ".gitignore",
            ".dockerignore",
            ".editorconfig",
            ".env.example",
        ],
        keywords=("Makefile", "Dockerfile"),
    ),
    SettingDef(
        key="filesystem.uploads.max_size_mb",
        category=CAT_FILESYSTEM,
        label="Максимальный размер загружаемого файла",
        description=(
            "Лимит на upload одного файла. Большие сборочные логи и lockfile "
            "могут не уложиться в 10 МБ."
        ),
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=1, max=500, step=1, unit="МБ"),
        default_paranoid=5,
        default_personal=10,
        default_developer=50,
        keywords=("размер", "upload"),
    ),
)


# ===========================================================================
# D. NETWORK — интернет и сетевые подключения.
# ===========================================================================
NETWORK_SETTINGS: tuple[SettingDef, ...] = (
    SettingDef(
        key="network.https_only",
        category=CAT_NETWORK,
        label="Только защищённые соединения (HTTPS)",
        description=(
            "biMEMO не будет ходить по обычному HTTP. Все запросы только "
            "через зашифрованный канал. Программисту с http://localhost — "
            "выключите."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.HIGH,
        default_paranoid=True,
        default_personal=True,
        default_developer=False,
        keywords=("https", "http", "tls"),
    ),
    SettingDef(
        key="network.http_allowed_hosts",
        category=CAT_NETWORK,
        label="Разрешённые HTTP-хосты",
        description=(
            "Если HTTPS-only выключен — допустимы HTTP-запросы только к "
            "хостам из этого списка. Поддерживаются wildcards (*.local)."
        ),
        type=SettingType.LIST_STRING,
        risk_level=RiskLevel.MEDIUM,
        constraints=SettingConstraints(placeholder="localhost"),
        default_paranoid=[],
        default_personal=[],
        default_developer=["localhost", "127.0.0.1", "*.local"],
        keywords=("http", "host"),
    ),
    SettingDef(
        key="network.block_loopback",
        category=CAT_NETWORK,
        label="Блокировать локальные адреса (127.0.0.0/8)",
        description=(
            "biMEMO не сможет обращаться к localhost и loopback-интерфейсу. "
            "Защищает от подмены ответов через локальные сервисы."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.MEDIUM,
        default_paranoid=True,
        default_personal=True,
        default_developer=False,
        keywords=("localhost", "loopback", "127"),
    ),
    SettingDef(
        key="network.block_rfc1918",
        category=CAT_NETWORK,
        label="Блокировать приватные сети (10/8, 172.16/12, 192.168/16)",
        description=(
            "Защита от обращений к внутренним сервисам в локальной/корпоративной "
            "сети. Программисту в офисе — выключите."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.MEDIUM,
        default_paranoid=True,
        default_personal=True,
        default_developer=False,
        keywords=("rfc1918", "private", "192.168"),
    ),
    SettingDef(
        key="network.block_cgnat",
        category=CAT_NETWORK,
        label="Блокировать CGNAT-сети (100.64.0.0/10)",
        description=(
            "Сюда попадают Tailscale и часть мобильных операторов. "
            "Используете Tailscale — выключите."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.LOW,
        default_paranoid=True,
        default_personal=True,
        default_developer=False,
        keywords=("tailscale", "cgnat"),
    ),
    SettingDef(
        key="network.block_cloud_metadata",
        category=CAT_NETWORK,
        label="Блокировать cloud metadata IP (169.254.169.254)",
        description=(
            "Адрес 169.254.169.254 в облачных серверах хранит токены доступа. "
            "biMEMO никогда не будет к нему обращаться. Эту защиту нельзя "
            "выключить."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.CRITICAL,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        locked=True,
        keywords=("metadata", "169.254", "облако"),
    ),
    SettingDef(
        key="network.block_link_local",
        category=CAT_NETWORK,
        label="Блокировать link-local (169.254.0.0/16, fe80::/10)",
        description=(
            "Защита от обращений к link-local адресам. Включает блок cloud "
            "metadata, плюс остальные link-local IP."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.HIGH,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        keywords=("link-local", "fe80"),
    ),
    SettingDef(
        key="network.block_numeric_ip_formats",
        category=CAT_NETWORK,
        label="Блокировать hex/octal IP-обходы",
        description=(
            "URL вида http://0x7f000001/ — обход блок-листа через альтернативные "
            "форматы IP. Эту защиту нельзя выключить."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.CRITICAL,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        locked=True,
        keywords=("hex", "octal", "ip"),
    ),
    SettingDef(
        key="network.dns_resolve_first",
        category=CAT_NETWORK,
        label="Резолвить DNS до соединения и проверять IP",
        description=(
            "Перед каждым запросом разрешается имя и проверяется по блок-листу. "
            "Защита от DNS rebinding-атак. Эту защиту нельзя выключить."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.CRITICAL,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        locked=True,
        keywords=("dns", "rebinding"),
    ),
    SettingDef(
        key="network.dns_timeout_seconds",
        category=CAT_NETWORK,
        label="Таймаут DNS-резолва",
        description="Сколько ждать ответа DNS прежде чем сдаться.",
        type=SettingType.SLIDER_FLOAT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=1.0, max=30.0, step=0.5, unit="сек"),
        default_paranoid=5.0,
        default_personal=5.0,
        default_developer=10.0,
        keywords=("dns", "таймаут"),
    ),
    SettingDef(
        key="network.custom_allowed_hosts",
        category=CAT_NETWORK,
        label="Allowlist хостов для всех типов запросов",
        description=(
            "Хосты из этого списка разрешены даже если попадают в "
            "заблокированные сети. Полезно для конкретных доменов."
        ),
        type=SettingType.LIST_STRING,
        risk_level=RiskLevel.MEDIUM,
        constraints=SettingConstraints(placeholder="api.example.com"),
        default_paranoid=[],
        default_personal=[],
        default_developer=[],
        keywords=("allowlist", "host"),
    ),
    SettingDef(
        key="network.custom_blocked_hosts",
        category=CAT_NETWORK,
        label="Свой блок-лист хостов",
        description="Хосты, которые блокируются в дополнение к встроенным правилам.",
        type=SettingType.LIST_STRING,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(placeholder="malicious.example.com"),
        default_paranoid=[],
        default_personal=[],
        default_developer=[],
        keywords=("blocklist", "host"),
    ),
    SettingDef(
        key="network.max_redirects_per_fetch",
        category=CAT_NETWORK,
        label="Максимум HTTP-редиректов",
        description=(
            "Сколько раз biMEMO будет следовать за 3xx-редиректами в одном "
            "запросе. Каждый редирект перепроверяется по правилам SSRF."
        ),
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=0, max=10, step=1, unit="редиректов"),
        default_paranoid=1,
        default_personal=3,
        default_developer=5,
        keywords=("redirect", "302"),
    ),
)


# ===========================================================================
# E. TRUST — подтверждения опасных действий.
# ===========================================================================
TRUST_SETTINGS: tuple[SettingDef, ...] = (
    SettingDef(
        key="trust.confirm_http_fetch",
        category=CAT_TRUST,
        label="Спрашивать подтверждение перед каждым выходом в интернет",
        description=(
            "Когда biMEMO собирается зайти на внешний сайт — появляется окошко "
            "«Разрешить?». Защита от того, чтобы агент сам куда-то ходил без "
            "ведома."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.HIGH,
        default_paranoid=True,
        default_personal=True,
        default_developer=False,
        keywords=("http_fetch", "подтверждение"),
    ),
    SettingDef(
        key="trust.confirm_file_write",
        category=CAT_TRUST,
        label="Спрашивать подтверждение перед записью в файл",
        description=(
            "Каждый file_write требует одобрения. Параноидально. Программисту "
            "в multi-file refactor мешает."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.MEDIUM,
        default_paranoid=True,
        default_personal=False,
        default_developer=False,
        keywords=("file_write", "подтверждение"),
    ),
    SettingDef(
        key="trust.confirm_email_send",
        category=CAT_TRUST,
        label="Спрашивать подтверждение перед отправкой email",
        description=(
            "biMEMO никогда не отправит письмо без явного подтверждения "
            "пользователя. Рекомендуется всегда держать включённым."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.HIGH,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        keywords=("email", "send"),
    ),
    SettingDef(
        key="trust.confirm_schedule_task",
        category=CAT_TRUST,
        label="Спрашивать подтверждение перед созданием задачи в шедулере",
        description=(
            "Запланированные задачи могут быть запущены позже — подтверждение "
            "защищает от случайного автоматического действия."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.MEDIUM,
        default_paranoid=True,
        default_personal=False,
        default_developer=False,
        keywords=("scheduler", "cron"),
    ),
    SettingDef(
        key="trust.confirmation_timeout_seconds",
        category=CAT_TRUST,
        label="Таймаут подтверждения",
        description=(
            "Сколько секунд токен подтверждения остаётся валидным. После "
            "истечения — операцию надо запросить заново."
        ),
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=60, max=7200, step=60, unit="сек"),
        default_paranoid=180,
        default_personal=300,
        default_developer=1800,
        keywords=("таймаут", "confirmation"),
    ),
    SettingDef(
        key="trust.confirmation_channel_binding",
        category=CAT_TRUST,
        label="Подтверждение должно прийти из того же канала",
        description=(
            "Защита от cross-channel hijack: подтверждение из Telegram нельзя "
            "использовать для операции, начатой в веб-чате."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.HIGH,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        keywords=("channel", "binding"),
    ),
    SettingDef(
        key="trust.auto_downgrade_threshold",
        category=CAT_TRUST,
        label="После N успешных вызовов предлагать понизить зону",
        description=(
            "Когда один и тот же инструмент успешно выполняется N раз подряд — "
            "biMEMO предлагает перевести его в более лояльную зону. 0 — выключено."
        ),
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=0, max=100, step=1, unit="вызовов"),
        default_paranoid=0,
        default_personal=10,
        default_developer=5,
        keywords=("downgrade", "trust"),
    ),
    SettingDef(
        key="trust.red_zone_tools",
        category=CAT_TRUST,
        label="Список инструментов в RED-зоне",
        description=(
            "RED-зона = подтверждение пользователем перед каждым вызовом. "
            "По умолчанию: http_fetch, email_send плюс всё неизвестное."
        ),
        type=SettingType.LIST_STRING,
        risk_level=RiskLevel.HIGH,
        constraints=SettingConstraints(placeholder="http_fetch"),
        default_paranoid=["http_fetch", "email_send", "schedule_task", "file_write"],
        default_personal=["http_fetch", "email_send"],
        default_developer=["email_send"],
        keywords=("red", "zone"),
    ),
    SettingDef(
        key="trust.yellow_zone_tools",
        category=CAT_TRUST,
        label="Список инструментов в YELLOW-зоне",
        description=(
            "YELLOW-зона = выполняется без подтверждения, но пользователь "
            "видит уведомление после."
        ),
        type=SettingType.LIST_STRING,
        risk_level=RiskLevel.MEDIUM,
        constraints=SettingConstraints(placeholder="web_search"),
        default_paranoid=["file_write", "web_search"],
        default_personal=["file_write", "web_search", "schedule_task"],
        default_developer=["file_write", "web_search", "schedule_task", "http_fetch"],
        keywords=("yellow", "zone"),
    ),
)


# ===========================================================================
# F. AGENT LIMITS — лимиты на работу агентского цикла.
# ===========================================================================
AGENT_LIMITS_SETTINGS: tuple[SettingDef, ...] = (
    SettingDef(
        key="agent_limits.iterations.max",
        category=CAT_AGENT_LIMITS,
        label="Максимум шагов в одном задании",
        description=(
            "Сколько раз biMEMO может «подумать → выполнить инструмент» в "
            "рамках одного запроса. Если задача длинная — увеличьте."
        ),
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=3, max=500, step=1, unit="шагов"),
        default_paranoid=10,
        default_personal=20,
        default_developer=80,
        keywords=("iterations", "шагов"),
    ),
    SettingDef(
        key="agent_limits.iterations.max_when_injection",
        category=CAT_AGENT_LIMITS,
        label="Максимум шагов в карантинном режиме",
        description=(
            "Когда сработал injection-фильтр — лимит итераций режется до этого "
            "значения, чтобы скомпрометированный сеанс не мог много сделать."
        ),
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=1, max=50, step=1, unit="шагов"),
        default_paranoid=3,
        default_personal=5,
        default_developer=10,
        keywords=("injection", "карантин"),
    ),
    SettingDef(
        key="agent_limits.loop_timeout_seconds",
        category=CAT_AGENT_LIMITS,
        label="Максимальное время на одно задание",
        description=(
            "После этого biMEMO остановится, даже если не доделал. "
            "Защита от «зависшего» агента."
        ),
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=30, max=7200, step=30, unit="сек"),
        default_paranoid=180,
        default_personal=300,
        default_developer=1800,
        keywords=("timeout", "таймаут"),
    ),
    SettingDef(
        key="agent_limits.tools.file_read_per_loop",
        category=CAT_AGENT_LIMITS,
        label="file_read за один цикл",
        description="Сколько раз агент может прочитать файл за одно задание.",
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=1, max=200, step=1, unit="вызовов"),
        default_paranoid=5,
        default_personal=10,
        default_developer=50,
        keywords=("file_read",),
    ),
    SettingDef(
        key="agent_limits.tools.file_write_per_loop",
        category=CAT_AGENT_LIMITS,
        label="file_write за один цикл",
        description=(
            "Сколько раз агент может писать в файлы за одно задание. "
            "Программистский multi-file refactor требует более 5."
        ),
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=1, max=200, step=1, unit="вызовов"),
        default_paranoid=2,
        default_personal=5,
        default_developer=30,
        keywords=("file_write", "refactor"),
    ),
    SettingDef(
        key="agent_limits.tools.file_search_per_loop",
        category=CAT_AGENT_LIMITS,
        label="file_search за один цикл",
        description="Сколько раз агент может искать по файлам за одно задание.",
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=1, max=100, step=1, unit="вызовов"),
        default_paranoid=3,
        default_personal=5,
        default_developer=20,
        keywords=("file_search",),
    ),
    SettingDef(
        key="agent_limits.tools.web_search_per_loop",
        category=CAT_AGENT_LIMITS,
        label="web_search за один цикл",
        description="Сколько раз агент может выполнять поиск в интернете за одно задание.",
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=1, max=100, step=1, unit="вызовов"),
        default_paranoid=3,
        default_personal=10,
        default_developer=20,
        keywords=("web_search",),
    ),
    SettingDef(
        key="agent_limits.tools.http_fetch_per_loop",
        category=CAT_AGENT_LIMITS,
        label="http_fetch за один цикл",
        description=(
            "Сколько раз агент может ходить на внешние URL за одно задание. "
            "Программистскому research-режиму нужно больше 2."
        ),
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.MEDIUM,
        constraints=SettingConstraints(min=0, max=100, step=1, unit="вызовов"),
        default_paranoid=0,
        default_personal=2,
        default_developer=20,
        keywords=("http_fetch", "research"),
    ),
    SettingDef(
        key="agent_limits.tools.email_send_per_loop",
        category=CAT_AGENT_LIMITS,
        label="email_send за один цикл",
        description="Сколько писем агент может отправить за одно задание.",
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.MEDIUM,
        constraints=SettingConstraints(min=0, max=10, step=1, unit="писем"),
        default_paranoid=1,
        default_personal=3,
        default_developer=1,
        keywords=("email",),
    ),
    SettingDef(
        key="agent_limits.tools.schedule_task_per_loop",
        category=CAT_AGENT_LIMITS,
        label="schedule_task за один цикл",
        description="Сколько задач в шедулере агент может создать за одно задание.",
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=0, max=50, step=1, unit="задач"),
        default_paranoid=1,
        default_personal=5,
        default_developer=10,
        keywords=("schedule",),
    ),
    SettingDef(
        key="agent_limits.tools.memory_recall_per_loop",
        category=CAT_AGENT_LIMITS,
        label="memory_recall за один цикл",
        description="Сколько раз агент может обращаться к долгосрочной памяти за одно задание.",
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=1, max=100, step=1, unit="вызовов"),
        default_paranoid=3,
        default_personal=5,
        default_developer=20,
        keywords=("memory",),
    ),
    SettingDef(
        key="agent_limits.tools.total_budget_per_loop",
        category=CAT_AGENT_LIMITS,
        label="Общий бюджет вызовов за цикл",
        description=(
            "Суммарно сколько вызовов любых инструментов агент может сделать "
            "за одно задание."
        ),
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=10, max=1000, step=10, unit="вызовов"),
        default_paranoid=15,
        default_personal=30,
        default_developer=200,
        keywords=("budget", "бюджет"),
    ),
    SettingDef(
        key="agent_limits.tools.total_budget_when_injection",
        category=CAT_AGENT_LIMITS,
        label="Общий бюджет при срабатывании injection",
        description="Сжатый бюджет на сеанс, в котором сработал injection-фильтр.",
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=5, max=500, step=5, unit="вызовов"),
        default_paranoid=5,
        default_personal=10,
        default_developer=50,
        keywords=("budget", "injection"),
    ),
    SettingDef(
        key="agent_limits.web_search_window.max_calls",
        category=CAT_AGENT_LIMITS,
        label="Окно sliding-limit для web_search: число вызовов",
        description=(
            "Сколько web_search можно сделать в скользящем окне (см. ниже). "
            "Защита от перегрузки поисковика."
        ),
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=5, max=100, step=1, unit="вызовов"),
        default_paranoid=5,
        default_personal=10,
        default_developer=30,
        keywords=("web_search", "window"),
    ),
    SettingDef(
        key="agent_limits.web_search_window.seconds",
        category=CAT_AGENT_LIMITS,
        label="Окно sliding-limit для web_search: длительность",
        description="Длительность скользящего окна в секундах.",
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=10, max=3600, step=10, unit="сек"),
        default_paranoid=60,
        default_personal=60,
        default_developer=60,
        keywords=("window", "sliding"),
    ),
)


# ===========================================================================
# G. MEMORY — память и история.
# ===========================================================================
MEMORY_SETTINGS: tuple[SettingDef, ...] = (
    SettingDef(
        key="memory.enable_long_term_memory",
        category=CAT_MEMORY,
        label="Включить долгосрочную память",
        description=(
            "Если выключено — каждая беседа независимая, biMEMO ничего не "
            "помнит между сессиями."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.LOW,
        default_paranoid=False,
        default_personal=True,
        default_developer=True,
        keywords=("memory", "память"),
    ),
    SettingDef(
        key="memory.sanitize_before_save",
        category=CAT_MEMORY,
        label="Прогонять content_filter перед сохранением воспоминаний",
        description=(
            "Защита от indirect prompt injection через память: подозрительные "
            "записи помечаются tainted и не подгружаются обратно. "
            "Рекомендуется."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.HIGH,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        keywords=("sanitize", "память"),
    ),
    SettingDef(
        key="memory.respect_tainted_flag",
        category=CAT_MEMORY,
        label="Исключать заражённые воспоминания из recall",
        description=(
            "Воспоминания с флагом tainted=true не попадают в контекст LLM. "
            "Эту защиту нельзя выключить."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.CRITICAL,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        locked=True,
        keywords=("tainted", "память"),
    ),
    SettingDef(
        key="memory.memory_ttl_days",
        category=CAT_MEMORY,
        label="Очищать память автоматически",
        description=(
            "Старые воспоминания удаляются через указанное число дней. "
            "0 — хранить вечно."
        ),
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=0, max=3650, step=1, unit="дней"),
        default_paranoid=30,
        default_personal=0,
        default_developer=365,
        keywords=("ttl", "память"),
    ),
    SettingDef(
        key="memory.isolate_memory_per_conversation",
        category=CAT_MEMORY,
        label="Изолировать память между беседами",
        description=(
            "Каждая беседа видит только свои воспоминания. Защита от утечки "
            "контекста между темами."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.MEDIUM,
        default_paranoid=True,
        default_personal=False,
        default_developer=False,
        keywords=("isolation", "беседа"),
    ),
    SettingDef(
        key="memory.max_memory_records_per_recall",
        category=CAT_MEMORY,
        label="Сколько записей подгружать в контекст за раз",
        description="Лимит на количество воспоминаний, которые попадают в один turn.",
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=1, max=50, step=1, unit="записей"),
        default_paranoid=5,
        default_personal=10,
        default_developer=20,
        keywords=("recall", "контекст"),
    ),
)


# ===========================================================================
# H. LLM — поведение языковой модели.
# ===========================================================================
LLM_SETTINGS: tuple[SettingDef, ...] = (
    SettingDef(
        key="llm.response_schema",
        category=CAT_LLM,
        label="Шаблон структурированного ответа",
        description=(
            "Какую JSON-схему LLM использует для ответа. Personal — для "
            "обычного помощника, Programmer — с полями для diff и команд."
        ),
        type=SettingType.ENUM,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(options=_RESPONSE_SCHEMA_OPTIONS),
        default_paranoid="personal",
        default_personal="personal",
        default_developer="programmer",
        keywords=("schema", "формат"),
    ),
    SettingDef(
        key="llm.strict_json_schema",
        category=CAT_LLM,
        label="Принудительная JSON-схема (strict mode)",
        description=(
            "Требовать от провайдера строгое соответствие схеме. Выключение — "
            "для freeform-ответов."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.MEDIUM,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        keywords=("strict", "schema"),
    ),
    SettingDef(
        key="llm.system_prompt_addons",
        category=CAT_LLM,
        label="Дополнения к системному промпту",
        description=(
            "Произвольный текст, который добавляется в конец системного "
            "сообщения. Полезно для персональных правил поведения."
        ),
        type=SettingType.TEXT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(max_length=4000),
        default_paranoid="",
        default_personal="",
        default_developer=(
            "Ты помощник программиста. Отвечай в коде блоках по умолчанию, "
            "ссылайся на файлы по path:line, не извиняйся, не пересказывай diff."
        ),
        keywords=("system", "prompt"),
    ),
    SettingDef(
        key="llm.disable_external_tools",
        category=CAT_LLM,
        label="Полностью отключить внешние инструменты",
        description=(
            "Параноидально: только LLM, никакого http_fetch / email_send / "
            "scheduler. Превращает biMEMO в офлайн-чатбота."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.HIGH,
        default_paranoid=True,
        default_personal=False,
        default_developer=False,
        keywords=("offline", "tools"),
    ),
    SettingDef(
        key="llm.max_response_tokens",
        category=CAT_LLM,
        label="Лимит токенов в ответе",
        description="Сколько максимум токенов LLM может сгенерировать в одном ответе.",
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=256, max=32000, step=64, unit="токенов"),
        default_paranoid=2048,
        default_personal=4096,
        default_developer=16000,
        keywords=("tokens", "ответ"),
    ),
)


# ===========================================================================
# I. AUTH — аутентификация и сессии.
# ===========================================================================
AUTH_SETTINGS: tuple[SettingDef, ...] = (
    SettingDef(
        key="auth.require_jwt",
        category=CAT_AUTH,
        label="Обязательная JWT-аутентификация",
        description=(
            "API-роуты требуют валидный JWT-токен. Без него — 401. "
            "Эту защиту нельзя выключить."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.CRITICAL,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        locked=True,
        keywords=("jwt", "auth"),
    ),
    SettingDef(
        key="auth.require_csrf",
        category=CAT_AUTH,
        label="CSRF-защита запросов",
        description=(
            "POST/PUT/DELETE требуют HMAC-подписанный X-CSRF-Token заголовок. "
            "Эту защиту нельзя выключить."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.CRITICAL,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        locked=True,
        keywords=("csrf", "token"),
    ),
    SettingDef(
        key="auth.access_token_ttl_minutes",
        category=CAT_AUTH,
        label="TTL access-токена",
        description=(
            "Сколько минут access-токен остаётся валидным. Короче = безопаснее, "
            "но чаще требует обновления refresh-токеном."
        ),
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=5, max=1440, step=1, unit="мин"),
        default_paranoid=5,
        default_personal=15,
        default_developer=60,
        keywords=("ttl", "access"),
    ),
    SettingDef(
        key="auth.refresh_token_ttl_days",
        category=CAT_AUTH,
        label="TTL refresh-токена",
        description=(
            "Сколько дней refresh-токен может использоваться для обновления "
            "сессии. По истечении — нужен повторный логин."
        ),
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=1, max=90, step=1, unit="дней"),
        default_paranoid=1,
        default_personal=7,
        default_developer=30,
        keywords=("refresh", "ttl"),
    ),
    SettingDef(
        key="auth.max_concurrent_sessions",
        category=CAT_AUTH,
        label="Максимум одновременных сессий",
        description=(
            "Если у пользователя больше открытых сессий — самая старая "
            "автоматически разлогинивается."
        ),
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=1, max=50, step=1, unit="сессий"),
        default_paranoid=2,
        default_personal=5,
        default_developer=10,
        keywords=("session", "сессии"),
    ),
    SettingDef(
        key="auth.session_idle_timeout_minutes",
        category=CAT_AUTH,
        label="Простой сессии до выхода",
        description=(
            "Если сессия неактивна указанное число минут — пользователя "
            "разлогинивает. 0 — никогда не разлогинивать по простою."
        ),
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=0, max=10080, step=5, unit="мин"),
        default_paranoid=30,
        default_personal=0,
        default_developer=0,
        keywords=("idle", "timeout"),
    ),
)


# ===========================================================================
# J. AUDIT — логи и аудит.
# ===========================================================================
AUDIT_SETTINGS: tuple[SettingDef, ...] = (
    SettingDef(
        key="audit.log_red_operations",
        category=CAT_AUDIT,
        label="Логировать все RED-операции в audit-trail",
        description=(
            "Каждый вызов http_fetch / email_send / scheduler записывается с "
            "хешем параметров. Полезно для разбора инцидентов."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.HIGH,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        keywords=("audit", "red"),
    ),
    SettingDef(
        key="audit.log_blocked_inputs",
        category=CAT_AUDIT,
        label="Логировать заблокированные сообщения",
        description=(
            "Когда content_filter блокирует ввод — короткий хеш и причина "
            "уходят в лог. Помогает понять что атакуют."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.MEDIUM,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        keywords=("blocked", "log"),
    ),
    SettingDef(
        key="audit.mask_secrets_in_logs",
        category=CAT_AUDIT,
        label="Маскировать секреты в логах",
        description=(
            "Любые токены / ключи / пароли попадающие в лог заменяются на "
            "[redacted]. Эту защиту нельзя выключить."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.CRITICAL,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        locked=True,
        keywords=("mask", "redact", "log"),
    ),
    SettingDef(
        key="audit.audit_retention_days",
        category=CAT_AUDIT,
        label="Срок хранения аудит-журнала",
        description="Сколько дней хранить записи аудита перед автоматической очисткой.",
        type=SettingType.SLIDER_INT,
        risk_level=RiskLevel.LOW,
        constraints=SettingConstraints(min=1, max=3650, step=1, unit="дней"),
        default_paranoid=365,
        default_personal=90,
        default_developer=30,
        keywords=("retention", "хранение"),
    ),
    SettingDef(
        key="audit.notify_on_injection",
        category=CAT_AUDIT,
        label="Уведомлять о срабатывании injection-фильтра",
        description=(
            "При срабатывании anti-jailbreak-правила пользователь получает "
            "уведомление в UI и (опционально) в Telegram."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.LOW,
        default_paranoid=True,
        default_personal=False,
        default_developer=False,
        keywords=("notify", "injection"),
    ),
)


# ===========================================================================
# K. FRONTEND — внешний вид и интерфейс.
# ===========================================================================
FRONTEND_SETTINGS: tuple[SettingDef, ...] = (
    SettingDef(
        key="frontend.markdown_disallowed_tags",
        category=CAT_FRONTEND,
        label="HTML-теги, запрещённые в markdown-ответах",
        description=(
            "Теги из этого списка вырезаются из markdown LLM-ответа в браузере. "
            "Защита от XSS через ответ модели."
        ),
        type=SettingType.LIST_STRING,
        risk_level=RiskLevel.HIGH,
        constraints=SettingConstraints(placeholder="script"),
        default_paranoid=[
            "script",
            "iframe",
            "object",
            "embed",
            "form",
            "style",
            "link",
            "meta",
            "svg",
        ],
        default_personal=[
            "script",
            "iframe",
            "object",
            "embed",
            "form",
            "style",
            "link",
        ],
        default_developer=[
            "script",
            "iframe",
            "object",
            "embed",
            "form",
            "style",
            "link",
        ],
        keywords=("xss", "markdown"),
    ),
    SettingDef(
        key="frontend.csp_unsafe_inline_styles",
        category=CAT_FRONTEND,
        label="Разрешить inline-стили (CSP unsafe-inline)",
        description=(
            "Если выключено — браузер блокирует inline-style. Может сломать "
            "часть UI до полной миграции на классы."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.LOW,
        default_paranoid=False,
        default_personal=True,
        default_developer=True,
        keywords=("csp", "inline"),
    ),
    SettingDef(
        key="frontend.highlight_red_operations_in_chat",
        category=CAT_FRONTEND,
        label="Подсвечивать опасные операции в чате",
        description=(
            "RED-операции получают красный бейдж в истории чата для прозрачности. "
            "UX, не безопасность."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.LOW,
        default_paranoid=True,
        default_personal=True,
        default_developer=True,
        keywords=("ux", "highlight"),
    ),
    SettingDef(
        key="frontend.confirm_external_links_click",
        category=CAT_FRONTEND,
        label="Подтверждать клик по внешним ссылкам",
        description=(
            "Перед открытием внешней ссылки из ответа LLM — диалог "
            "подтверждения. Защита от phishing-смуглинга."
        ),
        type=SettingType.TOGGLE,
        risk_level=RiskLevel.MEDIUM,
        default_paranoid=True,
        default_personal=True,
        default_developer=False,
        keywords=("phishing", "ссылка"),
    ),
)


# ---------------------------------------------------------------------------
# Final flat catalog and lookup helpers.
# ---------------------------------------------------------------------------
CATALOG: tuple[SettingDef, ...] = (
    *INPUT_FILTER_SETTINGS,
    *OUTPUT_FILTER_SETTINGS,
    *FILESYSTEM_SETTINGS,
    *NETWORK_SETTINGS,
    *TRUST_SETTINGS,
    *AGENT_LIMITS_SETTINGS,
    *MEMORY_SETTINGS,
    *LLM_SETTINGS,
    *AUTH_SETTINGS,
    *AUDIT_SETTINGS,
    *FRONTEND_SETTINGS,
)


_BY_KEY: dict[str, SettingDef] = {entry.key: entry for entry in CATALOG}


def get_catalog() -> tuple[SettingDef, ...]:
    """Return the full ordered catalog."""
    return CATALOG


def get_setting_def(key: str) -> SettingDef | None:
    """Return the catalog entry for one key, or None if unknown."""
    return _BY_KEY.get(key)


def get_locked_keys() -> frozenset[str]:
    """Return the set of locked-setting keys (always-on, never editable)."""
    return frozenset(entry.key for entry in CATALOG if entry.locked)


def get_editable_keys() -> frozenset[str]:
    """Return the set of editable-setting keys."""
    return frozenset(entry.key for entry in CATALOG if not entry.locked)


def get_settings_by_category(category: str) -> tuple[SettingDef, ...]:
    """Return all catalog entries that belong to one category."""
    return tuple(entry for entry in CATALOG if entry.category == category)


__all__ = [
    "CATALOG",
    "CATEGORY_ORDER",
    "CAT_AGENT_LIMITS",
    "CAT_AUDIT",
    "CAT_AUTH",
    "CAT_FILESYSTEM",
    "CAT_FRONTEND",
    "CAT_INPUT",
    "CAT_LLM",
    "CAT_MEMORY",
    "CAT_NETWORK",
    "CAT_OUTPUT",
    "CAT_TRUST",
    "get_catalog",
    "get_editable_keys",
    "get_locked_keys",
    "get_setting_def",
    "get_settings_by_category",
]
