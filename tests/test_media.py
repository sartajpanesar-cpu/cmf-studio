"""Mac now-playing → the watch's music card, and the watch's buttons → Mac playback (fake osascript/pgrep)."""

import asyncio

import pytest

from cmfstudio.core.jobs import JobQueue
from cmfstudio.core.media import MediaService, MediaSettings
from cmfstudio.store.db import Store


class FakeMac:
    def __init__(self) -> None:
        self.running = {"Spotify"}
        self.state = {"Spotify": "playing\nSo What\nMiles Davis", "Music": "paused\nClair de Lune\nDebussy"}
        self.calls: list[list[str]] = []
        self.volume = 50

    async def run(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(argv)
        if argv[0] == "pgrep":
            return (0 if argv[-1] in self.running else 1), ""
        script = argv[-1]
        if script == "output volume of (get volume settings)":
            return 0, str(self.volume)
        if script.startswith("set volume output volume "):
            self.volume = int(script.rsplit(" ", 1)[1])
            return 0, ""
        for app in self.state:
            if f'application "{app}"' in script and "player state" in script:
                return 0, self.state[app]
        return 0, ""


@pytest.fixture
async def media(paired_link, watch):
    store = Store(":memory:")
    queue = JobQueue(store, paired_link, retry_s=0.01)
    queue.start()
    mac = FakeMac()
    svc = MediaService(store, queue, run=mac.run)
    paired_link.subscribe(svc.on_link_event)
    yield svc, mac, watch, store
    await queue.stop()


async def settle():
    await asyncio.sleep(0.15)


async def test_now_playing_reaches_the_music_card_once(media):
    svc, mac, watch, store = media
    svc.save(MediaSettings(show_now_playing=True))
    assert await svc.push_if_changed() is True
    assert await svc.push_if_changed() is False, "unchanged: nothing re-sent"
    await settle()
    assert watch.state.music == ("So What", "Miles Davis")


async def test_playing_beats_paused_and_nothing_running_shows_idle(media):
    svc, mac, watch, _ = media
    mac.running = {"Spotify", "Music"}
    mac.state["Spotify"] = "paused\nSo What\nMiles Davis"
    mac.state["Music"] = "playing\nClair de Lune\nDebussy"
    assert (await svc.poll())["app"] == "Music"
    mac.running = set()
    assert await svc.poll() is None
    await svc.push_if_changed()
    await settle()
    assert watch.state.music[0] == "CMF Studio"


async def test_buttons_control_the_playing_app_and_volume(media, paired_link):
    svc, mac, watch, _ = media
    svc.save(MediaSettings(buttons_control_music=True))
    assert await svc.command("next") == "Spotify"
    assert mac.calls[-1] == ["osascript", "-e", 'tell application "Spotify" to next track']
    assert await svc.command("vol_down") == "volume" and mac.volume == 44, "one watch step: 50% is 8/16, down to 7/16"
    assert await svc.command("unknown") is None

    paired_link._emit("button", name="play_pause", raw="0100")
    await settle()
    assert mac.calls[-1] == ["osascript", "-e", 'tell application "Spotify" to playpause']


async def test_buttons_ignored_when_off_and_never_launch_apps(media, paired_link):
    svc, mac, *_ = media
    paired_link._emit("button", name="play_pause", raw="0100")
    await settle()
    assert not any("playpause" in " ".join(c) for c in mac.calls)
    mac.running = set()
    svc.save(MediaSettings(buttons_control_music=True))
    assert await svc.command("play_pause") is None, "nothing running: nothing to control (and nothing launched)"
    assert all(c[0] == "pgrep" for c in mac.calls)


async def test_volume_presses_step_the_mac_and_echo_the_level(media, monkeypatch):
    svc, mac, *_ = media
    sent: list[dict] = []
    submit = svc.queue.submit
    monkeypatch.setattr(svc.queue, "submit", lambda kind, args, **kw: (sent.append(args), submit(kind, args, **kw))[1])
    svc.save(MediaSettings(buttons_control_music=True))
    for _ in range(3):
        await svc.command("vol_up")
    assert mac.volume == 69 and [a["volume"] for a in sent] == [9, 10, 11], "each press echoes the new level"
    mac.volume = 100
    await svc.command("vol_up")
    assert mac.volume == 100 and sent[-1]["volume"] == 16, "clamped at the top"
    mac.volume = 25
    await svc.push_if_changed()
    assert sent[-1]["volume"] == 4, "a Mac-side change reaches the watch on the next poll"


async def test_volume_press_with_unreadable_output_does_nothing(media):
    svc, mac, *_ = media
    real = mac.run

    async def run(argv):
        return (0, "missing value") if argv[-1].startswith("output volume") else await real(argv)

    svc.run = run
    assert await svc.command("vol_up") is None
    assert not any(c[-1].startswith("set volume") for c in mac.calls)
