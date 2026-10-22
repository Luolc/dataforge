"""A machine's states and transitions as data, and as a Mermaid flowchart.

Machine.graph returns a Graph, and mermaid renders one. A Graph is plain data
that json.dumps takes as is.
"""

from typing import TypedDict


class GraphState(TypedDict):
  """One state of a Graph.

  Attributes:
    name: The state's name.
    kind: The name of the state's class, such as "AgentState".
    max_visits: How many times the state may be entered.
    on_exhausted: The attempt's status when the state is entered once too
      often and no visit was marked.
    max_cost_usd: The most one visit may spend, in US dollars.
  """

  name: str
  kind: str
  max_visits: int
  on_exhausted: str
  max_cost_usd: float


GraphEdge = TypedDict(
  "GraphEdge",
  {
    "from": str,
    "outcome": str,
    "to": str | None,
    "end": str | None,
    "report": bool,
    "mark": str | None,
    "end_on_last_visit": bool,
  },
)
"""One transition of a Graph.

"from" is the state and "outcome" the outcome the transition maps. A Goto has
"to" (the state it goes to), "report", "mark" and "end_on_last_visit" as the
Goto has them, and "end" None; an End has "end" (its status), "to" None,
"report" and "end_on_last_visit" False and "mark" None.
"""


class Graph(TypedDict):
  """A machine's states and transitions.

  Attributes:
    start: The name of the first state.
    states: The states, in the machine's order.
    edges: The transitions, by state in the machine's order, then in the order
      of each state's transitions.
  """

  start: str
  states: list[GraphState]
  edges: list[GraphEdge]


def mermaid(graph: Graph) -> str:
  """Render a graph as a Mermaid flowchart.

  The states are nodes s0, s1, ... in the graph's order; each status an End
  gives is one node e0, e1, ... in the order of the edges that first give it.
  A state's label shows its max_visits when that is above 1; an edge's label
  shows its outcome, then its mark, "no report" and "ends on last visit"
  where they apply. Labels are quoted, with #, &, ", <, > and ` escaped as
  Mermaid entities, so that a label shows its text as given.

  Args:
    graph: The graph, as Machine.graph returns it.

  Returns:
    The flowchart's text, one statement per line, ending with a newline.

  Raises:
    ValueError: When an edge has neither "to" nor "end".
  """
  ids = {state["name"]: f"s{i}" for i, state in enumerate(graph["states"])}
  ends: dict[str, str] = {}
  lines = ["flowchart TD", f"  start((start)) --> {ids[graph['start']]}"]
  for state in graph["states"]:
    label = _escape(state["name"])
    if state["max_visits"] > 1:
      label += f"<br/>max_visits {state['max_visits']}"
    lines.append(f'  {ids[state["name"]]}["{label}"]')
  arrows: list[str] = []
  for edge in graph["edges"]:
    to, status = edge["to"], edge["end"]
    if to is not None:
      target = ids[to]
    elif status is not None:
      if status not in ends:
        ends[status] = f"e{len(ends)}"
        lines.append(f'  {ends[status]}(["end: {_escape(status)}"])')
      target = ends[status]
    else:
      raise ValueError(f"{edge['from']}: {edge['outcome']} has no to or end")
    arrows.append(f'  {ids[edge["from"]]} -->|"{_edge_label(edge)}"| {target}')
  return "\n".join(lines + arrows) + "\n"


# "#" first, since the other entities hold it. Mermaid turns each #<code>;
# into the HTML entity &<code>;, so a raw & would start an entity of its own,
# and a label wrapped in backticks would be read as Markdown.
_ENTITIES = (
  ("#", "#35;"),
  ("&", "#38;"),
  ('"', "#quot;"),
  ("<", "#lt;"),
  (">", "#gt;"),
  ("`", "#96;"),
)


def _edge_label(edge: GraphEdge) -> str:
  parts = [edge["outcome"]]
  if edge["mark"] is not None:
    parts.append(f"mark {edge['mark']}")
  if edge["to"] is not None and not edge["report"]:
    parts.append("no report")
  if edge["end_on_last_visit"]:
    parts.append("ends on last visit")
  return ", ".join(_escape(part) for part in parts)


def _escape(text: str) -> str:
  for char, entity in _ENTITIES:
    text = text.replace(char, entity)
  return text
