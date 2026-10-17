#!/usr/bin/env python3
# gsd-path project runtime
"""Azure DevOps Services repository identities and pull requests through az.

Git still owns cloning, publication and ancestry checks. This adapter supplies
provider metadata in the same shape as the existing GitHub PR integration.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import quote, unquote

sys.dont_write_bytecode = True


class AzureError(ValueError):
    pass


@dataclass(frozen=True)
class Repository:
    organization: str
    project: str
    name: str

    @property
    def organization_url(self) -> str:
        return "https://dev.azure.com/" + quote(self.organization, safe="")

    @property
    def web_url(self) -> str:
        return (self.organization_url + "/" + quote(self.project, safe="")
                + "/_git/" + quote(self.name, safe=""))

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return ("azure", self.organization.casefold(), self.project.casefold(), self.name.casefold())

    @property
    def key(self) -> str:
        return self.web_url

    @property
    def scope(self) -> tuple[str, ...]:
        return ("--organization", self.organization_url, "--project", self.project, "--detect", "false")


_PART = r"([^/\s?#]+)"
_REMOTES = (
    re.compile(r"(?:git@ssh\.dev\.azure\.com:|ssh://git@ssh\.dev\.azure\.com(?::22)?/)v3/"
               + _PART + "/" + _PART + "/" + _PART, re.IGNORECASE),
    re.compile(r"https://(?:[^/@:\s]+@)?dev\.azure\.com/" + _PART + "/" + _PART
               + r"/_git/" + _PART, re.IGNORECASE),
)


def parse_remote(remote: str) -> Optional[Repository]:
    for pattern in _REMOTES:
        match = pattern.fullmatch(remote)
        if match is None:
            continue
        parts = tuple(unquote(value) for value in match.groups())
        if any(not part or part in {".", ".."} or any(ord(c) < 32 or c in "/\\?#" for c in part)
               or re.search(r"%[0-9a-fA-F]{2}", part) for part in parts):
            return None
        return Repository(*parts)
    return None


def valid_pull_request_url(value: str) -> bool:
    match = re.fullmatch(r"(.+)/pullrequest/([1-9][0-9]*)", value)
    return (value.startswith("https://dev.azure.com/") and match is not None
            and parse_remote(match.group(1)) is not None)


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise AzureError(f"Azure pull request has invalid {label}")
    return value


def _number(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise AzureError("Azure pull request has an invalid id")
    return value


def require_repository(repository: Repository, value: object) -> dict:
    if not isinstance(value, dict):
        raise AzureError("Azure repository response is not an object")
    project = value.get("project")
    remote = value.get("remoteUrl") or value.get("webUrl")
    actual = parse_remote(remote) if isinstance(remote, str) else None
    if (actual is None or actual.identity != repository.identity
            or not isinstance(project, dict)
            or not isinstance(project.get("name"), str)
            or project["name"].casefold() != repository.project.casefold()
            or not isinstance(value.get("name"), str)
            or value["name"].casefold() != repository.name.casefold()):
        raise AzureError("Azure pull request repository is not origin")
    return value


def normalize(repository: Repository, value: object, call: Callable[..., object]) -> dict:
    if not isinstance(value, dict):
        raise AzureError("Azure pull request response is not an object")
    number = _number(value.get("pullRequestId"))
    require_repository(repository, value.get("repository"))
    if value.get("forkSource") is not None:
        raise AzureError("Azure pull request source repository is not origin")
    status = value.get("status")
    if not isinstance(status, str) or status not in {"active", "abandoned", "completed"}:
        raise AzureError("Azure pull request has invalid status")
    source, target = value.get("sourceRefName"), value.get("targetRefName")
    if any(not isinstance(ref, str) or not ref.startswith("refs/heads/")
           or not ref.removeprefix("refs/heads/") for ref in (source, target)):
        raise AzureError("Azure pull request refs are invalid")
    if status == "active":
        # lastMergeSourceCommit may still name an earlier iteration while Azure
        # computes a merge. Read the actual source ref, including just-created PRs.
        refs = call("repos", "ref", "list", *repository.scope, "--repository", repository.name,
                    "--filter", source.removeprefix("refs/"))
        if not isinstance(refs, list):
            raise AzureError("Azure source branch response is not a list")
        exact = [ref for ref in refs if isinstance(ref, dict) and ref.get("name") == source]
        if len(exact) != 1:
            raise AzureError("Azure pull request source branch is missing or ambiguous")
        source_sha = _sha(exact[0].get("objectId"), "source commit")
    else:
        commit = value.get("lastMergeSourceCommit")
        source_sha = _sha(commit.get("commitId") if isinstance(commit, dict) else None, "source commit")
    merged_at, merge = None, None
    if status == "completed":
        merged_at = value.get("closedDate")
        commit = value.get("lastMergeCommit")
        merge = _sha(commit.get("commitId") if isinstance(commit, dict) else None, "merge commit")
        if not isinstance(merged_at, str) or not merged_at or value.get("mergeStatus") != "succeeded":
            raise AzureError("Azure pull request merge metadata is invalid")
    return {"number": number, "state": "open" if status == "active" else "closed",
            "html_url": f"{repository.web_url}/pullrequest/{number}",
            "merged_at": merged_at, "merge_commit_sha": merge, "body": value.get("description"), "title": value.get("title"),
            "base": {"ref": target.removeprefix("refs/heads/")},
            "head": {"ref": source.removeprefix("refs/heads/"), "sha": source_sha,
                     "repo": {"full_name": repository.key}}, "azure": value}


def show(repository: Repository, number: int, call: Callable[..., object]) -> dict:
    result = normalize(repository, call("repos", "pr", "show", "--organization", repository.organization_url,
                                       "--detect", "false", "--id", str(number)), call)
    if result["number"] != number:
        raise AzureError("Azure pull request response names a different id")
    return result


def find(repository: Repository, branch: str, base: str, call: Callable[..., object]) -> Optional[dict]:
    matches, seen = [], set()
    skip = 0
    while True:
        page = call("repos", "pr", "list", *repository.scope, "--repository", repository.name,
                    "--source-branch", branch, "--target-branch", base, "--status", "all",
                    "--top", "100", "--skip", str(skip))
        if not isinstance(page, list):
            raise AzureError("Azure pull request list response is not a list")
        for item in page:
            if not isinstance(item, dict):
                raise AzureError("Azure pull request list contains an invalid entry")
            number = _number(item.get("pullRequestId"))
            if number in seen:
                raise AzureError("Azure pull request pagination repeats an id")
            seen.add(number)
            if (item.get("sourceRefName"), item.get("targetRefName")) == (
                    f"refs/heads/{branch}", f"refs/heads/{base}"):
                require_repository(repository, item.get("repository"))
                matches.append(number)
        if len(page) < 100:
            break
        skip += len(page)
    if len(matches) > 1:
        raise AzureError(f"multiple pull requests target {base} from the bound branch")
    return show(repository, matches[0], call) if matches else None


def create(repository: Repository, branch: str, base: str, title: str, body: str,
           call: Callable[..., object]) -> dict:
    return normalize(repository, call("repos", "pr", "create", *repository.scope,
                     "--repository", repository.name, "--source-branch", branch, "--target-branch", base,
                     "--title", title, "--description", body, "--auto-complete", "false",
                     "--delete-source-branch", "false"), call)


def update_body(repository: Repository, number: int, body: str, call: Callable[..., object]) -> dict:
    return normalize(repository, call("repos", "pr", "update", "--organization", repository.organization_url,
                     "--detect", "false", "--id", str(number), "--description", body), call)


def require_merge_provenance(repository: Repository, number: int, merge: Optional[str],
                             call: Callable[..., object]) -> dict:
    pull = show(repository, number, call)
    raw = pull["azure"]
    options = raw.get("completionOptions")
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise AzureError("Azure pull request completion options are invalid")
    if raw.get("autoCompleteSetBy") is not None or options.get("triggeredByAutoComplete"):
        raise AzureError("pull-request integration cannot use Azure auto-complete")
    if options.get("bypassPolicy"):
        raise AzureError("pull-request integration cannot bypass Azure policies")
    if merge is None:
        if pull["state"] != "open" or pull["merged_at"] is not None:
            raise AzureError("open Azure pull request has invalid merge provenance")
        return pull
    if pull["merged_at"] is None or pull["merge_commit_sha"] != merge:
        raise AzureError("Azure pull-request merge action does not match landing")
    strategy = options.get("mergeStrategy")
    if not (isinstance(strategy, str) and strategy in {"noFastForward", "squash"}) and not (strategy is None and options.get("squashMerge") is False):
        raise AzureError("Azure pull-request integration requires a no-fast-forward merge or squash")
    if strategy == "squash":
        target = raw.get("lastMergeTargetCommit")
        _sha(target.get("commitId") if isinstance(target, dict) else None, "target commit")
    actor = raw.get("closedBy")
    if (not isinstance(actor, dict) or not isinstance(actor.get("id"), str) or not actor["id"]
            or actor.get("isContainer")
            or not isinstance(actor.get("uniqueName"), str) or "@" not in actor["uniqueName"]):
        raise AzureError("Azure pull request must be completed by a user")
    return pull
