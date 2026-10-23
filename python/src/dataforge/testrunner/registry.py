"""Which module of the package holds each runner's adapter and data."""

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
