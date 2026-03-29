# Conductor

[![Swift](https://img.shields.io/badge/Swift-f05138?style=flat-square&logo=swift)](#) [![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](#)

> See every agent, tool call, and delegation your AI workflow made — rendered as a live graph

Conductor is a native macOS app that visualizes Claude Code agentic workflows as interactive node graphs. It parses real JSONL session logs from `~/.claude/projects/`, builds a force-directed delegation tree, and lets you inspect every tool call, subagent spawn, and command in a three-panel interface.

## Features

- **Force-directed graph** — physics-simulated node layout showing session messages, tool calls, and subagent delegations as connected nodes
- **Real JSONL log parsing** — reads Claude Code's native session format; auto-discovers all projects and sessions
- **Subagent delegation tree** — visualizes multi-agent hierarchies so you can see which agent spawned which
- **Session management** — sidebar groups sessions by project with bookmark support and live refresh (⌘R)
- **Detail panel** — click any node to inspect its full content, tool inputs/outputs, and timestamps
- **Command history** — timeline of all tool calls and executed commands for a session

## Quick Start

### Prerequisites
- Xcode 16+
- macOS 14.0+
- XcodeGen: `brew install xcodegen`

### Installation
```bash
git clone https://github.com/saagpatel/Conductor
cd Conductor
xcodegen generate
open Conductor.xcodeproj
```

### Usage
Build and run. Conductor auto-discovers Claude Code sessions from `~/.claude/projects/` on launch.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Swift 6.0, strict concurrency |
| UI | SwiftUI |
| Graph | Custom force-directed physics simulation |
| Data | JSONL log parsing via `Codable` |
| Target | macOS 14.0+ |

## Architecture

Sessions are loaded from `~/.claude/projects/` by a `SessionDiscovery` actor that watches the directory for changes. Each JSONL file is parsed into a typed message graph. The force-directed layout runs on a `@MainActor`-isolated `PhysicsEngine` that applies spring forces on edges and repulsion between nodes, settling in under a second for typical session sizes. Node selection publishes to a `SelectionStore` that both the detail panel and the graph view observe.

## License

MIT