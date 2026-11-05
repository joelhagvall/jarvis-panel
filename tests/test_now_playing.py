"""Native music/announcement separation and generated controls; no device/network/audio."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MusicUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = json.loads(subprocess.check_output([sys.executable, str(ROOT/"board.py"), "--voice",
            str(ROOT/"styles/glass-v2/jarvis_orb.py"), "--remote", "http://example.test"], cwd=ROOT/"styles/glass-v2"))

    def test_native_state_parser_retains_music_during_announcements_and_rejects_invalid_wire(self):
        source = r'''
#include "styles/glass-v2/now_playing.h"
#include <cassert>
int main() {
 panel_music::State s;
 s.native("announcing"); assert(!s.active());
 assert(s.wire("playing|Radio|P3|Sveriges Radio|0|0")); assert(s.active());
 assert(s.caption()=="Radio · Sveriges Radio");
 s.native("announcing"); assert(s.active() && s.title=="P3");
 s.native("paused"); assert(s.active() && s.state=="paused");
 assert(!s.wire("playing|too|short")); assert(s.title=="P3");
 assert(!s.wire("playing|Radio|P3||2|0"));
 s.native("idle"); assert(!s.active() && s.title.empty());
 assert(s.wire("playing|Spotify|Track|Artist|1|1")); assert(s.next && s.previous);
 assert(s.caption()=="Spotify · Artist");
 assert(s.byline()=="Artist · Spotify");
 s.native("paused"); assert(s.caption()=="Spotify · Pausat · Artist");
 assert(s.wire("playing|P3|P3 med|Juan Havana|0|0")); assert(s.caption()=="P3 · Juan Havana");
 assert(s.wire("playing|P3|P3||0|0")); assert(s.caption()=="P3 · Spelas nu");
 s.clear(); assert(!s.next && !s.previous && !s.active());
 // Radio on Jarvis's Bluetooth speaker: the panel's own player going idle (after a reply) must not hide it.
 assert(s.wire("playing|P3|P3|Sveriges Radio|0|0|1")); assert(s.remote && s.active());
 s.native("idle"); assert(s.active() && s.title=="P3");
 assert(!s.wire("playing|P3|P3||0|0|")); assert(!s.wire("playing|P3|P3||0|0|1|x")); assert(s.remote);
 assert(s.place.empty() && s.caption()=="P3 · Sveriges Radio");
 assert(s.wire("playing|P3|Slick|Victony|0|0|JBL Charge 4")); assert(s.remote && s.place=="JBL Charge 4");
 assert(s.caption()=="P3 på JBL Charge 4 · Victony");
 assert(s.wire("playing|P3|Slick|Victony|0|0|0")); assert(!s.remote && s.place.empty());
 assert(s.wire("idle||||0|0")); assert(!s.remote && !s.active());
 assert(s.wire("playing|P3|P3||0|0|1")); s.native("playing"); assert(!s.remote);
 s.native("idle"); assert(!s.active());
 panel_music::Pending p;
 p.start(100); assert(p.waiting && !p.expire(8099));
 assert(p.expire(8100) && !p.waiting && !p.expire(9000));
 p.start(0xFFFFFF00U); assert(!p.expire(100U)); assert(p.expire(0xFFFFFF00U+8000U));
 p.start(10); p.finish(); assert(!p.expire(10000));
 panel_music::Artwork art;
 assert(!art.select("https://arbitrary.test/image")); assert(!art.begin());
 assert(art.select("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")); assert(art.begin());
 assert(!art.begin() && !art.ready());
 art.select("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"); art.finish(true);
 assert(!art.ready()); assert(art.begin()); art.finish(true); assert(art.ready());
 art.select("cccccccccccccccccccccccccccccccc"); assert(art.begin()); art.finish(false);
 assert(!art.ready() && !art.begin());  // no automatic failed-download loop
 art.select(""); assert(!art.ready() && !art.begin());
 // HA's key comes once; a cover dropped by the panel's own player going idle comes back from it.
 panel_music::Artwork moved;
 assert(moved.offer("dddddddddddddddddddddddddddddddd")); assert(moved.begin()); moved.finish(true); assert(moved.ready());
 moved.select(""); assert(!moved.ready());
 // The time line moves on between Home Assistant's updates while playing, never past the end.
 panel_music::Progress line; assert(line.wire("60000|300000|1", 1000)); assert(line.at(3000) == 62000);
 assert(line.at(999999999) == 300000 && panel_music::Progress::clock(62000) == "1:02");
 assert(line.wire("60000|300000|0", 1000) && line.at(50000) == 60000);              // paused: it stands
 assert(!line.wire("garbage", 0) && line.duration_ms == 0);
 // A new song is held until its cover is in (or 4 s), whichever order HA's attributes come in.
 panel_music::Hold h; panel_music::State old; old.wire("playing|Spotify|Old|A|1|1");
 h.start(old, "Old album (2020)", panel_music::Progress{}, 5, 1000);
 assert(h.on && h.shown.title == "Old" && !h.release(false, 2000));
 assert(h.release(true, 2100) && !h.on);                                      // its cover is in: shown together
 h.start(old, "", panel_music::Progress{}, 5, 1000); assert(h.release(false, 5000));   // never stuck
 // The first source after boot or a stop keeps a cover key that came before the text; a real change drops it.
 assert(!panel_music::source_changed("", "Spotify") && panel_music::source_changed("P3", "Spotify"));
 assert(!panel_music::source_changed("Spotify", "Spotify"));
 // The speaker icons step the volume one 5 % stop, from any value, never past 0 or 100.
 assert(panel_music::volume_step(55, 5) == 60 && panel_music::volume_step(55, -5) == 50);
 assert(panel_music::volume_step(52, 5) == 55 && panel_music::volume_step(52, -5) == 50);
 assert(panel_music::volume_step(100, 5) == 100 && panel_music::volume_step(0, -5) == 0 && panel_music::volume_step(3, -5) == 0);
 assert(panel_music::volume_step(98, 5) == 100 && panel_music::volume_step(120, -5) == 95);
 // The first Spotify music after boot waits for its cover; radio, an old cover or a song change do not use this.
 panel_music::State idle, spotify, radio; spotify.wire("playing|Spotify|New|B|1|1"); radio.wire("playing|P3|Song|C|0|0");
 panel_music::Artwork fresh_boot; assert(panel_music::hold_first_cover(idle, spotify, fresh_boot));
 assert(!panel_music::hold_first_cover(idle, radio, fresh_boot) && !panel_music::hold_first_cover(old, spotify, fresh_boot));
 fresh_boot.offer("abababababababababababababababab"); fresh_boot.begin(); fresh_boot.finish(true);
 assert(!panel_music::hold_first_cover(idle, spotify, fresh_boot));               // its cover is already in
 // The download alone releases (the record follows when its atlas turns); an older download does not.
 assert(panel_music::cover_in(fresh_boot, 0) && !panel_music::cover_in(fresh_boot, 1));
 panel_music::Artwork counted; counted.offer("abababababababababababababababab"); counted.begin(); counted.finish(true);
 assert(counted.finished == 1); counted.offer("cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"); counted.begin(); counted.finish(false);
 assert(counted.finished == 1);                                               // a failed download is not a cover
 // A new song keeps the last cover until its own is in; another source drops it.
 panel_music::Artwork song; assert(song.offer("eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")); song.begin(); song.finish(true);
 assert(song.offer("ffffffffffffffffffffffffffffffff")); assert(!song.ready() && song.shown());
 song.forget(); assert(!song.shown()); song.select(""); assert(!song.shown());
 moved.restore(); assert(moved.wanted == "dddddddddddddddddddddddddddddddd" && moved.begin());
 moved.forget(); moved.restore(); assert(moved.wanted.empty());      // another source: the old logo stays away
 assert(!moved.offer("https://arbitrary.test/image") && moved.offered.empty());
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            cpp=Path(tmp)/"music.cpp"; cpp.write_text(source); binary=Path(tmp)/"music"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT), str(cpp), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_panel_publishes_music_state_that_announcements_never_set(self):
        sensor = next(t for t in self.cfg["text_sensor"] if t.get("id") == "music_native")
        self.assertEqual((sensor["platform"], sensor["name"]), ("template", "Music state"))
        boot = [b for b in self.cfg["esphome"]["on_boot"] if any("music_native" in a.get("lambda", "") for a in b["then"])]
        self.assertEqual(len(boot), 1)
        handler = next(h["lambda"] for h in self.cfg["media_player"][0]["on_state"] if "music_native" in h["lambda"])
        self.assertNotIn("ANNOUNCING", handler)               # an announcement leaves the published state alone
        for state, value in (("PLAYING", '"playing"'), ("PAUSED", '"paused"'), ("IDLE", '"idle"')):
            self.assertRegex(handler, "MEDIA_PLAYER_STATE_" + state + r"\b[^\n]*(\n[^\n]*)?music = " + value)
        media = (ROOT / "ha/custom_components/jarvis_agent/media.py").read_text()
        self.assertIn('"sensor.hallway_hallpanel_music_state"', media)   # HA's entity id for "Music state"

    def test_a_held_first_song_never_turns_the_record_black(self):
        render = next(s for s in self.cfg["script"] if s["id"] == "music_render")["then"][0]["lambda"]
        self.assertIn("if (!panel_music::state.active()) panel_vinyl::page.source(nullptr, panel_vinyl::Kind::BLANK);", render)
        self.assertNotIn("if (!m.active()) panel_vinyl::page.source(", render)
        wire = json.dumps(next(t for t in self.cfg["text_sensor"] if t.get("id") == "music_status"))
        self.assertIn("panel_music::source_changed(old_source, panel_music::state.source)", wire)

    def test_the_record_and_its_sleeve_swap_on_a_tap_and_the_page_opens_on_the_turntable(self):
        page = next(p for p in self.cfg["lvgl"]["pages"] if p["id"] == "page_musik")
        back = page["widgets"][0]["obj"]["on_click"][0]["if"]
        self.assertIn("panel_sleeve::sleeve.tap(p.x, p.y)", back["condition"]["lambda"])
        self.assertEqual(back["then"], [{"script.execute": "music_render"}])
        self.assertIn("lvgl.page.show", json.dumps(back["else"]))              # anywhere else: back, as before
        text = json.dumps(page)
        self.assertIn('"id": "mp_sleeve", "x": 29, "y": 110', text)
        self.assertIn('"id": "mp_sleeve_image", "src": "np_art"', text)
        render = self.cfg_script_lambda("music_render")
        self.assertIn("panel_sleeve::sleeve.shown(cover && !radio, millis()) && !radio", render)
        lapsed = [i for i in self.cfg["interval"] if "sleeve.lapsed" in json.dumps(i)]
        self.assertEqual(len(lapsed), 1)
        self.assertIn('{"script.execute": "music_render"}', json.dumps(lapsed[0]))
        self.assertIn("panel_vinyl::page.hide(radio || sleeve);", render)    # the record is not drawn behind it
        self.assertIn("if (radio || sleeve) lv_obj_add_flag(id(mp_turntable), LV_OBJ_FLAG_HIDDEN);", render)
        setup = json.dumps(next(s for s in self.cfg["script"] if s["id"] == "vinyl_setup"))
        self.assertIn("LV_EVENT_SCREEN_UNLOADED", setup)
        self.assertIn("panel_sleeve::sleeve.leave();", setup)
        art = json.dumps(next(i for i in self.cfg["image"] if i["id"] == "np_art"))
        self.assertIn('{"lvgl.image.update": {"id": "mp_sleeve_image", "src": "np_art"}}', art)   # a new cover

    def test_the_speaker_icons_step_the_volume_down_and_up(self):
        page = json.dumps(next(p for p in self.cfg["lvgl"]["pages"] if p["id"] == "page_musik"))
        for key, delta in (("mp_vol_down", -5), ("mp_vol_up", 5)):
            self.assertIn('"id": "' + key + '"', page)
            self.assertIn('{"script.execute": {"id": "musicpage_step", "delta": ' + str(delta) + '}}', page)
        step = next(s for s in self.cfg["script"] if s["id"] == "musicpage_step")
        self.assertEqual(step["parameters"], {"delta": "int"})
        self.assertIn("panel_music::volume_step(id(musicpage_pct), delta)", json.dumps(step))
        self.assertEqual(step["then"][-1], {"script.execute": "musicpage_set"})   # the same commit as a release

    def test_the_slider_sets_the_speaker_while_the_music_plays_there(self):
        scripts = {s["id"]: s for s in self.cfg["script"]}
        for v in range(0, 101, 5):
            action = scripts[f"speakervol_{v}"]["then"][0]["homeassistant.action"]
            self.assertEqual((action["action"], action["data"]), ("jarvis_agent.speaker_volume", {"level": str(v)}))
        branch = scripts["musicvol_set"]["then"][0]["if"]
        self.assertEqual(branch["condition"]["lambda"], "return panel_music::state.remote;")
        code = branch["then"][0]["lambda"]
        self.assertIn("case 20: id(speakervol_100).execute();", code)
        self.assertNotIn("\\n", code)                     # real line breaks, not a backslash and an n
        self.assertIn("set_volume", branch["else"][0]["lambda"])
        self.assertIn("id(speaker_vol)", scripts["music_open"]["then"][0]["lambda"])

    def test_speaker_music_keeps_its_cover_when_the_panel_goes_idle_and_lines_never_wrap(self):
        handler = next(h["lambda"] for h in self.cfg["media_player"][0]["on_state"] if "music_native" in h["lambda"])
        self.assertIn("if (panel_music::state.remote) panel_music::artwork.restore();", handler)
        labels = {}
        def walk(node):
            if isinstance(node, dict):
                if "label" in node and isinstance(node["label"], dict) and node["label"].get("id"):
                    labels[node["label"]["id"]] = node["label"]
                for v in node.values(): walk(v)
            elif isinstance(node, list):
                for v in node: walk(v)
        walk(self.cfg["lvgl"])
        for key in ("np_caption", "np_small_title", "mp_title", "mp_artist"):
            self.assertEqual(labels[key]["long_mode"], "DOT")
            self.assertIn("height", labels[key], key)             # DOT without a height wraps instead

    def test_a_new_song_keeps_the_cover_and_only_another_source_drops_it(self):
        """The station logo blinked away at every song (2026-10-27): HA keeps a station's key, so must the panel."""
        wire = next(t for t in self.cfg["text_sensor"] if t.get("id") == "music_status")["on_value"][0]["lambda"]
        self.assertIn("panel_music::source_changed(old_source, panel_music::state.source)", wire)   # native test above
        self.assertIn("panel_music::artwork.restore();", wire)
        self.assertNotIn("state.title", wire)

    def test_stop_is_local_music_only_and_volume_has_no_chime(self):
        scripts = {s["id"]:s for s in self.cfg["script"]}
        stop = json.dumps(scripts["music_stop"])
        self.assertIn("MEDIA_PLAYER_COMMAND_STOP", stop)
        self.assertIn("set_announcement(false)", stop)
        self.assertNotIn("voice_assistant.stop", stop)
        volume = json.dumps(scripts["musicvol_set"])
        self.assertIn("set_volume", volume)
        self.assertNotIn("play_on_device", volume)
        self.assertNotIn("homeassistant.action", volume)

    def test_stop_blanks_the_row_everywhere_at_once(self):
        """Joel, 2026-10-30: stop blanks the row, on Hem too, Spotify included. HA reports a stopped Spotify idle
        (media.py), so the record is not built again as it was when HA said "paused" (2026-10-28)."""
        steps = {s["id"]:s for s in self.cfg["script"]}["music_stop"]["then"][0]["if"]["then"]
        lam = "\n".join(s["lambda"] for s in steps if "lambda" in s)
        self.assertNotIn('state.state = "paused"', lam)
        self.assertIn("panel_music::state.clear();", lam)
        self.assertIn('panel_music::artwork.select("");', lam)
        self.assertIn("id(music_render).execute();", lam)      # hides np_bar and np_bar_home alike
        self.assertIn("if (!panel_music::state.remote)\n  id(panel_media)->make_call().set_command(media_player::MEDIA_PLAYER_COMMAND_STOP)", lam)
        scripts = {s["id"]: s for s in self.cfg["script"]}
        on_error = json.dumps(scripts["music_send_stop"]["then"][0]["homeassistant.action"]["on_error"])
        self.assertIn("music_control_recover",on_error)
        self.assertIn("reported.wire(id(music_status).state)",json.dumps(scripts["music_control_recover"]))

    def test_buttons_do_not_dim_while_a_press_waits(self):
        """Every button dimmed until Home Assistant answered and came back: a blink at each press (2026-10-28).
        A second press is still ignored by the scripts' condition."""
        render = json.dumps(self.cfg["script"])
        self.assertNotIn("&& !panel_music::pending.waiting)", render)
        self.assertNotIn("enabled(id(np_toggle), !panel_music::pending.waiting)", render)
        scripts = {s["id"]: s for s in self.cfg["script"]}
        for action in ("toggle", "next", "previous"):     # stop may repeat: it is the same stop again
            condition = json.dumps(scripts["music_" + action]["then"][-1]["if"]["condition"])
            self.assertIn("!panel_music::pending.waiting", condition, action)

    def test_timeout_and_refusal_share_local_recovery_without_retrying_the_command(self):
        scripts={s['id']:s for s in self.cfg['script']}
        recovery=json.dumps(scripts['music_control_recover'])
        self.assertNotIn('homeassistant.action',recovery)
        self.assertIn('reported.remote && reported.active()',recovery)
        self.assertIn('picture_identity()',recovery)  # unconfirmed speculative next cover also returns
        timeout=next(i for i in self.cfg['interval'] if 'pending.tick' in json.dumps(i))
        self.assertIn('music_control_recover',json.dumps(timeout))
        for action in ('toggle','stop','next','previous'):
            failed=scripts['music_send_'+action]['then'][0]['homeassistant.action']['on_error'][0]['if']
            self.assertIn('pending.current(ticket)',failed['condition']['lambda'])
            self.assertIn('music_control_recover',json.dumps(failed['then']))

    def test_the_speaker_chip_names_the_speaker(self):
        """Joel, 2026-10-28: "JBL Charge 4", its own name from Home Assistant, not "JBL-högtalaren"."""
        render = self.cfg_script_lambda("music_render")
        self.assertIn('m.place.empty() ? "JBL-högtalaren" : m.place', render)
        self.assertIn("panel_music_ui::text(id(mp_out_fixed_lbl), speaker.c_str());", render)
        self.assertIn('"Spotify spelar via Jarvis på " + speaker', render)

    def cfg_script_lambda(self, name):
        script = {s["id"]: s for s in self.cfg["script"]}[name]
        return next(step["lambda"] for step in script["then"] if "lambda" in step)

    def test_idle_layout_stays_hidden_and_backend_errors_are_visible(self):
        def walk(value):
            if isinstance(value, dict):
                if "id" in value: yield value
                for child in value.values(): yield from walk(child)
            elif isinstance(value, list):
                for child in value: yield from walk(child)
        widgets = {v["id"]:v for v in walk(self.cfg["lvgl"])}
        self.assertTrue(widgets["np_bar"]["hidden"])
        self.assertNotIn("np_overlay", widgets)          # no sheet any more: the row opens Spelas nu (2026-10-28)
        self.assertEqual((widgets["np_bar"]["x"], widgets["np_bar"]["y"]), (32,204))
        # Reproduced on the panel: the default clickable clipping object stole
        # thumbnail taps instead of allowing the row's music_open action.
        for key in ("np_small_art_frame", "np_small_art_frame_home", "mp_art_frame"):
            self.assertFalse(widgets[key]["clickable"])
            self.assertFalse(widgets[key+"_image"]["clickable"])
        # The row is on Gå hemifrån and Hemmet (2026-10-28), at the same place; a tap opens the Spelas nu tab.
        pages = {p["id"]: json.dumps(p) for p in self.cfg["lvgl"]["pages"]}
        self.assertIn('"np_bar"', pages["page_glass_v2"])
        self.assertIn('"np_bar_home"', pages["page_hemmet"])
        for key, y in (("np_bar", 204), ("np_bar_home", 168)):
            self.assertTrue(widgets[key]["hidden"])
            self.assertEqual((widgets[key]["x"], widgets[key]["y"]), (32, y))
            self.assertEqual(widgets[key]["on_click"], [{"script.execute": "music_open"}])
        music_open = json.dumps(next(s for s in self.cfg["script"] if s["id"] == "music_open"))
        self.assertIn('"script.execute": "tab_go"', music_open)
        self.assertIn("id(tab_target) = 2;", music_open)                 # SWIPE_ORDER: Spelas nu is the third
        self.assertNotIn("np_overlay", json.dumps(self.cfg))
        for name in ("music_send_toggle", "music_send_next", "music_send_previous"):
            script = next(s for s in self.cfg["script"] if s["id"]==name)
            action = script["then"][0]["homeassistant.action"]
            self.assertEqual(action["action"], "jarvis_agent.media_control")
            self.assertIn("on_error", action)
            self.assertIn("mp_error", json.dumps(action["on_error"]))
