import asyncio

import pytest
from cmfkit.protocol import opcodes as ops
from cmfkit.protocol.frame import FrameDecoder, FrameError
from cmfkit.session import Session

from cmfstudio.ble import buttons
from cmfstudio.ble.frames import SafeFrameDecoder
from cmfstudio.ble.keys import MemoryKeyStore
from cmfstudio.ble.link import LinkState, NotConnectedError

from .conftest import wait_state

KEY = bytes(range(16))


# -- captured frames ----------------------------------------------------------


def test_zero_length_ack_decodes(frames):
    raw = bytes.fromhex(frames["notification_ack_raw"])
    msgs = list(SafeFrameDecoder(key=KEY).feed(raw))
    assert msgs[0].op == ops.APP_NOTIFICATION_ACK and msgs[0].payload == b""


def test_cmfkit_decoder_drops_zero_length_ack(frames):
    # documents the upstream bug SafeFrameDecoder exists for
    with pytest.raises(FrameError):
        list(FrameDecoder(key=KEY).feed(bytes.fromhex(frames["notification_ack_raw"])))


def test_undecryptable_frame_error_keeps_raw_body():
    raw = bytes.fromhex("f50010ffff00010001a0622a8af4ab82ca70bfec34f7121048adcf")  # FFFF:A062, run 5
    with pytest.raises(FrameError, match="body=2a8af4ab"):
        list(SafeFrameDecoder(key=KEY).feed(raw))


def test_button_payloads(frames):
    for payload, name in frames["music_button_payloads"].items():
        assert buttons.decode(bytes.fromhex(payload)).name == name
    assert buttons.decode(b"\x09").name == "unknown"


# -- link against the simulator -----------------------------------------------


async def test_no_key_means_needs_pair_and_never_auto_pairs(start_link, watch):
    link = start_link()
    await wait_state(link, LinkState.NEEDS_PAIR)
    await asyncio.sleep(0.15)  # several retry periods
    assert watch.connects == 0, "must not connect (and so never pair) without a key"
    assert "cmf pair" in link.last_error


async def test_pair_then_connected_with_info(paired_link):
    st = paired_link.status()
    assert st["state"] == "connected"
    assert st["firmware"] == "1.0.0.77" and st["serial"] == "CI0SIMULATOR0001" and st["battery"] == 87


async def test_notify_is_acked(paired_link, watch):
    assert await paired_link.notify("CMF Studio", "hello") is True
    assert watch.state.notifications[-1] == ("CMF Studio", "hello")


async def test_install_photo_face(paired_link, watch):
    from cmfkit.watchface import photo
    from PIL import Image

    blob = photo.build(Image.new("RGB", (466, 466), (0, 0, 80)))
    assert await paired_link.install_photo_face(blob) is True
    assert watch.state.active_dial is not None


async def test_reconnects_after_drop(paired_link, watch):
    before = watch.connects
    watch.drop()
    await wait_state(paired_link, LinkState.CONNECTED)
    await asyncio.sleep(0.05)
    assert watch.connects > before
    assert await paired_link.notify("back", "again")


async def test_ops_fail_fast_while_watch_away(paired_link, watch):
    watch.reachable = False
    watch.drop()
    await wait_state(paired_link, LinkState.DISCONNECTED)
    with pytest.raises(NotConnectedError):
        await paired_link.notify("x")
    watch.reachable = True
    await wait_state(paired_link, LinkState.CONNECTED)


async def test_rejected_key_means_needs_pair(start_link, watch):
    keys = MemoryKeyStore()
    keys.put("SIM", KEY)  # not the key this watch issued
    link = start_link(keys)
    await wait_state(link, LinkState.NEEDS_PAIR)
    assert "re-pair" in link.last_error


async def test_button_events(paired_link, watch):
    events = []
    paired_link.subscribe(events.append)
    await paired_link.set_music("CMF Studio", "press")
    watch.press("next")
    await asyncio.sleep(0.05)
    assert any(e["kind"] == "button" for e in events)


def test_studio_session_replaces_decoder():
    from cmfkit.transport.sim import SimWatch

    from cmfstudio.ble.link import StudioSession

    s = StudioSession(SimWatch())
    assert isinstance(s._decoder, SafeFrameDecoder) and isinstance(s, Session)
