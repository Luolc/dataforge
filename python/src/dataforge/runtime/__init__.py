"""Running coding agents headless and reading their traces, a module per agent.

`claude` runs Claude Code: one session at a time, its trace, and a runner
that drives it as `dataforge.curation.AgentRunner`. `pi` and `codex` read the
traces of Pi and of Codex CLI, which Harbor's agents of those names write; they
run neither.
"""
