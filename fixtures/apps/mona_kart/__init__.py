"""
Mona Kart: race three rivals round the Merge Bay Loop.

SOLO: pick a driver, then three laps against three AI rivals. VERSUS: hold two
badges back to back and race a friend 1v1 on a split screen. The kart
accelerates on its own; you only steer.

Controls:
* Up / Down = SOLO or VERSUS on the title
* Left / Right = Steer (and pick a driver)
* Enter = Spend a token for a boost
* Down = Brake
* Back = Pause, then Back again to leave the race
"""

import os
import sys

APP_DIR = "/system/apps/mona_kart"

try:
    os.chdir(APP_DIR)
    sys.path.insert(0, APP_DIR)
except OSError:
    pass  # running in the desktop simulator, which sets the path itself

from badgeware import State

import kart_art  # noqa: F401  (loaded up front so the first race frame doesn't stall)
import kart_hud as hud
import kart_render as render
from kart_config import LAPS, PLAYER_Z
from kart_racers import CHARACTERS, Controls, Race
from kart_track import TRACK

badge.mode(HIRES | VSYNC)
screen.antialias = image.OFF
previous_clear = badge.default_clear
badge.default_clear = None

STATE_KEY = "mona_kart"
OLD_STATE_KEY = "badger_kart"   # saves from before the rename
COUNTDOWN_MS = 3000
GO_MS = 600
FINISH_HOLD_MS = 2500
BANNER_MS = 1100
ATTRACT_SPEED = 2600        # world units per second for the title screen fly-by
MAX_DT = 0.1

TITLE = 0
SELECT = 1
COUNTDOWN = 2
RACE = 3
RESULTS = 4
VERSUS = 5              # kart_versus.Versus runs the whole 1v1 mode

BANNERS = {
    "token": "+TOKEN",
    "bug": "BUG! SPUN OUT",
    "final_lap": "FINAL LAP",
    "finish": "FINISH!",
}


class Game:
    def __init__(self):
        self.saved = {"best_ms": 0, "best_lap_ms": 0, "character": 4}  # fresh saves start as Mona
        if not State.load(STATE_KEY, self.saved) and State.load(OLD_STATE_KEY, self.saved):
            State.save(STATE_KEY, self.saved)
            State.delete(OLD_STATE_KEY)
        self.character = self.saved["character"] % len(CHARACTERS)
        self.mode = TITLE
        self.mode_at = badge.ticks
        self.race = None
        self.controls = Controls()
        self.attract_z = 0.0
        self.banner = None
        self.banner_until = 0
        self.finished_at = 0
        self.new_best = False
        self.paused = False
        self.light = 0.0
        self.choice = 0         # title menu: 0 = SOLO, 1 = VERSUS
        self.versus = None

    def remember_character(self, character):
        self.character = character
        if self.saved["character"] != character:
            self.saved["character"] = character
            State.save(STATE_KEY, self.saved)

    def set_mode(self, mode, now):
        self.mode = mode
        self.mode_at = now

    def new_race(self, now):
        self.race = Race(TRACK, self.character)
        self.banner = None
        self.paused = False
        self.new_best = False
        self.finished_at = 0
        self.set_mode(COUNTDOWN, now)

    def flash(self, text, now):
        self.banner = text
        self.banner_until = now + BANNER_MS

    # --- input / logic ---------------------------------------------------

    def update(self, now, dt):
        mode = self.mode
        if mode == TITLE:
            self.attract_z = (self.attract_z + ATTRACT_SPEED * dt) % TRACK.length
            if badge.pressed(BUTTON_UP) or badge.pressed(BUTTON_DOWN):
                self.choice = 1 - self.choice
            if badge.pressed(BUTTON_SELECT):
                if self.choice == 1:
                    self.start_versus(now)
                else:
                    self.set_mode(SELECT, now)
            elif badge.pressed(BUTTON_BACK):
                reset()
        elif mode == VERSUS:
            result = self.versus.update(now, dt)
            self.race = self.versus.race
            if result == "title":
                self.stop_versus()
                self.set_mode(TITLE, now)
        elif mode == SELECT:
            if badge.pressed(BUTTON_LEFT):
                self.character = (self.character - 1) % len(CHARACTERS)
            if badge.pressed(BUTTON_RIGHT):
                self.character = (self.character + 1) % len(CHARACTERS)
            if badge.pressed(BUTTON_SELECT):
                self.remember_character(self.character)
                self.new_race(now)
            elif badge.pressed(BUTTON_BACK):
                self.set_mode(TITLE, now)
        elif mode == COUNTDOWN:
            self.race.update(now, dt, self.controls)
            if now - self.mode_at >= COUNTDOWN_MS:
                self.race.start(now)
                self.set_mode(RACE, now)
        elif mode == RACE:
            self.update_race(now, dt)
        elif mode == RESULTS:
            if badge.pressed(BUTTON_SELECT):
                self.new_race(now)
            elif badge.pressed(BUTTON_BACK):
                self.race = None
                self.set_mode(TITLE, now)

    def start_versus(self, now):
        import kart_versus  # loaded on demand: solo players never touch the radio
        self.race = None
        self.versus = kart_versus.Versus(self, now)
        self.set_mode(VERSUS, now)

    def stop_versus(self):
        if self.versus is not None:
            self.versus.close()
            self.versus = None
            self.race = None

    def update_race(self, now, dt):
        if self.paused:
            if badge.pressed(BUTTON_SELECT):
                self.paused = False
            elif badge.pressed(BUTTON_BACK):
                self.race = None
                self.set_mode(TITLE, now)
            return
        if badge.pressed(BUTTON_BACK):
            self.paused = True
            return

        controls = self.controls
        controls.steer = (1 if badge.held(BUTTON_RIGHT) else 0) - (1 if badge.held(BUTTON_LEFT) else 0)
        controls.brake = badge.held(BUTTON_DOWN)
        controls.boost = badge.pressed(BUTTON_SELECT)

        race = self.race
        race.update(now, dt, controls)

        for event in race.events:
            if event == "lap" and "final_lap" not in race.events:
                self.flash("LAP %d/%d" % (min(race.player.lap + 1, LAPS), LAPS), now)
            elif event in BANNERS:
                self.flash(BANNERS[event], now)
            if event in ("token", "boost", "pad", "finish"):
                self.light = 1.0

        if race.finished and not self.finished_at:
            self.finished_at = now
            self.record_best()
        if self.finished_at and now - self.finished_at >= FINISH_HOLD_MS:
            self.set_mode(RESULTS, now)

    def record_best(self):
        player = self.race.player
        saved = self.saved
        if player.finish_ms and (not saved["best_ms"] or player.finish_ms < saved["best_ms"]):
            saved["best_ms"] = player.finish_ms
            self.new_best = True
        if player.lap_ms:
            best_lap = min(player.lap_ms)
            if not saved["best_lap_ms"] or best_lap < saved["best_lap_ms"]:
                saved["best_lap_ms"] = best_lap
        State.save(STATE_KEY, saved)

    # --- drawing ---------------------------------------------------------

    def draw(self, now):
        mode = self.mode
        if mode == TITLE:
            render.draw_world(TRACK, None, self.attract_z, 0.0, now, None)
            hud.draw_title(now, self.saved["best_ms"], self.choice)
        elif mode == VERSUS:
            self.versus.draw(now)
        elif mode == SELECT:
            hud.draw_select(self.character, now)
        elif mode == RESULTS:
            hud.draw_results(self.race, now, self.new_best)
        else:
            race = self.race
            player = race.player
            camera_z = (player.z - PLAYER_Z) % TRACK.length
            render.draw_world(TRACK, race, camera_z, player.x, now, player)
            hud.draw_race_hud(race, TRACK, now)
            if mode == COUNTDOWN:
                hud.draw_countdown(COUNTDOWN_MS - (now - self.mode_at))
            elif now - self.mode_at < GO_MS:
                hud.draw_countdown(0)
            if self.paused:
                hud.draw_banner("PAUSED", now)
                hud.draw_hints((("resume", "ENTER"), ("quit", "BACK")))
            elif self.banner is not None and now < self.banner_until:
                hud.draw_banner(self.banner, now)

    def update_lights(self, now, dt):
        if self.mode == VERSUS:
            self.versus.lights(now, dt)
            return
        race = self.race
        boosting = race is not None and self.mode == RACE and now < race.player.boost_until
        target = 0.6 if boosting else 0.0
        self.light = max(target, self.light - dt * 2.5)
        badge.caselights(self.light)


game = Game()


def update():
    now = badge.ticks
    dt = min(badge.ticks_delta / 1000, MAX_DT)
    game.update(now, dt)
    game.draw(now)
    game.update_lights(now, dt)


def on_exit():
    game.stop_versus()
    badge.caselights(0)
    badge.default_clear = previous_clear


run(update)
