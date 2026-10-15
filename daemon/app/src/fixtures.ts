// Sample /status payload: used by the tests and by the browser mock (dev-mock.ts).
import type { Project, Status } from "./status";

const base = (over: Partial<Project>): Project => ({
  root: "/work/x", project: null, repository: null, project_root: null, worktree_root: null,
  milestone: null, phase: null, status: null, branch: null, integration: null,
  tasks: [], tasks_done: 0, tasks_total: 0, current_wave: null, waves: {}, roadmap_milestones: [],
  next_milestone: null, phase_log: [], vision: null, intent: null, lesson: null, git: null,
  pending_answers: [], handoff: null, workflow: { state: "unverified", label: "Unverified" },
  last_activity_iso: null, reviews: [], criteria: null, ledger: [], answers: [],
  time_in_phase_s: null, usage: null, spend: null, health: "green", attention: [],
  ...over,
});

/** In build, with tasks, waves and matched usage. */
export const building = base({
  root: "/work/gsd-path", repository: "gsd-path", project: "GSD Path", project_root: "/work/gsd-path",
  milestone: "daemon", phase: "build", status: "active", branch: "feature/milestone-004", integration: "direct",
  workflow: { state: "active", label: "In build" }, health: "amber",
  attention: [{ kind: "stale", label: "no activity for 2d", ref: null }],
  last_activity_iso: "2026-09-29T04:00:00+00:00",
  git: { branch: "feature/milestone-004", head: "0e9a3b1c55d2", dirty: true },
  tasks_done: 2, tasks_total: 3, current_wave: 2, waves: { "1": "parsers", "2": "watcher", "3": "tray" },
  tasks: [
    { id: "T001", title: "Parse STATE.md", wave: 1, status: "done", files: ["daemon/gsd_daemon/probe.py"] },
    { id: "T002", title: "Watch folders", wave: 2, status: "done", files: ["daemon/gsd_daemon/watcher.py"] },
    { id: "T003", title: "Tray icon", wave: 3, status: null, files: [] },
  ],
  roadmap_milestones: [
    { number: "M003", slug: "core", status: "shipped", archive: ".project/archive/M003", goal: "Parsers and the status endpoint.",
      integrated: "91be44a0c1", manifest: { shipped: "2026-09-06", tasks_done: 5, tasks_total: 5, waves: 2 } },
    { number: "M004", slug: "daemon", status: "active", archive: null, goal: "Native tray and dashboard for the daemon." },
    { number: "M005", slug: "notify", status: "pending", archive: null, goal: "Desktop notifications.", depends: ["M004"] },
  ],
  phase_log: [
    { phase: "define", date: "2026-09-09" }, { phase: "research", date: "2026-09-09" },
    { phase: "roadmap", date: "2026-09-09" }, { phase: "plan", date: "2026-09-09" }, { phase: "build", date: "2026-09-10" },
  ],
  time_in_phase_s: 183600, intent: "One window for every project.", vision: "See every Path project at a glance.",
  lesson: "Keep the token out of the web view.",
  handoff: { outcome: "Wave 2 landed", next: "Run wave 3" },
  reviews: [{ file: ".project/review/wave-1.md", kind: "wave", verdict: "approve", cycle: 1, depth: "standard", note: "clean" }],
  criteria: [{ id: "SC1", text: "The board lists every project.", verdict: "met" }, { id: "SC2", text: "The tray shows health.", verdict: "pending" }],
  ledger: [{ command: "python -m unittest", commit: "0e9a3b1c55d2", result: "pass", recorded_at: "2026-09-29T03:58:11+00:00" }],
  usage: { tokens_in: 1200, tokens_out: 400, cost: 0.12, models: [], by_phase: [{ phase: "build", tokens: 1600 }], by_task: [{ task: "T001", model: "kimi-k2", tokens: 1600 }] },
  spend: {
    turns: 90, prompts: 31, tokens_in: 6_000_000, tokens_cached: 3_000_000, tokens_out: 412_000, cost: 26.1,
    priced_turns: 90, duration_s: 5400, timed_turns: 90, unpriced: [],
    models: [{ model: "gpt-5.5", host: "codex", turns: 90, tokens: 9_412_000, cost: 26.1 }],
    agents: [{ agent: "coder", models: ["gpt-5.5"], turns: 90, tokens: 9_412_000, cost: 26.1, duration_s: 5400 }],
    milestones: { M003: { turns: 6, tokens: 500_000, cost: 1.5 }, M004: { turns: 84, tokens: 8_912_000, cost: 24.6 } },
    recent: [{ at: "2026-09-29T03:59:40+00:00", agent: "coder", model: "gpt-5.5", tokens_in: 52_000, tokens_cached: 30_000, tokens_out: 900, cost: 0.31, duration_s: 41 }],
  },
});

/** Blocked at ship, no host sessions matched. */
export const blocked = base({
  root: "/work/atlas", repository: "atlas", project_root: "/work/atlas", milestone: "api-v2", phase: "ship",
  status: "blocked", branch: "feature/milestone-002", integration: "pull-request",
  workflow: { state: "blocked", label: "Blocked", reason: "The final review has not passed." }, health: "red",
  attention: [{ kind: "failed", label: ".project/review/FINAL.md — reject", ref: ".project/review/FINAL.md" }],
  last_activity_iso: "2026-10-01T01:00:00+00:00", git: { branch: "feature/milestone-002", head: "c5d8e21aa0", dirty: false },
  roadmap_milestones: [{ number: "M002", slug: "api-v2", status: "active", archive: null, goal: "Second version of the public API." }],
  reviews: [{ file: ".project/review/FINAL.md", kind: "final", verdict: "reject", cycle: 2, depth: "deep", note: "SC3 not met" }],
});

/** Shipped; no usage. */
export const shipped = base({
  root: "/work/done", repository: "done-thing", project_root: "/work/done", milestone: "graph", phase: "ship",
  status: "shipped", branch: "main", workflow: { state: "shipped", label: "Shipped" },
  last_activity_iso: "2026-09-03T04:00:00+00:00", git: { branch: "main", head: "e07a5c1b22", dirty: false },
  roadmap_milestones: [{ number: "M003", slug: "graph", status: "shipped", archive: ".project/archive/M003", goal: "Dependency graph view.",
    manifest: { shipped: "2026-09-03", tasks_done: 4, tasks_total: 4 } }],
});

/** In research, waiting for an owner answer. */
export const asking = base({
  root: "/work/notes", repository: "field-notes", project_root: "/work/notes", milestone: "bootstrap", phase: "research",
  status: "active", branch: "feature/milestone-001", workflow: { state: "active", label: "In research" }, health: "amber",
  attention: [{ kind: "question", label: "Which search index do we ship?", ref: "A001" }],
  last_activity_iso: "2026-10-01T03:00:00+00:00",
  answers: [{ id: "A001", question: "Which search index do we ship?", owner: "owner", status: "pending" }],
  pending_answers: [{ answer: "A001", owner: "owner", status: "pending" }],
});

export const status: Status = {
  generated_at: "2026-10-01T04:00:21+00:00",
  projects: [building, blocked, shipped, asking],
  daemon: { poll_seconds: 5 },
  plugin: { latest: "1.4.0", update_available: true },
};
