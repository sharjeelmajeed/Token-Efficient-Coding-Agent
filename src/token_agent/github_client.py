import base64
import binascii
import json
import re
from urllib.parse import quote

import httpx

from .repo import PolicyError

REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def repository_name(value: str) -> str:
    if (
        not isinstance(value, str)
        or not REPOSITORY.fullmatch(value)
        or any(p in {".", ".."} for p in value.split("/"))
    ):
        raise PolicyError("Invalid GitHub repository name")
    return value


def commit_sha(value: str) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise PolicyError("GitHub commit/blob identifier is invalid")
    return value


class GitHubClient:
    """Fixed-origin GitHub transport; repository-controlled URLs are never followed."""

    def __init__(self, token: str, client: httpx.AsyncClient):
        if not token or any(c in token for c in "\r\n"):
            raise PolicyError("GITHUB_TOKEN must be supplied through the environment")
        self.token, self.client = token, client

    async def request(self, path: str, method: str = "GET", body=None, missing_ok=False):
        if not path.startswith("/repos/") or ".." in path or "\\" in path:
            raise PolicyError("Invalid GitHub API route")
        try:
            async with self.client.stream(
                method,
                "https://api.github.com" + path,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json=body,
                follow_redirects=False,
            ) as response:
                if missing_ok and response.status_code == 404:
                    return None
                if response.status_code not in {200, 201}:
                    raise PolicyError(
                        f"GitHub returned HTTP {response.status_code}; response withheld"
                    )
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 2000000:
                        raise PolicyError("GitHub response exceeds size limit")
                return json.loads(data)
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            raise PolicyError("GitHub transport or response failed; details withheld") from exc

    async def pull(self, repository: str, number: int) -> dict:
        repository_name(repository)
        if type(number) is not int or number < 1:
            raise PolicyError("Pull request number must be positive")
        data = await self.request(f"/repos/{repository}/pulls/{number}")
        try:
            if (
                data["number"] != number
                or data["base"]["repo"]["full_name"] != repository
                or data["state"] != "open"
                or type(data["base"]["repo"]["private"]) is not bool
            ):
                raise ValueError
            commit_sha(data["head"]["sha"])
            commit_sha(data["base"]["sha"])
            repository_name(data["head"]["repo"]["full_name"])
            if type(data["changed_files"]) is not int or not 0 <= data["changed_files"] <= 30:
                raise PolicyError("Review supports at most 30 changed files; split this PR")
            return data
        except (KeyError, TypeError, ValueError) as exc:
            raise PolicyError("Pull request identity or metadata is invalid") from exc

    async def files(self, repository: str, number: int, expected_count: int) -> list[dict]:
        data = await self.request(
            f"/repos/{repository_name(repository)}/pulls/{number}/files?per_page=100"
        )
        if (
            not isinstance(data, list)
            or len(data) != expected_count
            or any(not isinstance(x, dict) for x in data)
        ):
            raise PolicyError("Changed-file list is incomplete or changed; retry the review")
        return data

    async def marker(self, repository: str, sha: str, marker: str) -> bool:
        result = await self.request(
            f"/repos/{repository_name(repository)}/contents/"
            f"{quote(marker, safe='')}?ref={commit_sha(sha)}",
            missing_ok=True,
        )
        if result is None:
            return False
        if not isinstance(result, dict) or result.get("type") != "file":
            raise PolicyError("Confidential marker metadata is ambiguous")
        return True

    async def blob(self, repository: str, sha: str) -> str:
        data = await self.request(
            f"/repos/{repository_name(repository)}/git/blobs/{commit_sha(sha)}"
        )
        try:
            if (
                data["encoding"] != "base64"
                or type(data["size"]) is not int
                or not 0 <= data["size"] <= 128000
                or not isinstance(data["content"], str)
            ):
                raise ValueError
            content = base64.b64decode("".join(data["content"].split()), validate=True)
            if len(content) != data["size"] or b"\x00" in content:
                raise ValueError
            return content.decode("utf-8")
        except (KeyError, TypeError, ValueError, binascii.Error, UnicodeError) as exc:
            raise PolicyError("PR context must be bounded UTF-8 text") from exc
