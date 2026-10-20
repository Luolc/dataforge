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
  states:
    <name>:
      uses: agent | script | judge | trial | pass-at-k | trials | rubric
            | <binding>               # a State subclass the dataset registers
      with: {...}                     # the state's own arguments
      env: <binding>                  # agent, script, rubric
      runner: <binding>               # agent and rubric: an AgentRunner;
                                      # trial, pass-at-k, trials: TrialRunner
      max_visits: <integer>
      on_exhausted: <string>
      max_cost_usd: <number>
      rename: {<outcome>: <new name>}
      feedback: <template>
      transitions:
        <outcome>: {goto: <state>, report: <boolean>, mark: <string>,
                    end_on_last_visit: <boolean>}
                 | {end: <status>}

"environments" and "runners" are reserved for declaring them as data; this
version rejects them. Relative paths (prompt and rubric files, validators,
{path: ...} inputs) are against the base directory.
"""

from collections.abc import Callable, Mapping, Sequence
import dataclasses
from dataclasses import dataclass
from pathlib import Path
import re
from typing import cast

import yaml

from dataforge.curation.actions import (
  Actions,
  AgentSpec,
  Env,
  Judge,
  Limits,
  ScriptSpec,
  TrialAgent,
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
) -> Machine:
  """Read a declaration into a machine, checking it strictly.

  Nothing runs: the states are only made, and a rubric state reads its rubric
  files.

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
  return loader.machine(data, inputs, guard, on_step)


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

  def machine(
    self,
    data: object,
    inputs: Mapping[str, object],
    guard: Guard | None,
    on_step: Callable[[Attempt], None] | None,
  ) -> Machine:
    top = _fields(
      data,
      "",
      required={"dataforge", "name", "start", "states"},
      optional={"inputs", "vars", "max_cost_usd", "environments", "runners"},
    )
    if top["dataforge"] != SCHEMA_VERSION or isinstance(top["dataforge"], bool):
      raise SpecError("dataforge", "the schema version must be 1")
    for reserved in ("environments", "runners"):
      if reserved in top:
        raise SpecError(
          reserved,
          (
            "not supported in schema version 1; register the object as a"
            " binding and name it"
          ),
        )
    self.inputs = _inputs(top.get("inputs", {}), inputs)
    _ = self.string(top["name"], "name")
    self.vars = {}
    for name, value in _mapping(top.get("vars", {}), "vars").items():
      at = f"vars.{name}"
      _check_name(name, at)
      self.vars[name] = self.string(value, at)
    cap = None
    if "max_cost_usd" in top:
      cap = self.number(top["max_cost_usd"], "max_cost_usd", minimum=0)
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
    return Machine(ordered, guard=guard, on_step=on_step, max_cost_usd=cap)

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
      "feedback": self.optional_string(
        fields.get("feedback"), f"{at}.feedback"
      ),
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
      args["env"] = cast(Env, self.binding(fields["env"], f"{at}.env"))
    if "runner" in fields:
      if kind.runner is None:
        raise SpecError(f"{at}.runner", f"not taken by uses: {uses}")
      runner = self.binding(fields["runner"], f"{at}.runner")
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
      _ = _fields(fields, at, required={"end"}, optional=set())
      return End(self.string(fields["end"], f"{at}.end"))
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

  def binding(self, value: object, at: str) -> object:
    name = self.string(value, at)
    if name not in self.bindings:
      raise SpecError(at, f"no binding named {name!r}")
    return self.bindings[name]

  def substitute(self, text: str, at: str, extra: Mapping[str, str]) -> str:
    names = {**self.vars, **extra}

    def one(match: re.Match[str]) -> str:
      whole, expression, var = match.group(0, 1, 2)
      if var is not None:
        return names.get(var, whole)
      if expression is None:
        return whole
      found = _INPUT.fullmatch(whole)
      if found is None:
        raise SpecError(at, f"{whole}: only ${{{{ inputs.<name> }}}} is known")
      return _text(self.input(found.group(1), at)[1])

    return _SUBST.sub(one, text)

  def input(self, name: str, at: str) -> tuple[str, object]:
    if name not in self.inputs:
      raise SpecError(at, f"no input named {name!r}")
    return self.inputs[name]

  def whole_input(self, value: object, at: str) -> tuple[str, object] | None:
    if isinstance(value, str) and (found := _INPUT.fullmatch(value)):
      return self.input(found.group(1), at)
    return None

  def string(self, value: object, at: str) -> str:
    if not isinstance(value, str):
      raise SpecError(at, _expected("a string", value))
    return self.substitute(value, at, {})

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
      return self.string(value, at)
    fields = _fields(value, at, required={"file"}, optional={"vars"})
    path = self.file(fields["file"], f"{at}.file")
    extra: dict[str, str] = {}
    for name, var in _mapping(fields.get("vars", {}), f"{at}.vars").items():
      _check_name(name, f"{at}.vars.{name}")
      extra[name] = self.string(var, f"{at}.vars.{name}")
    try:
      text = path.read_text()
    except OSError as error:
      raise SpecError(f"{at}.file", str(error)) from error
    return _replace_vars(text, {**self.vars, **extra})

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


_SCRIPT = {"inputs", "outputs", "memory_gb", "optional_inputs"}


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
      "last_if_unresumable",
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
  return {
    "spec": spec,
    "report_prompt": loader.optional_string(
      get("report_prompt"), f"{at}.report_prompt"
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
    optional={"configs", "each_task"},
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
  return {
    "task_dir": loader.input_ref(fields["task_dir"], f"{at}.task_dir"),
    "agents": agents,
    "limits": loader.limits(fields["limits"], f"{at}.limits"),
    "configs": configs,
    "each_task": loader.boolean(
      fields.get("each_task", False), f"{at}.each_task"
    ),
  }


def _rubric(
  loader: _Loader, value: Mapping[str, object], at: str
) -> dict[str, object]:
  fields = _fields(value, at, required={"reviews"}, optional={"workdir"})
  reviews: list[Review] = []
  for i, item in enumerate(_list(fields["reviews"], f"{at}.reviews")):
    where = f"{at}.reviews[{i}]"
    review = _fields(
      item, where, required={"name", "rubric", "inputs"}, optional=set()
    )
    inputs = {
      tag: loader.input_ref(ref, f"{where}.inputs.{tag}")
      for tag, ref in _mapping(review["inputs"], f"{where}.inputs").items()
    }
    reviews.append(
      Review(
        loader.string(review["name"], f"{where}.name"),
        loader.file(review["rubric"], f"{where}.rubric"),
        inputs,
      )
    )
  workdir = fields.get("workdir")
  return {
    "reviews": reviews,
    "workdir": (
      None if workdir is None else Path(loader.string(workdir, f"{at}.workdir"))
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


def _check_graph(states: Mapping[str, State], start: str) -> None:
  for name, state in states.items():
    _check_transitions(state, states, f"states.{name}.transitions")
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
    case "string" | "path":
      return isinstance(value, str)
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
