import asyncio

from cmfstudio.ble.link import LinkState
from cmfstudio.core.power import WakeWatcher

from .conftest import wait_state


async def test_detects_wall_clock_jump():
    clock = {"wall": 1000.0, "mono": 50.0}
    wakes = []
    w = WakeWatcher(wakes.append, tick_s=0.01, jump_s=20, wall=lambda: clock["wall"], mono=lambda: clock["mono"])
    task = asyncio.create_task(w.run())
    await asyncio.sleep(0.03)
    assert wakes == []
    clock["wall"] += 600  # slept 10 min: wall moved, monotonic didn't
    await asyncio.sleep(0.03)
    task.cancel()
    assert len(wakes) == 1 and wakes[0] >= 590


async def test_nudge_skips_backoff(paired_link, watch):
    paired_link.backoff_s = (30.0,)  # would wait 30 s without a nudge
    watch.reachable = False
    watch.drop()
    await wait_state(paired_link, LinkState.DISCONNECTED)
    await asyncio.sleep(0.05)
    watch.reachable = True
    paired_link.nudge()
    await wait_state(paired_link, LinkState.CONNECTED, timeout=1)


async def test_nudge_checks_link_immediately(paired_link, watch):
    paired_link.keepalive_s = 30.0
    await asyncio.sleep(0.1)  # let the current keepalive wait pick up the long timeout
    watch.up = False  # silent drop: no disconnect callback, like a sleeping Mac
    paired_link.nudge()
    await wait_state(paired_link, LinkState.DISCONNECTED, timeout=1)


async def test_bluetooth_off_polls_fast_and_reconnects(paired_link, watch):
    # the first retry after a drop is quick; without the fast poll, the next one would wait 30 s
    paired_link.backoff_s = (0.01, 30.0)
    paired_link.bt_off_poll_s = 0.05
    watch.bt_off = True
    watch.drop()
    await wait_state(paired_link, LinkState.DISCONNECTED)
    await asyncio.sleep(0.2)  # several fast polls while off
    watch.bt_off = False
    await wait_state(paired_link, LinkState.CONNECTED, timeout=1)
