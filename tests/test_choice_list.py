#!/usr/bin/env python3
"""Inställningar's lists (Bakgrund, Ljud ut): the C++ state and the generated dropdowns and pick scripts.

  python3 -m unittest tests/test_choice_list.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ChoiceListTest(unittest.TestCase):
    def test_entries_come_one_per_line_and_are_capped_at_the_pick_scripts(self):
        source = r'''
#include "styles/glass-v2/choice_list.h"
#include <cassert>
int main() {
  choice_list::List l;
  assert(l.joined() == "–" && l.index() == -1);
  assert(l.options("Panelen\nJBL Charge 4")); assert(!l.options("Panelen\nJBL Charge 4"));
  l.current = "JBL Charge 4"; assert(l.index() == 1);
  assert(l.options("Panelen\n\nRoam\n")); assert(l.joined() == "Panelen\nRoam" && l.index() == -1);
  assert(l.options("a\nb\nc\nd\ne\nf\ng\nh\ni\nj")); assert(l.names.size() == choice_list::MAX);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            cpp = Path(tmp) / "list.cpp"; cpp.write_text(source); binary = Path(tmp) / "list"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT), str(cpp), "-o", str(binary)],
                           check=True)
            subprocess.run([str(binary)], check=True)

    def test_each_entry_picks_home_assistants_own_option_by_index(self):
        env = {**os.environ, "HALLPANEL_BOARD": "1"}
        cfg = json.loads(subprocess.run([sys.executable, str(ROOT / "styles/glass-v2/gen.py")], cwd=ROOT / "styles/glass-v2",
                                        env=env, capture_output=True, text=True, check=True).stdout)
        scripts = {s["id"]: s for s in cfg["script"]}
        for key, entity in (("bg", "select.jarvis_bakgrund"), ("out", "select.jarvis_ljud_ut")):
            for i in range(8):
                action = scripts[f"{key}_send_{i}" if key=='out' else f"{key}_pick_{i}"]["then"][0]["homeassistant.action"]
                self.assertEqual(action["data_template"]["option"],
                                 f"{{{{ state_attr('{entity}', 'panel_options').split('\\n')[{i}] }}}}")
            self.assertNotIn(f"{key}_pick_8", scripts)
            dump = json.dumps(cfg["lvgl"])
            self.assertIn(f'"id": "s_{key}"', dump)
            self.assertIn(f"case 7: id({key}_pick_7).execute()", dump)
        sensors = {t["id"]: t for t in cfg["text_sensor"]}
        self.assertEqual(sensors["out_options"]["attribute"], "panel_options")
        self.assertIn('constexpr std::size_t MAX = 8;', (ROOT / "styles/glass-v2/choice_list.h").read_text())
        # Home Assistant offers the panel only as many entries as it has pick scripts.
        self.assertIn("PANEL_ENTRIES = 8 ", (ROOT / "ha/custom_components/jarvis_agent/select.py").read_text())

    def test_the_current_choice_is_in_the_panels_list_and_picks_itself(self):
        """An uploaded picture is option 9: the list showed NASA as chosen (2026-11-03). It takes the last place, and
        the pick template reads the same list, rendered here with Jinja as Home Assistant does."""
        import importlib.util, types
        stubs = {"homeassistant": None, "homeassistant.components": None, "homeassistant.components.select": ["SelectEntity"],
                 "homeassistant.config_entries": ["ConfigEntry"], "homeassistant.core": ["HomeAssistant"],
                 "homeassistant.exceptions": ["HomeAssistantError", "ServiceValidationError"],
                 "homeassistant.helpers": None, "homeassistant.helpers.entity_platform": ["AddConfigEntryEntitiesCallback"]}
        saved = {name: sys.modules.get(name) for name in stubs}
        try:
            for name, attrs in stubs.items():
                module = types.ModuleType(name)
                for attr in attrs or ():
                    setattr(module, attr, type(attr, (Exception,) if "Error" in attr else (), {}))
                sys.modules[name] = module
            package = types.ModuleType("jarvis_select_test"); package.__path__ = []
            sys.modules["jarvis_select_test"] = package
            entity = types.ModuleType("jarvis_select_test.entity"); entity.jarvis_device = lambda entry: None
            sys.modules["jarvis_select_test.entity"] = entity
            spec = importlib.util.spec_from_file_location("jarvis_select_test.select",
                                                          ROOT / "ha/custom_components/jarvis_agent/select.py")
            select = importlib.util.module_from_spec(spec); spec.loader.exec_module(select)
        finally:
            for name, module in saved.items():
                if module is None: sys.modules.pop(name, None)
                else: sys.modules[name] = module
        options = ["NASA", "Växlande", "Stjärnenatten", "Mandelblom", "Dagens målning", "Dagens foto", "Bing",
                   "NASA Jordbild", "opo0501a"]
        shown = select.panel_options(options, "opo0501a")["panel_options"].split("\n")
        self.assertEqual(shown, options[:7] + ["opo0501a"])
        self.assertEqual(select.panel_options(options, "Bing")["panel_options"].split("\n"), options[:8])
        self.assertEqual(select.panel_options(["Panelen"], None)["panel_options"], "Panelen")
        sys.path.insert(0, str(ROOT / "styles/glass-v2"))
        spec = importlib.util.spec_from_file_location("gen_pick_test", ROOT / "styles/glass-v2/gen.py")
        gen = importlib.util.module_from_spec(spec); sys.modules["gen_pick_test"] = gen; spec.loader.exec_module(gen)
        try:
            import jinja2
        except ImportError:
            self.skipTest("jinja2 not installed")
        attrs = select.panel_options(options, "opo0501a")
        for i, want in ((0, "NASA"), (7, "opo0501a")):
            rendered = jinja2.Environment().from_string(gen.pick_template("select.jarvis_bakgrund", i)).render(
                state_attr=lambda entity, key: attrs[key])
            self.assertEqual(rendered, want)

    def test_koppla_om_presses_home_assistants_reconnect_button(self):
        """Koppla om beside Ljud ut (2026-11-01): the panel presses the button entity the integration adds."""
        env = {**os.environ, "HALLPANEL_BOARD": "1"}
        cfg = json.loads(subprocess.run([sys.executable, str(ROOT / "styles/glass-v2/gen.py")], cwd=ROOT / "styles/glass-v2",
                                        env=env, capture_output=True, text=True, check=True).stdout)
        script = {s["id"]: s for s in cfg["script"]}["out_reconnect"]
        self.assertNotIn('35s',json.dumps(script))
        send=next(s for s in cfg['script'] if s['id']=='out_reconnect_send')
        action=send['then'][0]['homeassistant.action']
        self.assertEqual((action['action'],action['data']),('button.press',{'entity_id':'button.jarvis_koppla_om_hogtalaren'}))
        self.assertEqual(send['parameters'],{'ticket':'uint32_t'})
        self.assertIn('acknowledge(ticket',json.dumps(action['on_success']))
        self.assertIn('fail(ticket',json.dumps(action['on_error']))
        dump = json.dumps(cfg["lvgl"])
        self.assertIn('"id": "s_out_reconnect"', dump)
        self.assertNotIn('"id": "s_bg_reconnect"', dump)
        button = (ROOT / "ha/custom_components/jarvis_agent/button.py").read_text()
        self.assertIn('_attr_name = "Jarvis koppla om högtalaren"', button)   # -> button.jarvis_koppla_om_hogtalaren
        self.assertIn("Platform.BUTTON", (ROOT / "ha/custom_components/jarvis_agent/__init__.py").read_text())


if __name__ == "__main__":
    unittest.main()
