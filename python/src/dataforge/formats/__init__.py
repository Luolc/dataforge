"""The formats that installs of dataforge on one machine share, and versions.

A format (core) is a table, a file or a layout that the processes of two
installs share while both run, such as the scheduler of a new release and
the jobs of an old one; each has a version. dataforge declares its own
(builtin), and a project registers its own through the entry point group
dataforge.formats (GROUP). Two installs may run side by side only when they
have the same formats at the same versions: `dataforge formats list --json`
prints an install's, `dataforge formats compare` tells (command). Each
format's fingerprint, the facts its code gives (fingerprint), is kept as a
snapshot that tests check (snapshot), so that any change of it raises its
version.
"""

from dataforge.formats.core import (
  Change,
  changes,
  differences,
  Fingerprint,
  Format,
  GROUP,
  installed,
  RegistryError,
)
from dataforge.formats.fingerprint import json_shape, sqlite, tree, typed_dict
from dataforge.formats.snapshot import against_base, check, SnapshotError, write

__all__ = [
  "GROUP",
  "Change",
  "Fingerprint",
  "Format",
  "RegistryError",
  "SnapshotError",
  "against_base",
  "changes",
  "check",
  "differences",
  "installed",
  "json_shape",
  "sqlite",
  "tree",
  "typed_dict",
  "write",
]
