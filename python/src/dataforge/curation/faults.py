"""Why a trial gave nothing that can be judged, read from its exception.

`trial_fault` reads `TrialResult.exception`, whose text starts with the type
of what ended the trial and a colon (`<type>: <message>`), as one of the
FAULTS words of dataforge.curation.actions; `trial_judged` says whether the
trial's rewards can be judged as they are. A trial is judged when nothing
ended it early, or when the agent ended it itself: on its own time limit
(Harbor's `AgentTimeoutError`) or its own spend or turn cap, as a pass@k
measures whether the solver solves the task within them. Every other
exception keeps the trial from being judged: those of TYPES with a fault,
those of UNCLEAR, whose cause may lie in the machine or in the task, and any
the table does not know, so that a new kind of failure never reads as a hard
task.

The types are Harbor 0.24.0's, each named with the file of the `harbor`
package that defines it, and dataforge's own (dataforge.curation.harbor).
Read on 2026-10-22.
"""

import re

from dataforge.curation.actions import INFRA, MODEL, TIMEOUT
from dataforge.curation.harbor import NOT_STARTED, TIMED_OUT

TYPES: dict[str, str] = {
  # agents/installed/base.py: ApiError and each of its subclasses, which
  # Harbor matches in the agent's output.
  "ApiError": MODEL,
  "ApiRateLimitError": MODEL,
  "ApiUsageLimitError": MODEL,
  "ApiInternalServerError": MODEL,
  "ApiOverloadedError": MODEL,
  "ApiConnectionClosedError": MODEL,
  "ApiResponseStalledError": MODEL,
  "OutputTokenExceededError": MODEL,
  "ContextWindowExceededError": MODEL,
  "UnknownApiError": MODEL,
  "ApiProviderResourceNotFoundError": MODEL,
  "AgentSafetyRefusalError": MODEL,
  # agents/installed/base.py
  "ModelNotFoundError": MODEL,
  # agents/installed/vibe.py
  "ApiConnectionError": MODEL,
  # llms/base.py
  "ContextLengthExceededError": MODEL,
  "OutputLengthExceededError": MODEL,
  # agents/installed/base.py: the agent's model requests go through the
  # dataset's own proxy, so a login or a connection that fails is ours.
  "AgentAuthenticationError": INFRA,
  "NetworkConnectionError": INFRA,
  # trial/errors.py
  "EnvironmentStartTimeoutError": INFRA,
  "AgentSetupTimeoutError": INFRA,
  # environments/base.py
  "HealthcheckError": INFRA,
  "ServiceOperationsUnsupportedError": INFRA,
  # environments/modal.py
  "SandboxLikelyOutOfMemoryError": INFRA,
  # environments/singularity/singularity.py
  "MemoryLimitExceededError": INFRA,
  # environments/gke.py
  "GKEExecStreamClosedError": INFRA,
  "GKEPodNotFoundError": INFRA,
  # environments/ssh.py
  "SSHAccessError": INFRA,
  # verifier/verifier.py: copying the tests in or the verifier's files out.
  "AddTestsDirError": INFRA,
  "DownloadVerifierDirError": INFRA,
  # dataforge.curation.harbor: Harbor did not start the trial, Harbor itself
  # exited non-zero, or it exited without a trial result.
  NOT_STARTED: INFRA,
  "HarborError": INFRA,
}
# The types whose trial is judged as it is (trial/errors.py).
JUDGED = frozenset({"AgentTimeoutError"})
# The types that have no fault but whose cause may lie in the machine or in
# the task, so that their trial is not judged:
UNCLEAR = frozenset(
  {
    # trial/errors.py: the verifier ran past its time on a loaded machine, or
    # the task's tests, or the solver's change, made them slow.
    "VerifierTimeoutError",
    # verifier/verifier.py: the verifier wrote no reward, or none it could
    # read; dataforge.curation.harbor's NoReward is the same.
    "RewardFileNotFoundError",
    "RewardFileEmptyError",
    "VerifierOutputParseError",
    "NoReward",
    # dataforge.curation.harbor: Harbor recorded no exception, but the
    # claude-code trace's last result has `is_error`, so the session ended on
    # an error whose cause is not known.
    "SessionError",
    # trial/trial.py: Harbor was stopped by a signal, a cancel's or our
    # timeout's.
    "CancelledError",
    # environments/docker/docker.py: a docker compose command failed, `build`
    # (the task's Dockerfile) and `up` (the machine) alike.
    "RuntimeError",
  }
)
# agents/installed/base.py: an agent's non-zero exit that matches no pattern
# is a NonZeroAgentExitCodeError itself, its message the command, its stdout
# and its stderr; claude_code.py keeps in that stdout only Claude Code's
# `result` and `error` events. It is the agent's own end when that result
# says the session hit its own cap.
_EXIT = "NonZeroAgentExitCodeError"
_OWN_CAP = re.compile(r'"subtype"\s*:\s*"error_max_(budget_usd|turns)"')
# How dataforge.curation.harbor starts the exception of a trial it stopped
# on its timeout, before what Harbor recorded.
_OUR_TIMEOUT = f"{TIMED_OUT}: Harbor ran past"


def trial_fault(exception: str | None) -> str | None:
  """Read why a trial gave nothing to judge.

  Args:
    exception: The trial's `TrialResult.exception`.

  Returns:
    One of the FAULTS words: the one TYPES gives the exception's type, or
    `timeout` for our own timeout; None when there is no exception, or its
    type has no fault (JUDGED, UNCLEAR or one the table does not know).
  """
  if exception is None:
    return None
  if exception.startswith(_OUR_TIMEOUT):
    return TIMEOUT
  return TYPES.get(_type(exception))


def trial_judged(exception: str | None) -> bool:
  """Say whether a trial's rewards can be judged as they are.

  Args:
    exception: The trial's `TrialResult.exception`.

  Returns:
    True when there is none, its type is in JUDGED, or it is a
    NonZeroAgentExitCodeError of a session that hit its own spend or turn
    cap; False for every other exception.
  """
  if exception is None:
    return True
  kind = _type(exception)
  if kind == _EXIT:
    return _OWN_CAP.search(exception) is not None
  return kind in JUDGED


def _type(exception: str) -> str:
  return exception.split(":", 1)[0].strip()
