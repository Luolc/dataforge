"""A machine declared in YAML: the states of one attempt, read into a Machine.

A dataset keeps the declaration in its own repo. It declares the states, their
transitions, caps and what each runs; what the declaration cannot hold (the
environments and runners, a state written in Python) the dataset registers in
a table of bindings, by name. Loading substitutes two kinds of variables and no
other: "${{ inputs.<name> }}", the inputs the caller gives, and "${<name>}",
the declaration's vars. The engine's own variables (${state}, ${visit},
${round}, ${feedback}) and an outcome's are left for the engine to render.

The schema, version 1:

  dataforge: 1
  name: <string>
  start: <state>
  inputs: {<name>: string | integer | number | boolean | path}
  vars: {<name>: <string>}            # each may use the inputs and earlier vars
  max_cost_usd: <number>
  environments:
    <name>:                           # a Docker, its fields as data
      image: <string>
      mounts: [{source: <path> | <hook>, target: <string>,
                read_only: <boolean>}]
      outputs: <mount>
      cpus, memory_gb, memory_swap_gb: <number>
      cpu_shares, pids: <integer>
      network: <boolean>
      labels: {<name>: <string>}
      security, cap_drop: [<string>]
      user, workdir: <string>
      env: {binding: <name>}          # a mapping the dataset registers,
                                      # set as --env K=V on docker's
                                      # command line: no credentials
      pass_env: [<variable>]          # passed from docker's own environment
      pre_start, post_start, cli: <hook>
  runners:
    <name>:
      uses: claude-code | harbor | <binding>   # <binding>: a factory
      with: {...}                     # the factory's arguments, or:
        # claude-code: model, effort, max_budget_usd, files ({numbered: {dir,
        #   prefix}} | <hook>), command ({binding: <name>}, a list of
        #   strings), thinking_display, timeout, env, pass_env, tools, add_dirs,
        #   continue_session, min_fix_usd, serve, spent
        # harbor: harbor, cpus, cpu_shares, pids, sidecar {cpus, memory_mb},
        #   timeout, mounts, env, pass_env, compose_overlay [<path> | <hook>],
        #   setup, docker; not verifier, environment or tmpfs_mb, which
        #   only Python sets
  states:
    <name>:
      uses: agent | script | judge | trial | pass-at-k | trials | rubric
            | <binding>               # a State subclass the dataset registers
      with: {...}                     # the state's own arguments;
                                      # agent, pass-at-k and trials take
                                      # judge: {argv, <a script's fields>,
                                      # outcomes}, for agent run after an ok
      env: <environment> | <binding>  # agent, script, rubric
      runner: <runner> | <binding>    # agent and rubric: an AgentRunner;
                                      # trial, pass-at-k, trials: TrialRunner
      max_visits: <integer>
      on_exhausted: <string>
      max_cost_usd: <number>
      rename: {<outcome>: <new name>}
      feedback: <template>
      transitions:
        <outcome>: {goto: <state>, report: <boolean>, mark: <string>,
                    end_on_last_visit: <boolean>}
                 | {end: <status>,
                    keep_mark: <string>}  # keep the last visit marked so
  deliverables:                       # what an attempt shows, in this order
    <name>:
      kind: files | trials | report
      title: <string>
      source: {state: <state>, output: <string>,   # no output for trials
               visit: kept | last | <integer>}     # kept by default
                                      # state may list states: the last of
                                      # their visits that has the output,
                                      # with an output and visit kept or last
      table: <source>                 # report only
      value: {field: [<string>], output: <string>} # trials only
      match: <glob>                   # trials only: list only the trials
                                      # whose output name matches it

In the fields the engine renders as string.Template (feedback, prompt and
report_prompt, a prompt file and its vars), an input's value has each $
doubled, so that the engine's output holds it as given; vars are template
text, inserted as written, and feedback must be a valid template. In other
fields "$$" is passed on as it is.

A hook, what can only be Python, is {binding: <name>}, the object the
dataset registers, or {uses: <name>, with: {...}}, what the factory it
registers returns when loading calls it with the with's data. Environment
variables are only named in a declaration, their values taken from the
environment (pass_env) or a binding (env), and a runner's command is a
binding; a hook's with and labels are plain data, which holds no credential.
Relative paths
(prompt and rubric files, validators, mount sources, {path: ...} inputs) are
against the base directory.
"""

from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
import dataclasses
from dataclasses import dataclass
import os
from pathlib import Path, PurePath
import re
import string
from typing import cast, TYPE_CHECKING

import yaml

from dataforge.curation.actions import (
  Actions,
  AgentSpec,
  Docker,
  Env,
  Judge,
  Limits,
  Mount,
  ScriptSpec,
  TrialAgent,
)
from dataforge.curation.deliverables import check as check_deliverables
from dataforge.curation.deliverables import (
  Deliverable,
  Kind,
  Source,
  Value,
)
from dataforge.curation.engine import (
  Attempt,
  End,
  Goto,
  Guard,
  Input,
  Machine,
  State,
)
from dataforge.curation.harbor import HarborRunner, Sidecar
from dataforge.curation.rubric import Review
from dataforge.curation.states import (
  AgentState,
  JudgeState,
  PassAtKState,
  RubricState,
  ScriptState,
  TrialsState,
  TrialState,
)

if TYPE_CHECKING:
  from dataforge.runtime import claude


SCHEMA_VERSION = 1
"""The version of the schema this module reads."""

ENGINE_VARS = frozenset({"state", "visit", "round", "feedback"})
"""The variables the engine renders, which no input or var may be named."""

INPUT_TYPES = ("string", "integer", "number", "boolean", "path")
"""The types an input may be declared with."""

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STATE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_INPUT = re.compile(r"\$\{\{\s*inputs\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_SUBST = re.compile(r"\$\$|\$\{\{(.*?)\}\}|\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class SpecError(ValueError):
  """A declaration that does not load: path says where, message what.

  path is dotted, as "states.author.with.prompt", or "" for the whole file.
  """

  def __init__(self, path: str, message: str) -> None:
    """Make the error.

    Args:
      path: Where, as "states.author.with.prompt"; "" for the whole file.
      message: What is wrong.
    """
    super().__init__(f"{path}: {message}" if path else message)
    self.path: str = path
    self.message: str = message


def load(
  source: str | Path,
  *,
  inputs: Mapping[str, object],
  actions: Actions,
  bindings: Mapping[str, object] | None = None,
  base_dir: Path | None = None,
  guard: Guard | None = None,
  on_step: Callable[[Attempt], None] | None = None,
  stack: ExitStack | None = None,
  spent_usd: Callable[[Attempt], float] | None = None,
) -> Machine:
  """Read a declaration into a machine, checking it strictly.

  Nothing runs: the states, environments and runners are only made, a rubric
  state reads its rubric files, and each hook's factory is called. Each
  Docker environment is entered into stack, so that closing it, when the
  attempt ends, removes the environment's container.

  Args:
    source: The declaration's YAML text, or a Path to its file.
    inputs: The value of each declared input, by name.
    actions: What runs the states' actions; a state with a runner gets a copy
      bound to it.
    bindings: The objects the declaration names, by name: State subclasses
      for uses, and the environments and runners.
    base_dir: The root of relative paths; None for the file's directory, or
      the current directory for text.
    guard: The machine's guard, if any.
    on_step: The machine's on_step, if any.
    stack: Where the Docker environments are entered; needed when the
      declaration has any.
    spent_usd: The machine's measure of what an attempt has spent, if not
      what its actions report (Machine).

  Returns:
    The machine, its start state first.

  Raises:
    SpecError: When the declaration is not valid YAML, does not follow the
      schema, or does not fit the inputs and bindings.
  """
  if isinstance(source, Path):
    text = source.read_text()
    root = source.parent if base_dir is None else base_dir
  else:
    text = source
    root = Path.cwd() if base_dir is None else base_dir
  try:
    _check_keys(_compose(text), set())
    data = cast(object, yaml.safe_load(text))
  except yaml.YAMLError as error:
    raise SpecError("", f"not valid YAML: {error}") from error
  loader = _Loader(dict(bindings or {}), actions, root)
  machine = loader.machine(data, inputs, guard, on_step, spent_usd)
  if loader.environments and stack is None:
    raise SpecError("environments", "load needs a stack to enter them in")
  for docker in loader.environments.values():
    _ = cast(ExitStack, stack).enter_context(docker)
  return machine


_MERGE = "tag:yaml.org,2002:merge"


def _compose(text: str) -> yaml.Node | None:
  return yaml.SafeLoader(text).get_single_node()


def _check_keys(node: yaml.Node | None, seen_nodes: set[int]) -> None:
  # yaml.safe_load keeps the last of a repeated key; a declaration may not
  # repeat one, as two states or two transitions of one name.
  if node is None or id(node) in seen_nodes:
    return
  seen_nodes.add(id(node))
  if isinstance(node, yaml.SequenceNode):
    for item in cast(list[yaml.Node], node.value):
      _check_keys(item, seen_nodes)
  if not isinstance(node, yaml.MappingNode):
    return
  keys: set[str] = set()
  for key, value in cast(list[tuple[yaml.Node, yaml.Node]], node.value):
    if isinstance(key, yaml.ScalarNode) and key.tag != _MERGE:
      text = cast(str, key.value)
      if text in keys:
        line = cast(int, key.start_mark.line) + 1
        raise SpecError("", f"line {line}: key {text!r} repeated")
      keys.add(text)
    _check_keys(value, seen_nodes)


@dataclass(frozen=True)
class _Kind:
  """A shipped state as uses names it.

  Attributes:
    cls: The state's class.
    build: Its arguments from with.
    env: Whether it takes env.
    runner: "agent" or "trial" for the runner it takes, if any.
  """

  cls: type[State]
  build: Callable[[_Loader, Mapping[str, object], str], dict[str, object]]
  env: bool = False
  runner: str | None = None


class _Loader:
  def __init__(
    self, bindings: dict[str, object], actions: Actions, base_dir: Path
  ) -> None:
    self.bindings: dict[str, object] = bindings
    self.actions: Actions = actions
    self.base_dir: Path = base_dir
    self.inputs: dict[str, tuple[str, object]] = {}
    self.vars: dict[str, str] = {}
    self.environments: dict[str, Docker] = {}
    self.runners: dict[str, object] = {}

  def machine(
    self,
    data: object,
    inputs: Mapping[str, object],
    guard: Guard | None,
    on_step: Callable[[Attempt], None] | None,
    spent_usd: Callable[[Attempt], float] | None,
  ) -> Machine:
    top = _fields(
      data,
      "",
      required={"dataforge", "name", "start", "states"},
      optional={
        "inputs",
        "vars",
        "max_cost_usd",
        "environments",
        "runners",
        "deliverables",
      },
    )
    version = top["dataforge"]
    if type(version) is not int or version != SCHEMA_VERSION:
      raise SpecError("dataforge", "the schema version must be 1")
    self.inputs = _inputs(top.get("inputs", {}), inputs)
    _ = self.string(top["name"], "name")
    self.vars = {}
    declared = _mapping(top.get("vars", {}), "vars")
    for name, value in declared.items():
      at = f"vars.{name}"
      _check_name(name, at)
      _ = self.string(value, at)
      text = cast(str, value)
      for found in _SUBST.finditer(text):
        ref = found.group(2)
        if ref in declared and ref not in self.vars:
          raise SpecError(at, f"${{{ref}}} names a var declared after it")
      self.vars[name] = text
    cap = None
    if "max_cost_usd" in top:
      cap = self.number(top["max_cost_usd"], "max_cost_usd", minimum=0)
    for name, spec in _mapping(
      top.get("environments", {}), "environments"
    ).items():
      self.environments[name] = self.docker(
        spec, self.declared(name, "environments")
      )
    for name, spec in _mapping(top.get("runners", {}), "runners").items():
      self.runners[name] = self.runner(spec, self.declared(name, "runners"))
    specs = _mapping(top["states"], "states")
    if not specs:
      raise SpecError("states", "no states")
    start = self.string(top["start"], "start")
    if start not in specs:
      raise SpecError("start", f"no state named {start!r}")
    states = {
      name: self.state(name, spec, f"states.{name}")
      for name, spec in specs.items()
    }
    _check_graph(states, start)
    ordered = [states[start], *(s for n, s in states.items() if n != start)]
    deliverables = self.deliverables(top.get("deliverables", {}), set(states))
    return Machine(
      ordered,
      guard=guard,
      on_step=on_step,
      max_cost_usd=cap,
      deliverables=deliverables,
      spent_usd=spent_usd,
    )

  def deliverables(
    self, value: object, states: set[str]
  ) -> dict[str, Deliverable]:
    found: dict[str, Deliverable] = {}
    for name, spec in _mapping(value, "deliverables").items():
      at = f"deliverables.{name}"
      fields = _fields(
        spec,
        at,
        required={"kind", "title", "source"},
        optional={"table", "value", "match"},
      )
      table, data = fields.get("table"), fields.get("value")
      item = Deliverable(
        cast(Kind, self.string(fields["kind"], f"{at}.kind")),
        self.string(fields["title"], f"{at}.title"),
        self.source(fields["source"], f"{at}.source"),
        table=None if table is None else self.source(table, f"{at}.table"),
        value=None if data is None else self.value(data, f"{at}.value"),
        match=self.optional_string(fields.get("match"), f"{at}.match"),
      )
      try:
        check_deliverables({name: item}, states)
      except ValueError as error:
        why = str(error).removeprefix(f"deliverable {name}: ")
        raise SpecError(at, why) from None
      found[name] = item
    return found

  def source(self, value: object, at: str) -> Source:
    fields = _fields(
      value, at, required={"state"}, optional={"output", "visit"}
    )
    visit = fields.get("visit", "kept")
    if not (visit in ("kept", "last") or type(visit) is int):
      raise SpecError(
        f"{at}.visit", _expected('"kept", "last" or an integer', visit)
      )
    state, where = fields["state"], f"{at}.state"
    return Source(
      self.strings(cast(object, state), where)
      if isinstance(state, list)
      else self.string(state, where),
      self.optional_string(fields.get("output"), f"{at}.output"),
      visit,
    )

  def value(self, value: object, at: str) -> Value:
    fields = _fields(value, at, required={"field"}, optional={"output"})
    return Value(
      self.strings(fields["field"], f"{at}.field"),
      self.optional_string(fields.get("output"), f"{at}.output"),
    )

  def state(self, name: str, spec: object, at: str) -> State:
    if not _STATE_NAME.fullmatch(name):
      raise SpecError(at, "a state's name is letters, digits, _ and -")
    fields = _fields(
      spec,
      at,
      required={"uses", "transitions"},
      optional={
        "with",
        "env",
        "runner",
        "max_visits",
        "on_exhausted",
        "max_cost_usd",
        "rename",
        "feedback",
      },
    )
    uses = self.string(fields["uses"], f"{at}.uses")
    common: dict[str, object] = {
      "name": name,
      "transitions": self.transitions(fields["transitions"], at),
      "max_visits": self.integer(
        fields.get("max_visits", 1), f"{at}.max_visits", minimum=1
      ),
      "on_exhausted": self.string(
        fields.get("on_exhausted", "failed"), f"{at}.on_exhausted"
      ),
      "feedback": self.feedback(fields.get("feedback"), f"{at}.feedback"),
      "max_cost_usd": self.number(
        fields.get("max_cost_usd", 0.0), f"{at}.max_cost_usd", minimum=0
      ),
    }
    with_ = _mapping(fields.get("with", {}), f"{at}.with")
    cls, kind = self.uses(uses, f"{at}.uses")
    if kind is None:
      for key in ("rename", "env", "runner"):
        if key in fields:
          raise SpecError(f"{at}.{key}", f"not taken by uses: {uses}")
      args = {k: self.data(v, f"{at}.with.{k}") for k, v in with_.items()}
      return self.make(cls, {**common, **args}, f"{at}.with")
    args = {
      **common,
      **kind.build(self, with_, f"{at}.with"),
      "rename": self.renames(fields.get("rename", {}), f"{at}.rename"),
      "actions": self.actions,
    }
    if "env" in fields:
      if not kind.env:
        raise SpecError(f"{at}.env", f"not taken by uses: {uses}")
      env = self.named(fields["env"], f"{at}.env", "env")
      args["env"] = cast(Env, env)
    if "runner" in fields:
      if kind.runner is None:
        raise SpecError(f"{at}.runner", f"not taken by uses: {uses}")
      runner = self.named(fields["runner"], f"{at}.runner", kind.runner)
      field = f"{kind.runner}_runner"
      args["actions"] = dataclasses.replace(self.actions, **{field: runner})
    return self.make(cls, args, at)

  def uses(self, uses: str, at: str) -> tuple[type[State], _Kind | None]:
    if uses in _KINDS:
      bound = self.bindings.get(uses)
      if isinstance(bound, type) and issubclass(bound, State):
        raise SpecError(at, f"the binding {uses!r} has a shipped state's name")
      return _KINDS[uses].cls, _KINDS[uses]
    if uses not in self.bindings:
      known = ", ".join(_KINDS)
      raise SpecError(at, f"{uses!r} is neither one of {known} nor a binding")
    cls = self.bindings[uses]
    if not isinstance(cls, type) or not issubclass(cls, State):
      raise SpecError(at, f"the binding {uses!r} is not a State subclass")
    for kind in _KINDS.values():
      if issubclass(cls, kind.cls):
        return cls, kind
    return cls, None

  def make(self, cls: type[State], args: dict[str, object], at: str) -> State:
    name = args.pop("name")
    try:
      return cls(name, **args)  # pyright: ignore[reportArgumentType]
    except (TypeError, ValueError, OSError) as error:
      raise SpecError(at, str(error)) from error

  def transitions(self, value: object, at: str) -> dict[str, Goto | End]:
    at = f"{at}.transitions"
    found: dict[str, Goto | End] = {}
    for outcome, spec in _mapping(value, at).items():
      found[outcome] = self.transition(spec, f"{at}.{outcome}")
    return found

  def transition(self, value: object, at: str) -> Goto | End:
    fields = _mapping(value, at)
    if ("goto" in fields) == ("end" in fields):
      raise SpecError(at, "needs one of goto and end")
    if "end" in fields:
      for key in fields:
        if key in {"report", "mark", "end_on_last_visit"}:
          raise SpecError(f"{at}.{key}", "only a goto takes it")
      _ = _fields(fields, at, required={"end"}, optional={"keep_mark"})
      return End(
        self.string(fields["end"], f"{at}.end"),
        self.optional_string(fields.get("keep_mark"), f"{at}.keep_mark"),
      )
    if "keep_mark" in fields:
      raise SpecError(f"{at}.keep_mark", "only an end takes it")
    fields = _fields(
      fields,
      at,
      required={"goto"},
      optional={"report", "mark", "end_on_last_visit"},
    )
    return Goto(
      self.string(fields["goto"], f"{at}.goto"),
      report=self.boolean(fields.get("report", True), f"{at}.report"),
      mark=self.optional_string(fields.get("mark"), f"{at}.mark"),
      end_on_last_visit=self.boolean(
        fields.get("end_on_last_visit", False), f"{at}.end_on_last_visit"
      ),
    )

  def renames(self, value: object, at: str) -> dict[str, str]:
    return {
      name: self.string(new, f"{at}.{name}")
      for name, new in _mapping(value, at).items()
    }

  def named(self, value: object, at: str, protocol: str) -> object:
    name = self.string(value, at)
    declared = self.environments if protocol == "env" else self.runners
    found = declared[name] if name in declared else self.bound(name, at)
    what, methods = _PROTOCOLS[protocol]
    if not all(callable(getattr(found, m, None)) for m in methods):
      raise SpecError(at, f"{name!r} is not {what}")
    return found

  def bound(self, name: str, at: str) -> object:
    if name not in self.bindings:
      raise SpecError(at, f"no binding named {name!r}")
    return self.bindings[name]

  def declared(self, name: str, where: str) -> str:
    at = f"{where}.{name}"
    if not _STATE_NAME.fullmatch(name):
      raise SpecError(at, "a name is letters, digits, _ and -")
    if name in self.bindings:
      raise SpecError(at, "a binding has this name too")
    return at

  def hook(self, value: object, at: str) -> object:
    fields = _mapping(value, at)
    if "binding" in fields:
      fields = _fields(fields, at, required={"binding"}, optional=set())
      return self.bound(self.string(fields["binding"], f"{at}.binding"), at)
    fields = _fields(fields, at, required={"uses"}, optional={"with"})
    name = self.string(fields["uses"], f"{at}.uses")
    factory = self.bound(name, f"{at}.uses")
    if not callable(factory):
      raise SpecError(f"{at}.uses", f"the binding {name!r} is not callable")
    args = _mapping(
      self.data(fields.get("with", {}), f"{at}.with"), f"{at}.with"
    )
    try:
      return factory(**args)
    except (TypeError, ValueError) as error:
      raise SpecError(f"{at}.with", str(error)) from error

  def callable_hook(self, value: object, at: str) -> Callable[..., object]:
    found = self.hook(value, at)
    if not callable(found):
      raise SpecError(at, "the hook is not callable")
    return found

  def path(self, value: object, at: str) -> Path:
    if not _is_mapping(value):
      return self.base_dir / self.string(value, at)
    found = self.hook(value, at)
    if not isinstance(found, str | PurePath):
      raise SpecError(at, "the hook gives no path")
    return self.base_dir / found

  def mount(self, value: object, at: str) -> Mount:
    fields = _fields(
      value, at, required={"source", "target"}, optional={"read_only"}
    )
    return Mount(
      self.path(fields["source"], f"{at}.source"),
      self.string(fields["target"], f"{at}.target"),
      self.boolean(fields.get("read_only", True), f"{at}.read_only"),
    )

  def mounts(self, value: object, at: str) -> tuple[Mount, ...]:
    return tuple(
      self.mount(v, f"{at}[{i}]") for i, v in enumerate(_list(value, at))
    )

  def env_vars(self, fields: Mapping[str, object], at: str) -> dict[str, str]:
    # A variable's value comes from this process's environment (pass_env) or
    # a mapping the dataset registers (env), never from the declaration.
    found: dict[str, str] = {}
    for name in self.names(fields.get("pass_env", []), f"{at}.pass_env"):
      if name in os.environ:
        found[name] = os.environ[name]
    if "env" in fields:
      found.update(self.env_binding(fields["env"], f"{at}.env"))
    return found

  def env_binding(self, value: object, at: str) -> dict[str, str]:
    fields = _fields(value, at, required={"binding"}, optional=set())
    name = self.string(fields["binding"], f"{at}.binding")
    env = self.bound(name, f"{at}.binding")
    if not _is_mapping(env) or not all(
      isinstance(k, str) and isinstance(v, str)
      for k, v in cast(Mapping[object, object], env).items()
    ):
      raise SpecError(at, f"the binding {name!r} is not a mapping of strings")
    return dict(cast(Mapping[str, str], env))

  def command(self, value: object, at: str) -> tuple[str, ...]:
    # A command may set variables (env NAME=value claude), so the declaration
    # names a command the dataset registers instead of holding one.
    fields = _fields(value, at, required={"binding"}, optional=set())
    name = self.string(fields["binding"], f"{at}.binding")
    command = self.bound(name, f"{at}.binding")
    if not isinstance(command, list | tuple) or not all(
      isinstance(item, str) for item in cast(Sequence[object], command)
    ):
      raise SpecError(at, f"the binding {name!r} is not a list of strings")
    return tuple(cast(Sequence[str], command))

  def names(self, value: object, at: str) -> tuple[str, ...]:
    found = self.strings(value, at)
    for i, name in enumerate(found):
      if not _NAME.fullmatch(name):
        raise SpecError(f"{at}[{i}]", "not the name of a variable")
    return found

  def docker(self, value: object, at: str) -> Docker:
    fields = _fields(
      value, at, required={"image"}, optional={*_DOCKER, "env", "pass_env"}
    )
    args = _read(self, fields, _DOCKER, at)
    args["image"] = self.string(fields["image"], f"{at}.image")
    if "env" in fields:
      args["env"] = self.env_binding(fields["env"], f"{at}.env")
    if "pass_env" in fields:
      args["pass_env"] = self.names(fields["pass_env"], f"{at}.pass_env")
    try:
      return Docker(**args)  # pyright: ignore[reportArgumentType]
    except (TypeError, ValueError) as error:
      raise SpecError(at, str(error)) from error

  def runner(self, value: object, at: str) -> object:
    fields = _fields(value, at, required={"uses"}, optional={"with"})
    uses = self.string(fields["uses"], f"{at}.uses")
    with_ = _mapping(fields.get("with", {}), f"{at}.with")
    if uses in {"claude-code", "harbor"} and uses in self.bindings:
      raise SpecError(f"{at}.uses", f"the binding {uses!r} has a runner's name")
    if uses == "claude-code":
      return _claude_code(self, with_, f"{at}.with")
    if uses == "harbor":
      return _harbor(self, with_, f"{at}.with")
    return self.hook(fields, at)

  def substitute(self, text: str, at: str, template: bool) -> str:
    # In a template, a text the engine renders again, an input's value has
    # each $ doubled so that it reaches the engine's output as given; a var
    # is template text and goes in as written, its own inputs escaped alike.

    def one(match: re.Match[str]) -> str:
      whole, expression, var = match.group(0, 1, 2)
      if var is not None:
        if var not in self.vars:
          return whole
        return self.substitute(self.vars[var], at, template)
      if expression is None:
        return whole
      found = _INPUT.fullmatch(whole)
      if found is None:
        raise SpecError(at, f"{whole}: only ${{{{ inputs.<name> }}}} is known")
      value = _text(self.input(found.group(1), at)[1])
      return value.replace("$", "$$") if template else value

    return _SUBST.sub(one, text)

  def input(self, name: str, at: str) -> tuple[str, object]:
    if name not in self.inputs:
      raise SpecError(at, f"no input named {name!r}")
    return self.inputs[name]

  def whole_input(self, value: object, at: str) -> tuple[str, object] | None:
    if isinstance(value, str) and (found := _INPUT.fullmatch(value)):
      return self.input(found.group(1), at)
    return None

  def string(self, value: object, at: str, template: bool = False) -> str:
    if not isinstance(value, str):
      raise SpecError(at, _expected("a string", value))
    return self.substitute(value, at, template)

  def choice(self, value: object, at: str, choices: tuple[str, ...]) -> str:
    text = self.string(value, at)
    if text not in choices:
      raise SpecError(at, f"unknown value {text!r}: use {', '.join(choices)}")
    return text

  def feedback(self, value: object, at: str) -> str | None:
    # The engine renders feedback with string.Template.substitute, which
    # raises on a $ that starts no placeholder; refuse it here instead.
    if value is None:
      return None
    text = self.string(value, at, template=True)
    if not string.Template(text).is_valid():
      raise SpecError(at, "not a valid template: write $$ for a lone $")
    return text

  def optional_string(self, value: object, at: str) -> str | None:
    return None if value is None else self.string(value, at)

  def strings(self, value: object, at: str) -> tuple[str, ...]:
    return tuple(
      self.string(v, f"{at}[{i}]") for i, v in enumerate(_list(value, at))
    )

  def integer(self, value: object, at: str, minimum: int | None = None) -> int:
    whole = self.whole_input(value, at)
    if whole is not None:
      value = _typed(whole, {"integer"}, "an integer", at)
    if not isinstance(value, int) or isinstance(value, bool):
      raise SpecError(at, _expected("an integer", value))
    if minimum is not None and value < minimum:
      raise SpecError(at, f"must be at least {minimum}, not {value}")
    return value

  def number(
    self, value: object, at: str, minimum: float | None = None
  ) -> float:
    whole = self.whole_input(value, at)
    if whole is not None:
      value = _typed(whole, {"integer", "number"}, "a number", at)
    if not isinstance(value, int | float) or isinstance(value, bool):
      raise SpecError(at, _expected("a number", value))
    if minimum is not None and value < minimum:
      raise SpecError(at, f"must be at least {minimum}, not {value}")
    return float(value)

  def boolean(self, value: object, at: str) -> bool:
    whole = self.whole_input(value, at)
    if whole is not None:
      value = _typed(whole, {"boolean"}, "a boolean", at)
    if not isinstance(value, bool):
      raise SpecError(at, _expected("a boolean", value))
    return value

  def file(self, value: object, at: str) -> Path:
    # A file an input names is the caller's to make, maybe after loading.
    from_input = self.whole_input(value, at) is not None
    path = self.base_dir / self.string(value, at)
    if not from_input and not path.is_file():
      raise SpecError(at, f"no such file: {path}")
    return path

  def input_ref(self, value: object, at: str) -> Input:
    whole = self.whole_input(value, at)
    if whole is not None and whole[0] == "path":
      return self.base_dir / cast(str, whole[1])
    if _is_mapping(value):
      fields = _fields(value, at, required={"path"}, optional=set())
      return self.base_dir / self.string(fields["path"], f"{at}.path")
    return self.string(value, at)

  def input_refs(self, value: object, at: str) -> tuple[Input, ...]:
    return tuple(
      self.input_ref(v, f"{at}[{i}]") for i, v in enumerate(_list(value, at))
    )

  def data(self, value: object, at: str) -> object:
    if isinstance(value, str):
      whole = self.whole_input(value, at)
      return self.string(value, at) if whole is None else whole[1]
    if isinstance(value, list):
      items = cast(list[object], value)
      return [self.data(v, f"{at}[{i}]") for i, v in enumerate(items)]
    if _is_mapping(value):
      return {
        k: self.data(v, f"{at}.{k}") for k, v in _mapping(value, at).items()
      }
    return value

  def prompt(self, value: object, at: str) -> str:
    if not _is_mapping(value):
      return self.string(value, at, template=True)
    fields = _fields(value, at, required={"file"}, optional={"vars"})
    path = self.file(fields["file"], f"{at}.file")
    extra: dict[str, str] = {}
    for name, var in _mapping(fields.get("vars", {}), f"{at}.vars").items():
      _check_name(name, f"{at}.vars.{name}")
      extra[name] = self.string(var, f"{at}.vars.{name}", template=True)
    try:
      text = path.read_text()
    except OSError as error:
      raise SpecError(f"{at}.file", str(error)) from error
    names = {
      name: self.substitute(raw, at, template=True)
      for name, raw in self.vars.items()
    }
    return _replace_vars(text, {**names, **extra})

  def script(self, fields: Mapping[str, object], at: str) -> ScriptSpec:
    return ScriptSpec(
      self.strings(fields["argv"], f"{at}.argv"),
      inputs=self.input_refs(fields.get("inputs", []), f"{at}.inputs"),
      outputs=self.strings(fields.get("outputs", []), f"{at}.outputs"),
      memory_gb=(
        None
        if "memory_gb" not in fields
        else self.number(fields["memory_gb"], f"{at}.memory_gb", minimum=0)
      ),
      optional_inputs=self.input_refs(
        fields.get("optional_inputs", []), f"{at}.optional_inputs"
      ),
      build=self.boolean(fields.get("build", False), f"{at}.build"),
      optional_outputs=self.strings(
        fields.get("optional_outputs", []), f"{at}.optional_outputs"
      ),
    )

  def judge(self, value: object, at: str) -> Judge:
    fields = _fields(
      value, at, required={"argv"}, optional={*_SCRIPT, "outcomes"}
    )
    outcomes = self.strings(fields.get("outcomes", []), f"{at}.outcomes")
    return Judge(self.script(fields, at), outcomes)

  def limits(self, value: object, at: str) -> Limits:
    fields = _fields(value, at, required={"memory_gb"}, optional={"cpus"})
    cpus = fields.get("cpus")
    return Limits(
      self.number(fields["memory_gb"], f"{at}.memory_gb", minimum=0),
      None if cpus is None else self.number(cpus, f"{at}.cpus", minimum=0),
    )

  def trial_agent(self, value: object, at: str) -> TrialAgent:
    if _is_mapping(value):
      return cast(Mapping[str, object], self.data(value, at))
    return self.string(value, at)


_SCRIPT = {
  "inputs",
  "outputs",
  "memory_gb",
  "optional_inputs",
  "build",
  "optional_outputs",
}

_PROTOCOLS = {
  "env": ("an Env", ("start", "stop", "stage", "exec", "collect")),
  "agent": ("an AgentRunner", ("start", "resume")),
  "trial": ("a TrialRunner", ("memory_gb", "run")),
}
"""What env and runner bindings must be, by the methods each must have."""


def _agent(
  loader: _Loader, value: Mapping[str, object], at: str
) -> dict[str, object]:
  fields = _fields(
    value,
    at,
    required={"prompt", "workdir"},
    optional={
      "inputs",
      "optional_inputs",
      "outputs",
      "validator",
      "max_fixes",
      "output_schema",
      "tools",
      "name",
      "report_prompt",
      "report_outputs",
      "last_if_unresumable",
      "judge",
    },
  )
  get = fields.get
  spec = AgentSpec(
    loader.prompt(fields["prompt"], f"{at}.prompt"),
    Path(loader.string(fields["workdir"], f"{at}.workdir")),
    inputs=loader.input_refs(get("inputs", []), f"{at}.inputs"),
    outputs=loader.strings(get("outputs", []), f"{at}.outputs"),
    validator=(
      None
      if get("validator") is None
      else loader.file(fields["validator"], f"{at}.validator")
    ),
    max_fixes=loader.integer(get("max_fixes", 2), f"{at}.max_fixes", minimum=0),
    output_schema=(
      None
      if get("output_schema") is None
      else cast(
        Mapping[str, object],
        loader.data(
          _mapping(fields["output_schema"], f"{at}.output_schema"),
          f"{at}.output_schema",
        ),
      )
    ),
    tools=(
      None
      if get("tools") is None
      else loader.strings(fields["tools"], f"{at}.tools")
    ),
    name=loader.optional_string(get("name"), f"{at}.name"),
    optional_inputs=loader.input_refs(
      get("optional_inputs", []), f"{at}.optional_inputs"
    ),
  )
  # Only a declared judge is passed on, so that a subclass a dataset
  # registers need not take one.
  judge = (
    {}
    if "judge" not in fields
    else {"judge": loader.judge(fields["judge"], f"{at}.judge")}
  )
  return {
    **judge,
    "spec": spec,
    "report_prompt": (
      None
      if get("report_prompt") is None
      else loader.string(
        fields["report_prompt"], f"{at}.report_prompt", template=True
      )
    ),
    "report_outputs": loader.strings(
      get("report_outputs", []), f"{at}.report_outputs"
    ),
    "last_if_unresumable": loader.boolean(
      get("last_if_unresumable", False), f"{at}.last_if_unresumable"
    ),
  }


def _script(
  loader: _Loader, value: Mapping[str, object], at: str
) -> dict[str, object]:
  fields = _fields(value, at, required={"argv"}, optional=_SCRIPT)
  return {"spec": loader.script(fields, at)}


def _judge(
  loader: _Loader, value: Mapping[str, object], at: str
) -> dict[str, object]:
  return {"judge": loader.judge(value, at)}


def _trial(
  loader: _Loader, value: Mapping[str, object], at: str
) -> dict[str, object]:
  fields = _fields(
    value, at, required={"task_dir", "agent", "limits"}, optional=set()
  )
  return _trial_args(loader, fields, at)


def _trial_args(
  loader: _Loader, fields: Mapping[str, object], at: str
) -> dict[str, object]:
  return {
    "task_dir": loader.input_ref(fields["task_dir"], f"{at}.task_dir"),
    "agent": loader.trial_agent(fields["agent"], f"{at}.agent"),
    "limits": loader.limits(fields["limits"], f"{at}.limits"),
  }


def _pass_at_k(
  loader: _Loader, value: Mapping[str, object], at: str
) -> dict[str, object]:
  fields = _fields(
    value,
    at,
    required={"task_dir", "agent", "limits", "k"},
    optional={"judge"},
  )
  judge = fields.get("judge")
  return {
    **_trial_args(loader, fields, at),
    "k": loader.integer(fields["k"], f"{at}.k", minimum=1),
    "judge": None if judge is None else loader.judge(judge, f"{at}.judge"),
  }


def _trials(
  loader: _Loader, value: Mapping[str, object], at: str
) -> dict[str, object]:
  fields = _fields(
    value,
    at,
    required={"task_dir", "agents", "limits"},
    optional={"configs", "each_task", "judge"},
  )
  agents = {
    name: loader.integer(n, f"{at}.agents.{name}", minimum=1)
    for name, n in _mapping(fields["agents"], f"{at}.agents").items()
  }
  configs = {
    name: loader.trial_agent(agent, f"{at}.configs.{name}")
    for name, agent in _mapping(
      fields.get("configs", {}), f"{at}.configs"
    ).items()
  }
  judge = fields.get("judge")
  return {
    "task_dir": loader.input_ref(fields["task_dir"], f"{at}.task_dir"),
    "agents": agents,
    "limits": loader.limits(fields["limits"], f"{at}.limits"),
    "configs": configs,
    "each_task": loader.boolean(
      fields.get("each_task", False), f"{at}.each_task"
    ),
    "judge": None if judge is None else loader.judge(judge, f"{at}.judge"),
  }


def _rubric(
  loader: _Loader, value: Mapping[str, object], at: str
) -> dict[str, object]:
  fields = _fields(
    value,
    at,
    required={"reviews"},
    optional={"workdir", "resume", "reuse_unchanged", "cross_criteria"},
  )
  reviews: list[Review] = []
  for i, item in enumerate(_list(fields["reviews"], f"{at}.reviews")):
    where = f"{at}.reviews[{i}]"
    review = _fields(
      item, where, required={"name", "rubric", "inputs"}, optional={"notes"}
    )
    inputs = {
      tag: loader.input_ref(ref, f"{where}.inputs.{tag}")
      for tag, ref in _mapping(review["inputs"], f"{where}.inputs").items()
    }
    notes = {
      tag: loader.string(name, f"{where}.notes.{tag}")
      for tag, name in _mapping(
        review.get("notes", {}), f"{where}.notes"
      ).items()
    }
    reviews.append(
      Review(
        loader.string(review["name"], f"{where}.name"),
        loader.file(review["rubric"], f"{where}.rubric"),
        inputs,
        notes,
      )
    )
  workdir = fields.get("workdir")
  return {
    "reviews": reviews,
    "workdir": (
      None if workdir is None else Path(loader.string(workdir, f"{at}.workdir"))
    ),
    "resume": loader.boolean(fields.get("resume", False), f"{at}.resume"),
    "reuse_unchanged": loader.boolean(
      fields.get("reuse_unchanged", False), f"{at}.reuse_unchanged"
    ),
    "cross_criteria": loader.boolean(
      fields.get("cross_criteria", False), f"{at}.cross_criteria"
    ),
  }


_KINDS: dict[str, _Kind] = {
  "agent": _Kind(AgentState, _agent, env=True, runner="agent"),
  "script": _Kind(ScriptState, _script, env=True),
  "judge": _Kind(JudgeState, _judge),
  "pass-at-k": _Kind(PassAtKState, _pass_at_k, runner="trial"),
  "trial": _Kind(TrialState, _trial, runner="trial"),
  "trials": _Kind(TrialsState, _trials, runner="trial"),
  "rubric": _Kind(RubricState, _rubric, env=True, runner="agent"),
}
"""What uses names, a subclass before its base (pass-at-k before trial)."""


type _Read = Callable[[_Loader, object, str], object]

_DOCKER: dict[str, _Read] = {
  "mounts": _Loader.mounts,
  "outputs": _Loader.mount,
  "cpus": lambda r, v, at: r.number(v, at, minimum=0),
  "cpu_shares": lambda r, v, at: r.integer(v, at, minimum=0),
  "memory_gb": lambda r, v, at: r.number(v, at, minimum=0),
  "memory_swap_gb": lambda r, v, at: r.number(v, at, minimum=0),
  "pids": lambda r, v, at: r.integer(v, at, minimum=1),
  "network": _Loader.boolean,
  "labels": lambda r, v, at: r.renames(v, at),
  "security": _Loader.strings,
  "cap_drop": _Loader.strings,
  "user": _Loader.string,
  "workdir": _Loader.string,
  "pre_start": _Loader.callable_hook,
  "post_start": _Loader.callable_hook,
  "cli": _Loader.callable_hook,
}
"""The Docker fields read as data or hooks; env and pass_env are read apart."""


def _claude_code(
  loader: _Loader, value: Mapping[str, object], at: str
) -> object:
  # Imported as a module: dataforge.runtime.claude imports this package.
  from dataforge.runtime import claude

  fields = _fields(
    value,
    at,
    required={"model", "effort", "max_budget_usd", "files"},
    optional={
      "command",
      "thinking_display",
      "timeout",
      "env",
      "pass_env",
      "tools",
      "add_dirs",
      "continue_session",
      "min_fix_usd",
      "serve",
      "spent",
    },
  )
  get = fields.get
  session: dict[str, object] = {
    "model": loader.string(fields["model"], f"{at}.model"),
    "effort": loader.string(fields["effort"], f"{at}.effort"),
    "max_budget_usd": loader.number(
      fields["max_budget_usd"], f"{at}.max_budget_usd", minimum=0
    ),
    **(
      {
        "thinking_display": loader.choice(
          fields["thinking_display"],
          f"{at}.thinking_display",
          claude.THINKING_DISPLAYS,
        )
      }
      if "thinking_display" in fields
      else {}
    ),
  }
  if "command" in fields:
    session["command"] = loader.command(fields["command"], f"{at}.command")
  if "timeout" in fields:
    session["timeout"] = loader.number(
      fields["timeout"], f"{at}.timeout", minimum=0
    )
  if "env" in fields or "pass_env" in fields:
    session["env"] = loader.env_vars(fields, at)
  runner: dict[str, object] = {}
  for key in ("tools", "add_dirs"):
    if key in fields:
      runner[key] = loader.strings(fields[key], f"{at}.{key}")
  if "continue_session" in fields:
    runner["continue_session"] = loader.boolean(
      fields["continue_session"], f"{at}.continue_session"
    )
  if "min_fix_usd" in fields:
    runner["min_fix_usd"] = loader.number(
      fields["min_fix_usd"], f"{at}.min_fix_usd", minimum=0
    )
  for key in ("serve", "spent"):
    if get(key) is not None:
      runner[key] = loader.callable_hook(fields[key], f"{at}.{key}")
  return claude.Runner(
    claude.Session(**session),  # pyright: ignore[reportArgumentType]
    _files(loader, fields["files"], f"{at}.files"),
    **runner,  # pyright: ignore[reportArgumentType]
  )


def _files(loader: _Loader, value: object, at: str) -> claude.Files:
  from dataforge.runtime import claude

  fields = _mapping(value, at)
  if "numbered" not in fields:
    return cast("claude.Files", loader.callable_hook(value, at))
  fields = _fields(fields, at, required={"numbered"}, optional=set())
  numbered = _fields(
    fields["numbered"], f"{at}.numbered", required={"dir"}, optional={"prefix"}
  )
  run_dir = loader.path(numbered["dir"], f"{at}.numbered.dir")
  if "prefix" not in numbered:
    return claude.numbered(run_dir)
  prefix = loader.string(numbered["prefix"], f"{at}.numbered.prefix")
  return claude.numbered(run_dir, prefix)


def _overlays(loader: _Loader, value: object, at: str) -> tuple[Path, ...]:
  return tuple(
    loader.path(v, f"{at}[{i}]") for i, v in enumerate(_list(value, at))
  )


def _sidecar(loader: _Loader, value: object, at: str) -> Sidecar:
  fields = _fields(value, at, required=set(), optional=set(_SIDECAR))
  return Sidecar(**_read(loader, fields, _SIDECAR, at))  # pyright: ignore[reportArgumentType]


_SIDECAR: dict[str, _Read] = {
  "cpus": lambda r, v, at: r.number(v, at, minimum=0),
  "memory_mb": lambda r, v, at: r.integer(v, at, minimum=1),
}

_HARBOR: dict[str, _Read] = {
  "cpus": lambda r, v, at: r.number(v, at, minimum=0),
  "cpu_shares": lambda r, v, at: r.integer(v, at, minimum=0),
  "pids": lambda r, v, at: r.integer(v, at, minimum=1),
  "sidecar": _sidecar,
  "timeout": lambda r, v, at: r.number(v, at, minimum=0),
  "mounts": _Loader.mounts,
  "compose_overlay": _overlays,
  "setup": _Loader.callable_hook,
  "docker": _Loader.string,
}
"""HarborRunner's fields that a declaration sets besides harbor, env, pass_env.

Only Python sets verifier, environment and tmpfs_mb; a declaration that
names one is refused as an unknown field.
"""


def _harbor(loader: _Loader, value: Mapping[str, object], at: str) -> object:
  fields = _fields(
    value,
    at,
    required={"harbor"},
    optional={*_HARBOR, "env", "pass_env"},
  )
  args = _read(loader, fields, _HARBOR, at)
  args["harbor"] = loader.path(fields["harbor"], f"{at}.harbor")
  if "env" in fields or "pass_env" in fields:
    args["env"] = loader.env_vars(fields, at)
  return HarborRunner(**args)  # pyright: ignore[reportArgumentType]


def _read(
  loader: _Loader,
  fields: Mapping[str, object],
  table: Mapping[str, _Read],
  at: str,
) -> dict[str, object]:
  return {
    key: read(loader, fields[key], f"{at}.{key}")
    for key, read in table.items()
    if key in fields
  }


def _check_graph(states: Mapping[str, State], start: str) -> None:
  for name, state in states.items():
    _check_transitions(state, states, f"states.{name}.transitions")
  marks = {
    transition.mark
    for state in states.values()
    for transition in state.transitions.values()
    if isinstance(transition, Goto)
  }
  for name, state in states.items():
    for outcome, transition in state.transitions.items():
      if isinstance(transition, End) and transition.keep_mark not in {
        None,
        *marks,
      }:
        raise SpecError(
          f"states.{name}.transitions.{outcome}.keep_mark",
          f"no goto has the mark {transition.keep_mark!r}",
        )
  reached, todo = {start}, [start]
  while todo:
    for transition in states[todo.pop()].transitions.values():
      if isinstance(transition, Goto) and transition.state not in reached:
        reached.add(transition.state)
        todo.append(transition.state)
  for name in states:
    if name not in reached:
      raise SpecError(f"states.{name}", f"not reachable from {start!r}")


def _check_transitions(
  state: State, states: Mapping[str, State], at: str
) -> None:
  for outcome, transition in state.transitions.items():
    if isinstance(transition, Goto) and transition.state not in states:
      raise SpecError(
        f"{at}.{outcome}.goto", f"no state named {transition.state!r}"
      )
  possible = state.possible_outcomes()
  if possible is None:
    return
  missing = sorted(possible - set(state.transitions))
  if missing:
    raise SpecError(at, f"no transition for the outcomes {missing}")
  for outcome in state.transitions:
    if outcome not in possible:
      raise SpecError(
        f"{at}.{outcome}",
        f"the state never gives this outcome; it gives {sorted(possible)}",
      )


def _inputs(
  declared: object, given: Mapping[str, object]
) -> dict[str, tuple[str, object]]:
  found: dict[str, tuple[str, object]] = {}
  for name, kind in _mapping(declared, "inputs").items():
    at = f"inputs.{name}"
    _check_name(name, at)
    if kind not in INPUT_TYPES:
      raise SpecError(at, f"the type must be one of {', '.join(INPUT_TYPES)}")
    if name not in given:
      raise SpecError(at, "declared but not given")
    value = given[name]
    if not _is_type(cast(str, kind), value):
      raise SpecError(at, f"given {value!r}, which is not of type {kind}")
    if isinstance(value, PurePath):
      value = str(value)
    found[name] = (cast(str, kind), value)
  for name in given:
    if name not in found:
      raise SpecError(f"inputs.{name}", "given but not declared")
  return found


def _typed(
  whole: tuple[str, object], kinds: set[str], what: str, at: str
) -> object:
  if whole[0] not in kinds:
    raise SpecError(at, f"expected {what}, got an input of type {whole[0]}")
  return whole[1]


def _is_type(kind: str, value: object) -> bool:
  if isinstance(value, bool):
    return kind == "boolean"
  match kind:
    case "string":
      return isinstance(value, str)
    case "path":
      return isinstance(value, str | PurePath)
    case "integer":
      return isinstance(value, int)
    case "number":
      return isinstance(value, int | float)
    case _:
      return False


def _check_name(name: str, at: str) -> None:
  if not _NAME.fullmatch(name):
    raise SpecError(at, "a name is letters, digits and _, not starting a digit")
  if name in ENGINE_VARS:
    raise SpecError(at, f"{name!r} is a variable the engine renders")


def _replace_vars(text: str, names: Mapping[str, str]) -> str:
  def one(match: re.Match[str]) -> str:
    var = match.group(2)
    return names.get(var, match.group(0)) if var is not None else match.group(0)

  return _SUBST.sub(one, text)


def _text(value: object) -> str:
  if isinstance(value, bool):
    return "true" if value else "false"
  return str(value)


def _mapping(value: object, at: str) -> dict[str, object]:
  if not _is_mapping(value):
    raise SpecError(at, _expected("a mapping", value))
  found: dict[str, object] = {}
  for key, item in cast(Mapping[object, object], value).items():
    if not isinstance(key, str):
      raise SpecError(
        at,
        (
          f"the key {key!r} is not a string (YAML 1.1 reads on, off, yes and"
          " no as booleans; quote it)"
        ),
      )
    found[key] = item
  return found


def _fields(
  value: object, at: str, *, required: set[str], optional: set[str]
) -> dict[str, object]:
  fields = _mapping(value, at)
  for key in fields:
    if key not in required and key not in optional:
      raise SpecError(_at(at, key), "unknown field")
  for key in sorted(required):
    if key not in fields:
      raise SpecError(_at(at, key), "missing")
  return fields


def _at(at: str, key: str) -> str:
  return f"{at}.{key}" if at else key


def _is_mapping(value: object) -> bool:
  return isinstance(value, Mapping)


def _list(value: object, at: str) -> Sequence[object]:
  if not isinstance(value, list):
    raise SpecError(at, _expected("a list", value))
  return cast(list[object], value)


def _expected(what: str, value: object) -> str:
  kinds: dict[type, str] = {
    bool: "a boolean",
    int: "an integer",
    float: "a number",
    str: "a string",
    list: "a list",
    dict: "a mapping",
    type(None): "null",
  }
  kind = kinds.get(type(value), type(value).__name__)
  hint = ""
  if isinstance(value, bool) and what == "a string":
    hint = " (YAML 1.1 reads on, off, yes and no as booleans; quote it)"
  return f"expected {what}, got {kind}{hint}"
