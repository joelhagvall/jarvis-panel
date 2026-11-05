"""Run generated routes with synchronous LVGL load events, which exposed skipped tabs."""
import importlib.util
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('glass_navigation', ROOT / 'styles/glass-v2/gen.py')
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


class NavigationTest(unittest.TestCase):
    def setUp(self):
        cfg = {'script': [], 'globals': [], 'esphome': {},
               'lvgl': {'pages': [{'id': name} for name in gen.SWIPE_ORDER],
                        'top_layer': {'widgets': []}}}
        gen.shared_bar(cfg)
        self.routes = {script['id']: script['then'] for script in cfg['script']}
        self.bar = cfg['lvgl']['top_layer']['widgets'][0]['obj']

    def test_tab_bar_receives_child_gestures_instead_of_bubbling_past_its_handlers(self):
        self.assertIs(self.bar['gesture_bubble'], False)
        for direction in ('left', 'right'):
            actions = self.bar['on_swipe_' + direction]
            self.assertEqual(actions[0], gen.SWIPE_NO_TAP)
            self.assertEqual(actions[1], {'script.execute': 'swipe_' + direction})

    def execute(self, actions, state):
        def condition(value):
            code = value['lambda']
            if code == 'return id(night);': return state['night']
            match = re.fullmatch(r'return id\(cur_page\) == (\d+);', code)
            self.assertIsNotNone(match, code)
            return state['page'] == int(match[1])
        for action in actions:
            if 'if' in action:
                branch = action['if']
                self.execute(branch.get('then' if condition(branch['condition']) else 'else', []), state)
            elif 'lvgl.page.show' in action:
                target = action['lvgl.page.show']['id']
                state['moves'].append(target)
                # LV_EVENT_SCREEN_LOAD_START updates cur_page inside page.show.
                state['page'] = gen.SWIPE_ORDER.index(target)
            elif action.get('script.execute') == 'night_on':
                state['moves'].append(gen.NIGHT)
                state['page'] = gen.SWIPE_ORDER.index(gen.NIGHT)
                state['night'] = True
            elif action.get('script.execute') == 'night_hw_off':
                state['night_hw_off'] += 1
            elif 'lambda' in action:
                self.assertEqual(action['lambda'], 'id(night) = false;')
                state['night'] = False
            else:
                self.fail(f'Unmodeled route action: {action}')

    def test_one_gesture_moves_one_tab_from_every_page_and_never_wraps(self):
        for direction, delta in (('left', 1), ('right', -1)):
            for page in range(len(gen.SWIPE_ORDER)):
                with self.subTest(direction=direction, page=page):
                    night = gen.SWIPE_ORDER.index(gen.NIGHT)
                    state = {'page': page, 'night': page == night, 'night_hw_off': 0, 'moves': []}
                    self.execute(self.routes['swipe_' + direction], state)
                    target = page + delta
                    if 0 <= target < len(gen.SWIPE_ORDER):
                        self.assertEqual(state['moves'], [gen.SWIPE_ORDER[target]])
                        self.assertEqual(state['page'], target)
                    else:
                        self.assertEqual(state['moves'], [])
                        self.assertEqual(state['page'], page)
                    if page == night and direction == 'right':
                        self.assertFalse(state['night'])
                        self.assertEqual(state['night_hw_off'], 1)

    def test_next_gesture_starts_from_incoming_page_before_animation_finishes(self):
        state = {'page': 0, 'night': False, 'night_hw_off': 0, 'moves': []}
        self.execute(self.routes['swipe_left'], state)
        self.execute(self.routes['swipe_left'], state)
        self.assertEqual(state['moves'], [gen.HOME, gen.MUSIC])
        self.execute(self.routes['swipe_right'], state)
        self.execute(self.routes['swipe_right'], state)
        self.assertEqual(state['moves'], [gen.HOME, gen.MUSIC, gen.HOME, gen.DASH])


if __name__ == '__main__':
    unittest.main()
