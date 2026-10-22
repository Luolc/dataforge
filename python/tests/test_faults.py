import pytest

from dataforge.curation.faults import trial_fault, trial_judged

# Harbor's NonZeroAgentExitCodeError, as agents/installed/base.py words it.
EXIT = "NonZeroAgentExitCodeError: Command failed (exit 1): claude -p\nstdout: "


@pytest.mark.parametrize(
  ("exception", "fault", "judged"),
  [
    (None, None, True),
    ("AgentSafetyRefusalError: API Error: flagged", "model", False),
    ("ApiRateLimitError: API Error: 429", "model", False),
    ("HarborNotStarted: Harbor did not start the trial", "infra", False),
    ("HarborError: harbor exited 1", "infra", False),
    ("EnvironmentStartTimeoutError: timed out", "infra", False),
    ("TimeoutError: Harbor ran past 60 s", "timeout", False),
    ("AgentTimeoutError: ran past 3600 s", None, True),
    ("VerifierTimeoutError: timed out after 900.0 seconds", None, False),
    ("SessionError: the agent's session ended with is_error", None, False),
    ("SomethingNewError: what Harbor 0.25 adds", None, False),
    (EXIT + '{"type":"result","subtype":"error_max_budget_usd"}', None, True),
    (EXIT + '{"type": "result", "subtype": "error_max_turns"}', None, True),
    (EXIT + "\nstderr: Killed", None, False),
    (
      "NonZeroAgentExitCodeError: the agent's container hit its process limit"
      " (512) 530 times; Command failed (exit 134): claude -p\nstdout: ",
      None,
      True,
    ),
    # Only dataforge's words right after the type count, not the agent's.
    (
      EXIT + "the agent's container hit its process limit (512) 1 times;",
      None,
      False,
    ),
  ],
)
def test_a_trial_exception_reads_as_its_fault(
  exception: str | None, fault: str | None, judged: bool
) -> None:
  assert (trial_fault(exception), trial_judged(exception)) == (fault, judged)
