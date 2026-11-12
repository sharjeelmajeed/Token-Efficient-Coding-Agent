import ast
import difflib
import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from .models import ApplyPatch, Grep, ListTree, ReadFile, RepoMap, SyntaxCheck, WriteFile


class PolicyError(Exception):
    pass


class ToolInputError(PolicyError):
    """The model's tool input was wrong and nothing changed, so the model may correct it.

    Never used for security decisions, which stay plain PolicyError and pause the run.
    """


MAX_FILE_BYTES = 128000
# Fixed ceiling for tool output when optimization truncation is off. It is a resource
# safety limit, not an optimization, so it applies in every configuration.
ABSOLUTE_OUTPUT_CHARS = MAX_FILE_BYTES
MAX_READ_LINES = 200
MAX_FILES = 500
SECRET_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*['\"]?[^\s'\"]{12,})"
)
BLOCKED_PARTS = {".git", ".ssh", ".aws", ".azure", ".agent", ".venv", "node_modules"}


def bounded(text: str, limit: int) -> dict:
    truncated = len(text) > limit
    if truncated:
        marker = "\n...[output truncated]...\n"
        available = limit - len(marker)
        text = text[: available * 3 // 4] + marker + text[-(available - available * 3 // 4) :]
    return {"text": text, "truncated": truncated}


class Repository:
    def __init__(
        self,
        root: str,
        marker: str,
        secrets: list[str],
        output_chars: int,
        max_read_lines: int | None = MAX_READ_LINES,
    ):
        try:
            self.root = Path(root).resolve(strict=True)
        except OSError as exc:
            raise PolicyError("Repository is no longer available") from exc
        if not self.root.is_dir():
            raise PolicyError("Repository is no longer a directory")
        self.marker = marker
        self.secrets = [s for s in secrets if s]
        self.output_chars = output_chars
        # None means whole-file reads (targeted reads switched off).
        self.max_read_lines = max_read_lines

    def protected(self, part: str) -> bool:
        name = part.casefold()
        return (
            name in BLOCKED_PARTS
            or name == self.marker.casefold()
            or name.startswith(".env")
            or name in {".npmrc", ".pypirc", "id_rsa", "id_ed25519", "credentials"}
            or name.endswith((".pem", ".key", ".p12"))
        )

    def path(self, relative: str) -> Path:
        if (
            "\x00" in relative
            or "\\" in relative
            or PureWindowsPath(relative).drive
            or PurePosixPath(relative).is_absolute()
        ):
            raise PolicyError("Only repository-relative paths are permitted")
        parts = PurePosixPath(relative).parts
        if not parts or any(p in {"..", "."} for p in parts):
            raise PolicyError("Path traversal is forbidden")
        # Windows ignores trailing dots/spaces and reads alternate streams after a colon.
        if ":" in relative or any(p != p.rstrip(". ") for p in parts):
            raise PolicyError("Ambiguous path names are not permitted")
        if any(self.protected(part) for part in parts):
            raise PolicyError("Protected repository path")
        current = self.root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise PolicyError("Symbolic links are not followed")
        resolved = current.resolve()
        if not resolved.is_relative_to(self.root):
            raise PolicyError("Path leaves the repository")
        # The name on disk also counts: a short name such as SERVER~1.PEM hides the real one.
        if any(self.protected(part) for part in resolved.relative_to(self.root).parts):
            raise PolicyError("Protected repository path")
        if current.exists() and current.is_file() and current.stat().st_nlink != 1:
            raise PolicyError("Hard-linked files are not accessible")
        return current

    def check_secrets(self, content: str):
        if SECRET_PATTERN.search(content) or any(secret in content for secret in self.secrets):
            raise PolicyError("Possible secret detected; content withheld")

    def read(self, relative: str) -> str:
        path = self.path(relative)
        if not path.is_file():
            raise ToolInputError("File does not exist or is not a regular file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise PolicyError("Only ordinary, unlinked text files are accessible")
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise PolicyError("File exceeds the bounded-read limit")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PolicyError("Only UTF-8 text files are supported") from exc
        if "\x00" in text:
            raise PolicyError("Binary files are not supported")
        self.check_secrets(text)
        return text

    def files(self, strict: bool = False):
        count = 0
        for visited, (directory, dirs, files) in enumerate(os.walk(self.root, followlinks=False)):
            if visited >= MAX_FILES:
                if strict:
                    raise PolicyError("Repository directory scan exceeds snapshot limit")
                return
            dirs[:] = sorted(
                d
                for d in dirs
                if d not in BLOCKED_PARTS
                and not d.startswith(".")
                and not (Path(directory) / d).is_symlink()
            )
            for filename in sorted(files):
                relative = (Path(directory) / filename).relative_to(self.root).as_posix()
                try:
                    self.path(relative)
                except PolicyError:
                    continue
                yield relative
                count += 1
                if count >= MAX_FILES:
                    if strict:
                        raise PolicyError("Repository file scan exceeds snapshot limit")
                    return

    def fingerprint(self) -> str:
        """Identify the repository's current contents, for gateway cache partitioning (TO-08).

        Hashes the path, size and modification time of every file outside blocked and hidden
        folders, without reading any file. So binary files count, there is no file-count
        limit, and adding, removing or editing a file changes the result. A change only ever
        costs cache hits. Sandbox execution keeps using the strict snapshot().
        """
        state = hashlib.sha256()
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(
                d
                for d in dirs
                if d not in BLOCKED_PARTS
                and not d.startswith(".")
                and not (Path(directory) / d).is_symlink()
            )
            for name in sorted(files):
                path = Path(directory) / name
                try:
                    info = path.lstat()
                except OSError:
                    continue
                relative = path.relative_to(self.root).as_posix()
                state.update(f"{relative}\0{info.st_size}\0{info.st_mtime_ns}\n".encode())
        return state.hexdigest()

    @staticmethod
    def digest(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def prepare_patch(self, action: ApplyPatch) -> tuple[str, str]:
        content = self.read(action.path)
        if self.digest(content) != action.expected_sha256:
            raise ToolInputError("File changed; read it again and request a new approval")
        if content.count(action.old) != 1:
            raise ToolInputError("Patch must match exactly one occurrence")
        revised = content.replace(action.old, action.new, 1)
        self.check_secrets(revised)
        if revised == content:
            raise ToolInputError("Patch has no effect")
        if len(revised.encode()) > MAX_FILE_BYTES:
            raise ToolInputError("Resulting file exceeds size limit")
        diff = "".join(
            difflib.unified_diff(
                content.splitlines(keepends=True),
                revised.splitlines(keepends=True),
                fromfile=f"a/{action.path}",
                tofile=f"b/{action.path}",
            )
        )
        if len(diff) > 30000:
            raise ToolInputError("Diff too large for a single review; split the change")
        return revised, diff

    def apply(self, action: ApplyPatch) -> dict:
        revised, _ = self.prepare_patch(action)
        path = self.path(action.path)
        mode = path.stat().st_mode & 0o777
        descriptor, temp = tempfile.mkstemp(prefix=".agent-patch-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(revised)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, mode)
            # Detect normal concurrent edits after review and before replacement.
            if self.digest(self.read(action.path)) != action.expected_sha256:
                raise PolicyError("File changed before execution; approval is no longer valid")
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return {"path": action.path, "sha256": self.digest(revised), "applied": True}

    def prepare_write(self, action: WriteFile) -> tuple[str, str]:
        path = self.path(action.path)
        if not path.parent.is_dir():
            raise ToolInputError("Create files inside an existing directory")
        if action.expected_sha256 == "absent":
            if path.exists():
                raise ToolInputError("File exists; read it and provide its current hash")
            current = ""
        else:
            current = self.read(action.path)
            if self.digest(current) != action.expected_sha256:
                raise ToolInputError("File changed; request a new approval")
        self.check_secrets(action.content)
        if action.expected_sha256 != "absent" and current == action.content:
            raise ToolInputError("Write has no effect")
        diff = "".join(
            difflib.unified_diff(
                current.splitlines(keepends=True),
                action.content.splitlines(keepends=True),
                fromfile=f"a/{action.path}",
                tofile=f"b/{action.path}",
            )
        )
        if len(diff) > 30000:
            raise ToolInputError("Diff too large; split the change")
        return action.content, diff or f"Create empty file: {action.path}\n"

    def write(self, action: WriteFile) -> dict:
        content, _ = self.prepare_write(action)
        path = self.path(action.path)
        mode = path.stat().st_mode & 0o777 if action.expected_sha256 != "absent" else 0o644
        descriptor, temp = tempfile.mkstemp(prefix=".agent-write-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, mode)
            self.path(action.path)
            if action.expected_sha256 == "absent":
                try:
                    os.link(temp, path)
                except FileExistsError as exc:
                    raise PolicyError("File was created after review; approval is stale") from exc
            else:
                if self.digest(self.read(action.path)) != action.expected_sha256:
                    raise PolicyError("File changed before execution; approval is stale")
                os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return {"path": action.path, "sha256": self.digest(content), "applied": True}

    def snapshot(self, max_bytes: int = 8000000) -> tuple[dict[str, str], str]:
        files = {}
        size = 0
        for path in self.files(strict=True):
            # Hidden/tool configuration files are excluded from the execution environment.
            if any(p.startswith(".") for p in PurePosixPath(path).parts):
                continue
            try:
                content = self.read(path)
            except PolicyError:
                # A verification snapshot must not silently omit an accessible unsafe file.
                raise PolicyError(
                    f"Cannot safely snapshot {path}; review repository contents"
                ) from None
            files[path] = content
            size += len(content.encode())
            if size > max_bytes or len(files) >= MAX_FILES:
                raise PolicyError("Repository snapshot exceeds configured limits")
        manifest = "\n".join(f"{p}:{self.digest(text)}" for p, text in sorted(files.items()))
        return files, self.digest(manifest)

    def execute_read(self, action) -> dict:
        if isinstance(action, ReadFile):
            start, end = action.start_line, action.end_line
            if self.max_read_lines is not None and (
                end < start or end - start >= self.max_read_lines
            ):
                raise ToolInputError(f"Read at most {self.max_read_lines} lines in ascending order")
            content = self.read(action.path)
            lines = content.splitlines()
            if self.max_read_lines is None:
                start, end = 1, len(lines)  # Whole-file read; size is still capped by read().
            # Plain file text, so models can copy it into exact patches; numbering is separate.
            body = "\n".join(lines[start - 1 : end])
            return {
                "path": action.path,
                "sha256": self.digest(content),
                "total_lines": len(lines),
                "start_line": start,
                "end_line": min(end, len(lines)),
                **bounded(body, self.output_chars),
            }
        if isinstance(action, ListTree):
            paths = [p for p in self.files() if len(PurePosixPath(p).parts) <= action.depth]
            return {**bounded("\n".join(paths), self.output_chars), "scan_limit": MAX_FILES}
        if isinstance(action, Grep):
            matches = []
            for path in self.files():
                try:
                    content = self.read(path)
                except PolicyError:
                    continue
                for i, line in enumerate(content.splitlines(), 1):
                    if action.pattern in line:
                        matches.append(f"{path}:{i}: {line[:500]}")
                        if len(matches) >= 50:
                            return {
                                **bounded("\n".join(matches), self.output_chars),
                                "match_cap": 50,
                            }
            return bounded("\n".join(matches), self.output_chars)
        if isinstance(action, RepoMap):
            outlines = []
            for path in self.files():
                if not path.endswith(".py"):
                    outlines.append(path)
                    continue
                try:
                    tree = ast.parse(self.read(path))
                except (SyntaxError, PolicyError):
                    continue
                symbols = [
                    f"{n.name}:{n.lineno}"
                    for n in ast.walk(tree)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                ]
                outlines.append(f"{path}: {', '.join(symbols)}")
            return bounded("\n".join(outlines), self.output_chars)
        if isinstance(action, SyntaxCheck):
            if not action.path.endswith(".py"):
                raise ToolInputError("Syntax check currently supports Python files only")
            try:
                ast.parse(self.read(action.path))
            except SyntaxError as exc:
                return {
                    "passed": False,
                    "check": "python_syntax",
                    "line": exc.lineno,
                    "message": exc.msg,
                    "error_type": "SyntaxError",
                }
            return {
                "passed": True,
                "check": "python_syntax",
                "note": "Syntax only; this does not execute tests or prove correctness",
            }
        raise PolicyError("Tool is not available in this milestone")
