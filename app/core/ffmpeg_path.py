import functools
import logging
import os
import shutil

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def get_ffmpeg_exe() -> str:
    """Resolve the ffmpeg executable to invoke.

    Order: FFMPEG_PATH env override -> system PATH (apt/brew install, what the
    Docker image relies on) -> imageio-ffmpeg's bundled static binary. The last
    step exists because Windows has no equivalent of "apt install ffmpeg": users
    routinely have no ffmpeg on PATH at all, which surfaces deep inside a Join
    step as an opaque WinError 2 instead of a clear "install ffmpeg" message.
    """
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path:
        if shutil.which(env_path) or os.path.isfile(env_path):
            return env_path
        logger.warning("FFMPEG_PATH=%s is not a usable executable, ignoring", env_path)

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    try:
        import imageio_ffmpeg
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        logger.info("No system ffmpeg on PATH, using bundled binary: %s", bundled)
        return bundled
    except Exception as e:
        raise RuntimeError(
            "ffmpeg executable not found. Install ffmpeg and add it to PATH, "
            "or set the FFMPEG_PATH environment variable to its full path."
        ) from e


@functools.lru_cache(maxsize=1)
def get_ffprobe_exe() -> str | None:
    """Resolve ffprobe, or None when there is none to resolve.

    Unlike ffmpeg this is optional, and returns None rather than raising:
    imageio-ffmpeg bundles ffmpeg alone, so the Windows fallback that saves the
    download has no ffprobe to offer. Debian's ffmpeg package ships both, which
    covers the Docker image.

    FFPROBE_PATH overrides, then a sibling of the resolved ffmpeg — a manual
    install usually puts the pair in one directory, and finding ffmpeg there
    without looking would miss it — then PATH.
    """
    env_path = os.environ.get("FFPROBE_PATH")
    if env_path and (shutil.which(env_path) or os.path.isfile(env_path)):
        return env_path

    try:
        sibling = os.path.join(os.path.dirname(get_ffmpeg_exe()), "ffprobe")
    except RuntimeError:
        sibling = ""
    for candidate in (sibling, sibling + ".exe"):
        if candidate and os.path.isfile(candidate):
            return candidate

    return shutil.which("ffprobe")


def ffmpeg_file_arg(path: str) -> str:
    r"""Return *path* explicitly marked as a filesystem path for FFmpeg.

    FFmpeg reads everything before the first ':' in a filename as a protocol
    name, so a Windows-style destination — ``N:\Jellyfin\Anime\…`` — is
    taken for a protocol called ``N`` and rejected with "Protocol not found",
    an error naming a protocol nobody asked for. The ``file:`` prefix says
    which protocol it actually is and takes the guess away.

    Applied to every input and output that comes from a configured library
    path. Format detection is unaffected: FFmpeg still guesses the muxer from
    the extension, which the prefix leaves at the end of the string.
    """
    text = str(path)
    return text if text.startswith("file:") else f"file:{text}"


def hidden_process_options() -> dict:
    """Suppress console windows for FFmpeg and FFprobe on Windows."""
    import subprocess
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


# The longest a join or a remux may run. Both are stream copies, which finish
# in minutes even for a long film; an FFmpeg still going after this is stuck on
# its input, and without a limit it held the job, its download slot and the
# destination lock for as long as the process lived.
FFMPEG_TIMEOUT = 2 * 60 * 60

_POLL_INTERVAL = 0.2


def _stop(process) -> None:
    """Terminate, then kill what does not listen."""
    import subprocess
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_command(cmd: list[str], cancel_event=None, timeout: float = FFMPEG_TIMEOUT):
    """Run an FFmpeg argv to completion: ``(returncode, stdout, stderr)``.

    ``subprocess.run`` could be neither cancelled nor timed out, so a cancelled
    job kept its FFmpeg until the process ended by itself. This polls instead,
    the way ``transcode._encode`` does, and stops the process on cancel
    (DownloadCancelledError) or once *timeout* seconds have passed
    (RuntimeError).

    *cmd* is always an argument list and never goes through a shell: titles
    from a third-party site end up in these paths. Output goes to temporary
    files rather than pipes, which nobody drains while this is polling — a
    chatty FFmpeg would fill the pipe and block forever.
    """
    import subprocess
    import tempfile
    import time

    if not isinstance(cmd, (list, tuple)):
        raise TypeError("FFmpeg arguments must be a list, never a shell string")

    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(list(cmd), stdin=subprocess.DEVNULL, stdout=out,
                                   stderr=err, shell=False, **hidden_process_options())
        try:
            while True:
                try:
                    process.wait(timeout=_POLL_INTERVAL)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if cancel_event is not None and cancel_event.is_set():
                    from app.progress import DownloadCancelledError
                    raise DownloadCancelledError("Download annullato dall'utente")
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"FFmpeg interrotto: ancora in esecuzione dopo {int(timeout // 60)} minuti")
        finally:
            _stop(process)
        out.seek(0)
        err.seek(0)
        return process.returncode, out.read(), err.read()


def run_ffmpeg(stream, cancel_event=None, timeout: float = FFMPEG_TIMEOUT):
    import ffmpeg
    returncode, stdout, stderr = run_command(
        ffmpeg.compile(stream, cmd=get_ffmpeg_exe(), overwrite_output=True),
        cancel_event=cancel_event, timeout=timeout,
    )
    if returncode:
        raise ffmpeg.Error("ffmpeg", stdout, stderr)
    return stdout, stderr
