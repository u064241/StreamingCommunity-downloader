import re
import threading

from random_user_agent.user_agent import UserAgent
from random_user_agent.params import SoftwareName, OperatingSystem

# Windows forbids \ / : * ? " < > | ; POSIX only forbids / and NUL. The filter is
# applied on every platform regardless: titles come from third-party sites and
# end up in filesystem paths, so a title containing "../" must not become a
# traversal just because the container happens to run Linux.
_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


# Device names Windows resolves before it looks at the directory: a file called
# "CON.mkv" or "NUL" cannot be created there, with or without an extension, and
# the same library is routinely an SMB share written from Linux.
_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# The longest a whole file name (stem, " - title", extension) is allowed to be.
# Filesystems stop at 255, and the staging names written beside the file while
# it is assembled add up to 45 characters to it (".…converting-<32 hex>").
MAX_FILENAME = 200


def sanitize_filename(name: str) -> str:
    # Windows drops trailing dots and spaces silently, so "Title." and "Title"
    # would be two names for one file.
    cleaned = _FORBIDDEN.sub("", str(name)).strip(" .")
    # "." and ".." survive the character filter but are not usable names.
    if not cleaned or set(cleaned) <= {"."}:
        return "senza-nome"
    cleaned = cleaned[:180].rstrip(" .")
    if cleaned.split(".", 1)[0].strip().upper() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned


def fit_title(title: str, taken: int, limit: int = MAX_FILENAME) -> str:
    """*title* cut so that it and the *taken* characters around it fit *limit*.

    The stable part of an episode's name — "Series S01E02", the separator, the
    extension — is never what gets cut; only the episode's own caption is, and
    it is dropped altogether when what is left would not say anything.
    """
    room = limit - taken
    if room < 8:
        return ""
    return title[:room].rstrip(" .")


_FALLBACK_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
_user_agent: str | None = None
_user_agent_lock = threading.Lock()


def get_headers() -> str:
    """One User-Agent for the whole process.

    A new random agent on every request makes one IP look like a crowd of
    browsers, which is what anti-bot filters on the source are built to notice.
    The agent is picked once, on first use, and kept.
    """
    global _user_agent
    with _user_agent_lock:
        if _user_agent is None:
            _user_agent = _pick_user_agent()
        return _user_agent


def _pick_user_agent() -> str:
    try:
        software_names = [SoftwareName.CHROME.value]
        operating_systems = [OperatingSystem.WINDOWS.value, OperatingSystem.LINUX.value]
        user_agent_rotator = UserAgent(software_names=software_names, operating_systems=operating_systems, limit=10)
        return user_agent_rotator.get_random_user_agent()
    except (OSError, ValueError, IndexError):
        # Keep API requests usable if the optional rotation database is absent
        # or contains no entries (for example in a damaged portable bundle).
        return _FALLBACK_USER_AGENT
