import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

from gsd_daemon import config as config_module
from gsd_daemon.config import Config
from gsd_daemon.watcher import Watcher


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "daemon.json"

    def test_defaults(self) -> None:
        cfg = Config()
        self.assertEqual(cfg.parents, [])
        self.assertEqual(cfg.excludes, [])
        self.assertEqual(cfg.max_depth, 6)
        self.assertEqual(cfg.poll_seconds, 5)
        self.assertTrue(cfg.notify)
        self.assertTrue(cfg.history)

    def test_load_missing_file_returns_defaults(self) -> None:
        cfg = Config.load(self.path)
        self.assertEqual(cfg, Config())

    def test_round_trip(self) -> None:
        cfg = Config(
            parents=[str(Path(self.tmp.name) / "work")],
            excludes=["~/archive"],
            max_depth=3,
            poll_seconds=9,
            notify=False,
            history=False,
        )
        saved = cfg.save(self.path)
        self.assertEqual(saved, self.path)
        loaded = Config.load(self.path)
        self.assertEqual(loaded.parents, [os.path.abspath(str(Path(self.tmp.name) / "work"))])
        self.assertEqual(loaded.excludes, [os.path.abspath(os.path.expanduser("~/archive"))])
        self.assertEqual(loaded.max_depth, 3)
        self.assertEqual(loaded.poll_seconds, 9)
        self.assertFalse(loaded.notify)
        self.assertFalse(loaded.history)

    def test_env_override(self) -> None:
        Config(parents=[self.tmp.name]).save(self.path)
        with mock.patch.dict(os.environ, {config_module.ENV_CONFIG: str(self.path)}):
            loaded = Config.load()
        self.assertEqual(loaded.parents, [os.path.abspath(self.tmp.name)])

    def test_explicit_path_beats_env(self) -> None:
        other = Path(self.tmp.name) / "other.json"
        Config(parents=["/tmp/from-explicit"]).save(other)
        Config(parents=["/tmp/from-env"]).save(self.path)
        with mock.patch.dict(os.environ, {config_module.ENV_CONFIG: str(self.path)}):
            loaded = Config.load(other)
        self.assertEqual(loaded.parents, [os.path.abspath("/tmp/from-explicit")])

    def test_malformed_json_returns_defaults(self) -> None:
        self.path.write_bytes("{not json".encode("utf-8"))
        self.assertEqual(Config.load(self.path), Config())

    def test_non_dict_json_returns_defaults(self) -> None:
        self.path.write_bytes("[1, 2, 3]".encode("utf-8"))
        self.assertEqual(Config.load(self.path), Config())

    def test_partial_json_keeps_defaults(self) -> None:
        self.path.write_bytes(json.dumps({"parents": [self.tmp.name]}).encode("utf-8"))
        loaded = Config.load(self.path)
        self.assertEqual(loaded.parents, [os.path.abspath(self.tmp.name)])
        self.assertEqual(loaded.max_depth, 6)
        self.assertTrue(loaded.notify)

    def test_save_is_atomic_and_creates_parents(self) -> None:
        nested = Path(self.tmp.name) / "deep" / "dir" / "daemon.json"
        Config().save(nested)
        self.assertTrue(nested.is_file())
        self.assertEqual(json.loads(nested.read_text(encoding="utf-8")), Config().to_dict())

    def test_add_and_remove_parent(self) -> None:
        cfg = Config()
        cfg.add_parent(self.tmp.name)
        cfg.add_parent(self.tmp.name + os.sep)
        self.assertEqual(cfg.parents, [os.path.abspath(self.tmp.name)])
        cfg.remove_parent(self.tmp.name)
        self.assertEqual(cfg.parents, [])


if __name__ == "__main__":
    unittest.main()


class SessionConfigTests(unittest.TestCase):
    def test_defaults_include_host_session_dirs_and_no_prices(self) -> None:
        config = Config()
        self.assertIn("~/.codex/sessions", config.session_dirs)
        self.assertIn("~/.claude/projects", config.session_dirs)
        self.assertEqual(config.prices, {})

    def test_prices_round_trip_and_reject_junk(self) -> None:
        config = Config.from_dict({"prices": {"gpt-6-astra": {"input": 1.25, "cached": "x", "output": 10},
                                              "bad": "nope", 7: {"input": 1}},
                                   "session_dirs": ["/tmp/sessions", 3]})
        self.assertEqual(config.prices, {"gpt-6-astra": {"input": 1.25, "output": 10.0}})
        self.assertEqual(config.session_dirs, ["/tmp/sessions"])
        self.assertEqual(Config.from_dict(config.to_dict()).prices, config.prices)


class ConfigFileReloadTests(unittest.TestCase):
    """Another program may edit session_dirs and prices in daemon.json while the daemon runs."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.path = self.root / "daemon.json"
        env = mock.patch.dict(os.environ, {"GSD_DAEMON_CONFIG": str(self.path)})
        env.start()
        self.addCleanup(env.stop)
        self.project = self.root / "proj"
        self.project.mkdir()
        self.stamp = 1_800_000_000
        self.write({"parents": [], "history": False, "session_dirs": []})
        self.watcher = Watcher(Config.load(self.path), config_path=self.path)

    def write(self, data) -> None:
        self.path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        # Two writes within one timestamp tick would look unchanged; give each its own time.
        self.stamp += 10
        os.utime(self.path, (self.stamp, self.stamp))

    def session_folder(self, name: str) -> Path:
        folder = self.root / name / "projects"
        folder.mkdir(parents=True)
        line = {"type": "assistant", "cwd": str(self.project), "timestamp": "2026-09-01T09:00:05.000Z", "uuid": "a1",
                "message": {"model": "priced", "usage": {"input_tokens": 1_000_000, "output_tokens": 0}}}
        (folder / "session.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
        return folder

    def records(self):
        self.watcher.poll_once()
        self.watcher.sessions.scan([str(self.project)])
        return self.watcher.sessions.records_for(str(self.project))

    def test_session_folders_added_to_the_file_count_on_the_next_scan(self):
        folder = self.session_folder("sandbox")
        self.assertEqual(self.records(), [])
        self.write({"parents": [], "history": False, "session_dirs": [str(folder)]})
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(self.watcher.config.session_dirs, [str(folder)])

    def test_session_folders_removed_from_the_file_stop_counting(self):
        kept, removed = self.session_folder("kept"), self.session_folder("removed")
        self.write({"parents": [], "history": False, "session_dirs": [str(kept), str(removed)]})
        self.assertEqual(len(self.records()), 2)
        self.write({"parents": [], "history": False, "session_dirs": [str(kept)]})
        self.assertEqual(len(self.records()), 1)

    def test_prices_edited_in_the_file_change_the_cost(self):
        self.write({"parents": [], "history": False, "session_dirs": [],
                    "prices": {"priced": {"input": 3.0}}})
        self.watcher.poll_once()
        self.assertEqual(self.watcher.sessions.prices, {"priced": {"input": 3.0}})
        self.assertEqual(self.watcher.config.prices, {"priced": {"input": 3.0}})

    def test_discovery_settings_in_the_file_wait_for_the_settings_api(self):
        self.write({"parents": [str(self.root)], "history": False, "session_dirs": [], "max_depth": 2})
        self.watcher.poll_once()
        self.assertEqual(self.watcher.config.parents, [])
        self.assertEqual(self.watcher.config.max_depth, 6)

    def test_an_unreadable_file_keeps_the_settings_in_memory(self):
        folder = self.session_folder("sandbox")
        self.write({"parents": [], "history": False, "session_dirs": [str(folder)]})
        self.assertEqual(len(self.records()), 1)
        for broken in ("{not json", "[]"):
            self.write(broken)
            self.assertEqual(len(self.records()), 1)
            self.assertEqual(self.watcher.config.session_dirs, [str(folder)])

    def test_a_file_with_the_same_settings_keeps_parsed_records(self):
        folder = self.session_folder("sandbox")
        self.write({"parents": [], "history": False, "session_dirs": [str(folder)]})
        self.records()
        with mock.patch.object(self.watcher.sessions, "set_dirs") as set_dirs:
            self.write({"parents": [], "history": True, "session_dirs": [str(folder)]})
            self.watcher.poll_once()
        set_dirs.assert_not_called()

    def test_a_config_handed_in_without_its_file_is_not_reread(self):
        folder = self.session_folder("sandbox")
        watcher = Watcher(Config(parents=[], history=False, session_dirs=[]))
        self.write({"parents": [], "history": False, "session_dirs": [str(folder)]})
        watcher.poll_once()
        self.assertEqual(watcher.config.session_dirs, [])

