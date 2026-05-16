<!-- portfolio-context:start -->
# Portfolio Context

## What This Project Is

Conductor is a native macOS workflow-visualization app for Claude Code sessions. It reads JSONL logs from `~/.claude/projects/`, builds an interactive graph of messages, tool calls, and subagent delegations, and lets the operator inspect each command or spawned agent in a three-panel interface.

## Current State

The README describes a complete product concept and architecture. The recovered context exists so future sessions can resume from that app intent without rediscovering the JSONL/session graph model.

## Stack

| Layer | Technology |
|-------|------------|
| Language | Swift 6.0, strict concurrency |
| UI | SwiftUI |
| Graph | Custom force-directed physics simulation |
| Data | JSONL log parsing via `Codable` |
| Target | macOS 14.0+ |

## How To Run

Build and run. Conductor auto-discovers Claude Code sessions from `~/.claude/projects/` on launch.

## Known Risks

- Session parsing depends on Claude Code's JSONL shape; verify against real `~/.claude/projects/` logs before changing graph assumptions.
- Keep filesystem access local and explicit; this app inspects developer-session logs.
- Swift concurrency and main-actor boundaries matter because session discovery, parsing, graph physics, and selection state cross actor/UI concerns.

## Next Recommended Move

Use this context plus the README and supporting docs to resume the next active task, then promote the repo beyond minimum-viable by capturing a dedicated handoff, roadmap, or discovery artifact.

<!-- portfolio-context:end -->
