import gc

import pytest

from tests.sigterm import sigterm_once


@pytest.mark.parametrize("collecting", [True, False])
def test_no_garbage_is_collected_in_the_block_and_the_state_is_restored(
  collecting: bool,
):
  was = gc.isenabled()
  gc.enable() if collecting else gc.disable()
  try:
    with sigterm_once(lambda: False):
      assert not gc.isenabled()
    assert gc.isenabled() is collecting
  finally:
    gc.enable() if was else gc.disable()
