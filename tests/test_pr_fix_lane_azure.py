"""PR fix lane on Azure DevOps (ADR 0006): reviewer votes, PR threads, replies and ship-check."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import azure_repos, check_handoffs, members, parking, pr_fix_lane
import tests.test_member_integration as member_integration
import tests.test_parking as parking_fixtures
import tests.test_pr_fix_lane as github_lane

git = member_integration.git
ORG = "https://dev.azure.com/acme"
AZ_PULL = f"{ORG}/Demo%20Project/_git/web/pullrequest/7"
AUTHOR, REVIEWER, OTHER, GROUP = "id-author", "id-rev", "id-other", "id-group"


def person(identifier, name):
    return {"id": identifier, "uniqueName": f"{name}@example.test", "displayName": name.title()}


def reviewer(identifier, vote, container=False):
    return dict(person(identifier, identifier[3:]), vote=vote, isContainer=container or None)


def note(identifier, author, content, kind="text", deleted=None):
    return {"id": identifier, "parentCommentId": 0 if identifier == 1 else 1, "author": person(author, author[3:]),
            "content": content, "commentType": kind, "isDeleted": deleted}


def thread(identifier, comments, status="active", path=None, line=None):
    context = {"filePath": path, "rightFileStart": {"line": line, "offset": 1}} if path else None
    return {"id": identifier, "status": status, "threadContext": context, "isDeleted": False, "comments": comments}


class FakeAzure:
    """The `az` surface the lane uses: `repos pr show`, `repos ref list` and `devops invoke`."""

    def __init__(self, head, reviewers, threads):
        self.head, self.reviewers, self.threads = head, reviewers, threads
        self.calls, self.bodies, self.counter = [], [], 1000

    def pull(self):
        return {"pullRequestId": 7, "status": "active", "title": "t", "description": "Descrição",
                "sourceRefName": "refs/heads/feature/demo-milestone-001", "targetRefName": "refs/heads/main",
                "forkSource": None, "createdBy": person(AUTHOR, "author"), "reviewers": self.reviewers,
                "lastMergeSourceCommit": {"commitId": self.head},
                "repository": {"name": "web", "project": {"name": "Demo Project"},
                               "remoteUrl": f"{ORG}/Demo%20Project/_git/web"}}

    def __call__(self, *arguments):
        self.calls.append(arguments)
        if arguments[:3] == ("repos", "pr", "show"):
            return self.pull()
        if arguments[:3] == ("repos", "ref", "list"):
            return [{"name": "refs/heads/feature/demo-milestone-001", "objectId": self.head}]
        assert arguments[:2] == ("devops", "invoke"), arguments
        flags = {arguments[index]: arguments[index + 1] for index in range(2, len(arguments) - 1)
                 if arguments[index].startswith("--")}
        route = dict(value.split("=", 1) for value in arguments[arguments.index("--route-parameters") + 1:]
                     if "=" in value and not value.startswith("--"))
        assert (route["project"], route["repositoryId"], route["pullRequestId"]) == ("Demo Project", "web", "7")
        assert flags["--organization"] == ORG and flags["--api-version"] == "7.1"
        body = json.loads(Path(flags["--in-file"]).read_text(encoding="utf-8")) if "--in-file" in flags else None
        self.bodies.append(body)
        method, resource = flags["--http-method"], flags["--resource"]
        if resource == "pullRequestThreads" and method == "GET":
            return {"count": len(self.threads), "value": json.loads(json.dumps(self.threads)),
                    "continuation_token": None}
        if resource == "pullRequestThreadComments" and method == "POST":
            target = next(item for item in self.threads if item["id"] == int(route["threadId"]))
            self.counter += 1
            target["comments"].append(note(self.counter, AUTHOR, body["content"]))
            return {"id": self.counter}
        if resource == "pullRequestThreads" and method == "PATCH":
            next(item for item in self.threads if item["id"] == int(route["threadId"]))["status"] = body["status"]
            return {"id": int(route["threadId"])}
        if resource == "pullRequestThreads" and method == "POST":
            self.counter += 1
            self.threads.append({"id": self.counter, "status": body["status"], "threadContext": None,
                                 "comments": [note(1, AUTHOR, body["comments"][0]["content"])]})
            return {"id": self.counter}
        raise AssertionError(arguments)


def review_threads():
    return [thread(10, [note(1, "id-system", "Policy status has been updated", kind="system")], status=None),
            thread(11, [note(1, REVIEWER, "Teto da ponte abaixo do da API")], path="/apps/web/lib/api.ts", line=20),
            thread(12, [note(1, REVIEWER, "Resumo: nove itens")]),
            thread(13, [note(1, OTHER, "Só uma dúvida")], path="/README.md", line=1),
            thread(14, [note(1, REVIEWER, "Já resolvido")], status="fixed", path="/a.ts", line=2),
            thread(15, [note(1, REVIEWER, "   ")], path="/b.ts", line=3)]


class AzureReviewStateTests(unittest.TestCase):
    def read(self, reviewers, threads):
        return pr_fix_lane.read_forge(AZ_PULL, FakeAzure("a" * 40, reviewers, threads))

    def test_negative_individual_votes_are_the_change_requests(self) -> None:
        read = self.read([reviewer(REVIEWER, -5), reviewer(GROUP, -5, container=True), reviewer(OTHER, 10),
                          reviewer("id-x", -10)], [])
        self.assertEqual(read["provider"], "azure")
        self.assertEqual(sorted(item["id"] for item in pr_fix_lane.change_requests(read)), ["id-rev", "id-x"])
        self.assertEqual(self.read([reviewer(REVIEWER, 0), reviewer(GROUP, -5, container=True)], [])["reviews"][0]
                         ["state"], "NONE")
        self.assertEqual(pr_fix_lane.change_requests(self.read([reviewer(GROUP, -5, container=True)], [])), [])

    def test_items_are_the_requesting_reviewers_open_threads(self) -> None:
        read = self.read([reviewer(REVIEWER, -5)], review_threads())
        items = pr_fix_lane.request_items(read, pr_fix_lane.change_requests(read))
        self.assertEqual([item["id"] for item in items], ["T11", "T12"])
        self.assertEqual((items[0]["kind"], items[0]["location"]), ("inline", "apps/web/lib/api.ts:20"))
        self.assertEqual((items[1]["kind"], items[1]["location"]), ("thread", "none"))
        self.assertEqual(items[0]["link"], f"{AZ_PULL}?discussionId=11&commentId=1")
        self.assertEqual(items[0]["text"], "Teto da ponte abaixo do da API")

    def test_an_answered_thread_waits_until_the_reviewer_writes_again(self) -> None:
        marker = pr_fix_lane.REPLY_MARKER.format(key="1/T11/" + "a" * 40)
        threads = [thread(11, [note(1, REVIEWER, "Teto"), note(2, AUTHOR, f"Deixei para depois.\n\n{marker}")],
                          path="/a.ts", line=1),
                   thread(12, [note(1, REVIEWER, "Escopo"), note(2, AUTHOR, f"Corrigi.\n\n{marker}"),
                               note(3, REVIEWER, "Ainda falta o prompt=login")], path="/b.ts", line=2),
                   thread(13, [note(1, REVIEWER, "Novo"), note(2, AUTHOR, "comentário do autor sem marcador")])]
        read = self.read([reviewer(REVIEWER, -5)], threads)
        items = pr_fix_lane.request_items(read, pr_fix_lane.change_requests(read))
        self.assertEqual([item["id"] for item in items], ["T12", "T13"])
        self.assertEqual(items[0]["text"], "Ainda falta o prompt=login")
        # A thread ruled again in a later round keeps its id but gets a new link (the parking key).
        self.assertEqual(items[0]["link"], f"{AZ_PULL}?discussionId=12&commentId=3")

    def test_a_thread_left_for_later_twice_is_parked_twice(self) -> None:
        marker = pr_fix_lane.REPLY_MARKER.format(key="1/T12/" + "a" * 40)
        first = [thread(12, [note(1, REVIEWER, "Lento")], path="/b.ts", line=2)]
        second = [thread(12, [note(1, REVIEWER, "Lento"), note(2, AUTHOR, f"Depois.\n\n{marker}"),
                              note(3, REVIEWER, "Ainda lento no lote")], path="/b.ts", line=2)]
        with tempfile.TemporaryDirectory() as tmp:
            repo = parking_fixtures.make_repo(tmp)
            for number, threads in ((1, first), (2, second)):
                read = self.read([reviewer(REVIEWER, -5)], threads)
                items = pr_fix_lane.request_items(read, pr_fix_lane.change_requests(read))
                directory = pr_fix_lane.lane_root(repo) / "002-m2" / f"round-{number}"
                directory.mkdir(parents=True)
                parking_fixtures.write_json(directory / "round.json",
                                            {"round": number, "pull_request": AZ_PULL, "member": "web"})
                parking_fixtures.write_json(directory / "snapshot.json", {"items": items})
                parking_fixtures.write_json(directory / "ruling.json", {"verbatim": "Depois.", "items": [
                    {"id": "T12", "class": "follow-up", "note": "later", "risk": "operations"}]})
            self.assertEqual(parking.sync(repo)["imported"], ["K001", "K002"])

    def test_without_a_change_request_any_reviewers_open_remark_is_an_approval_item(self) -> None:
        read = self.read([reviewer(REVIEWER, 10)], review_threads())
        requests, items = pr_fix_lane.approval_items(read, "a" * 40)
        self.assertEqual([item["id"] for item in items], ["T11", "T12", "T13"])
        self.assertEqual(sorted(item["id"] for item in requests), [OTHER, REVIEWER])
        self.assertEqual({item["body"] for item in requests}, {""})

    def test_other_forges_take_the_ordinary_route(self) -> None:
        self.assertEqual(pr_fix_lane.forge_provider(AZ_PULL), "azure")
        self.assertEqual(pr_fix_lane.forge_provider("https://github.com/acme/web/pull/7"), "github")
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "GitHub and Azure DevOps pull requests only"):
            pr_fix_lane.forge_provider("https://gitlab.example/acme/web/-/merge_requests/7")

    def test_a_paginated_thread_list_is_refused(self) -> None:
        repository, number = azure_repos.pull_request_parts(AZ_PULL)
        with self.assertRaisesRegex(azure_repos.AzureError, "paginated"):
            azure_repos.pull_request_threads(repository, number,
                                             lambda *_: {"value": [], "continuation_token": "next"})

    def test_azure_item_ids_pass_the_delta_and_handoff_parsers(self) -> None:
        source = "\n".join(["# PR Review — round 1", "", "## Items", "", "### Item T11 — Teto", "",
                            "- **Class**: fix", "- **Found**: Teto da ponte", "- **Fix direction**: Subir o teto", ""])
        self.assertEqual(check_handoffs._source_repair_fields(".project/review/pr-review-1.md", source, "Item T11"),
                         ("Teto da ponte", "Subir o teto"))
        delta = "\n".join(["## PR fix delta", "", "Eligibility: local", "Risk inventory: complete", "",
                           "### Item T11: closed", "", "- **Evidence**: `apps/web/lib/api.ts:20`", "",
                           "### SC1 — unaffected", "", "- **Reason**: só a ponte", ""])
        self.assertEqual(pr_fix_lane.parse_delta(delta, ["SC1"], [], ["T11"])["items"], {"T11": "closed"})


class AzureLaneEntryTests(github_lane.LaneEntryTests):
    # Crash recovery after the undo does not read the forge; the GitHub suite covers it.
    test_start_resumes_after_a_crash_between_undo_and_handoff = None

    def prepare_archive(self) -> None:
        super().prepare_archive()
        common = Path(git(self.root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        members.write_member_close(common, "001-demo", [{
            "name": "web", "mode": "pull-request", "reviewed_head": self.member_tip, "status": "pending",
            "merge": None, "tag": None, "pull_request": AZ_PULL}])

    def setUp(self) -> None:
        super().setUp()
        self.azure = FakeAzure(self.member_tip, [reviewer(REVIEWER, -5), reviewer(GROUP, -5, container=True)],
                               review_threads())

    def snapshot(self, **options):
        return pr_fix_lane.snapshot(self.root, api=self.azure, **options)

    def test_snapshot_captures_the_baseline_without_touching_the_project(self) -> None:
        before = git(self.root, "status", "--porcelain")
        result = self.snapshot()
        self.assertEqual(git(self.root, "status", "--porcelain"), before)
        self.assertEqual([item["id"] for item in result["items"]], ["T11", "T12"])
        record = pr_fix_lane.active_round(self.root)
        self.assertEqual((record["stage"], record["review_ids"]), ("captured", [REVIEWER]))
        snap = json.loads((Path(result["directory"]) / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual((snap["head"], snap["body"]), (self.member_tip, "Descrição"))
        self.assertEqual(self.snapshot()["directory"], result["directory"])

    def test_entry_refuses_without_the_setting_a_request_or_a_matching_head(self) -> None:
        with mock.patch.object(pr_fix_lane, "setting_enabled", return_value=False):
            with self.assertRaisesRegex(pr_fix_lane.LaneError, "lane is off"):
                self.snapshot()
        self.azure.reviewers = [reviewer(REVIEWER, 5)]
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "no active change request"):
            self.snapshot()
        self.azure.reviewers = [reviewer(REVIEWER, -10)]
        self.azure.head = "f" * 40
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "head differs"):
            self.snapshot()
        self.assertIsNone(pr_fix_lane.active_round(self.root))

    def test_start_undoes_the_archive_writes_the_handoff_and_blocks_ship(self) -> None:
        self.snapshot()
        ruling = self.root.parent / "azure-ruling.json"
        ruling.write_text(json.dumps({"verbatim": "Corrigir T11; T12 fica.", "items": [
            {"id": "T11", "class": "fix", "group": "G1", "title": "Teto da ponte",
             "fix_direction": "Igualar o teto ao da API"},
            {"id": "T12", "class": "follow-up", "note": "Resumo do revisor", "risk": "other"}]}), encoding="utf-8")
        result = pr_fix_lane.start(self.root, ruling)
        self.assertEqual([row["id"] for row in result["fixes"]], ["T11"])
        source = (self.root / ".project/review/pr-review-1.md").read_text(encoding="utf-8")
        self.assertIn("### Item T11 — Teto da ponte", source)
        self.assertIn(f"Pull request: {AZ_PULL}", source)
        findings = (self.root / ".project/review/PATCH-FINDINGS.md").read_text(encoding="utf-8")
        self.assertIn("- **Locator**: Item T11", findings)

    def test_ship_check_blocks_on_a_negative_vote_and_takes_the_reviewer_id_override(self) -> None:
        self.snapshot()
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "captured items that were never ruled"):
            pr_fix_lane.ship_check(self.root, api=self.azure)
        record = pr_fix_lane.active_round(self.root)
        directory = Path(record.pop("directory"))
        pr_fix_lane._save_round(self.root, dict(record, stage="replied", directory=str(directory)))
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "change request is still active"):
            pr_fix_lane.ship_check(self.root, api=self.azure)
        override = self.root.parent / "override.json"
        override.write_text(json.dumps({"head": self.member_tip, "reviews": [REVIEWER], "verbatim": "Shippar."}),
                            encoding="utf-8")
        self.assertEqual(pr_fix_lane.ship_check(self.root, api=self.azure, override=override)["status"],
                         "overridden")

    def test_a_cleared_vote_closes_the_round(self) -> None:
        self.snapshot()
        record = pr_fix_lane.active_round(self.root)
        directory = Path(record.pop("directory"))
        pr_fix_lane._save_round(self.root, dict(record, stage="replied", directory=str(directory)))
        self.azure.reviewers = [reviewer(REVIEWER, 10)]
        self.assertEqual(pr_fix_lane.ship_check(self.root, api=self.azure)["status"], "clear")


class AzurePostThreadsTests(unittest.TestCase):
    head = "e" * 40
    plan = {"pull_request": AZ_PULL, "head": "e" * 40, "round": 1, "language": "pt-BR", "items": [
        {"id": "T11", "kind": "inline", "class": "fix", "note": "Igualei o teto", "link": f"{AZ_PULL}?discussionId=11"},
        {"id": "T12", "kind": "thread", "class": "fix", "note": "Resumo atendido", "link": "none"},
        {"id": "T13", "kind": "inline", "class": "follow-up", "note": "Fica para o M011", "link": "none"}]}

    def fake(self):
        return FakeAzure(self.head, [reviewer(REVIEWER, -5)],
                         [thread(11, [note(1, REVIEWER, "Teto")], path="/a.ts", line=1),
                          thread(12, [note(1, REVIEWER, "Resumo")]),
                          thread(13, [note(1, REVIEWER, "Lento")], path="/b.ts", line=2)])

    def test_each_thread_is_answered_the_fixes_are_resolved_and_one_closed_summary_is_posted(self) -> None:
        fake = self.fake()
        result = pr_fix_lane.post_threads(self.plan, fake)
        self.assertEqual((result["summary"], result["replied"], result["resolved"], result["missing_threads"]),
                         ("posted", ["T11", "T12", "T13"], ["T11", "T12"], []))
        by_id = {item["id"]: item for item in fake.threads}
        self.assertEqual([by_id[key]["status"] for key in (11, 12, 13)], ["fixed", "fixed", "active"])
        self.assertTrue(by_id[11]["comments"][-1]["content"].startswith("Corrigi em `eeeeeeeeeeee`: Igualei o teto"))
        self.assertTrue(by_id[13]["comments"][-1]["content"].startswith("Deixei para depois: Fica para o M011"))
        replies = [body for body in fake.bodies if body and "parentCommentId" in body]
        self.assertEqual({(body["parentCommentId"], body["commentType"]) for body in replies}, {(1, 1)})
        summary = [item for item in fake.threads if item["id"] > 1000]
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["status"], "closed")
        text = summary[0]["comments"][0]["content"]
        self.assertIn("Rodada 1 de correção do PR, publicada em `eeeeeeeeeeee`.", text)
        self.assertIn(f"- [T11]({AZ_PULL}?discussionId=11): corrigido — Igualei o teto", text)
        self.assertIn("- T13: fica para depois — Fica para o M011", text)
        again = pr_fix_lane.post_threads(self.plan, fake)
        self.assertEqual((again["summary"], again["replied"], again["skipped"], again["resolved"]),
                         ("present", [], ["T11", "T12", "T13"], []))
        self.assertEqual(len([item for item in fake.threads if item["id"] > 1000]), 1)

    def test_a_follow_up_answered_by_the_lane_is_not_an_item_of_the_next_round(self) -> None:
        fake = self.fake()
        pr_fix_lane.post_threads(self.plan, fake)
        read = pr_fix_lane.read_forge(AZ_PULL, fake)
        self.assertEqual(pr_fix_lane.request_items(read, pr_fix_lane.change_requests(read)), [])

    def test_replies_wait_for_the_published_head_and_report_a_missing_thread(self) -> None:
        fake = self.fake()
        fake.head = "f" * 40
        with self.assertRaisesRegex(pr_fix_lane.LaneError, "publish it through close-members"):
            pr_fix_lane.post_threads(self.plan, fake)
        self.assertEqual([body for body in fake.bodies if body], [])
        fake = self.fake()
        fake.threads = fake.threads[1:]
        fake.threads[0]["isDeleted"] = True
        result = pr_fix_lane.post_threads(dict(self.plan, language="en"), fake)
        self.assertEqual((result["missing_threads"], result["resolved"]), (["T11", "T12"], []))
        self.assertIn("PR fix round 1, published in", fake.threads[-1]["comments"][0]["content"])


if __name__ == "__main__":
    unittest.main()
