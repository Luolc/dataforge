"""Which modules of the package hold the runners and the languages' checks."""

from __future__ import annotations

MODULES: dict[str, str] = {
  "pytest": "python",
  "jest": "js",
  "vitest": "js",
  "jasmine": "js",
  "node": "js",
  "go": "gotest",
  "cargo": "cargo",
  "ctest": "cpp",
  "gtest": "cpp",
  "maven": "maven",
  "phpunit": "phpunit",
}
"""The module of each runner, whose SPECS gives the runner's Spec
(core.spec)."""

GUARDS: tuple[str, ...] = ("gotest", "cargo", "cpp", "jsguard", "pyguard")
"""The modules that also hold a language's checks of a fix's files (core's
is_test_file, kept_lines and rewires), whose hooks core gathers."""
