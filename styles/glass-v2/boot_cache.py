"""Bounded, visibly stale weather/calendar startup snapshot; no restored HA state."""
from pathlib import Path
import gen

CACHED = ('hm_w_temp', 'hm_w_line', 'hm_w_tip', 'hm_w_icon', 'hm_cal_head', 'hm_cal0', 'hm_cal1', 'hm_cal2',
          'hm_w_days')
COHORT = ('hm_w_temp', 'hm_w_line', 'hm_w_tip', 'hm_w_icon', 'hm_sl_walk',
          'hm_sl0', 'hm_sl1', 'hm_sl2', 'hm_sl3', 'hm_cal_head', 'hm_cal0', 'hm_cal1', 'hm_cal2', 'hm_w_days')
ALL_SEEN = (1 << len(COHORT)) - 1     # boot_seen once every field of the cohort has arrived


def install(cfg):
    if not gen.BOARD:
        return
    cfg['esphome'].setdefault('includes', []).extend(
        str(Path(__file__).resolve().parents[2] / 'bench' / name)
        for name in ('boot_cache.h', 'boot_cache_esphome.h'))
    cfg['globals'].extend([
        *[{'id': name, 'type': 'bool', 'initial_value': 'false'} for name in
          ('boot_cache_active', 'boot_cache_available', 'boot_cache_used', 'boot_live_ready', 'boot_ha_connected')],
        {'id': 'boot_live_ms', 'type': 'uint32_t', 'initial_value': '0'},
        {'id': 'boot_seen', 'type': 'uint32_t', 'initial_value': '0'},
        {'id': 'boot_ntp_ms', 'type': 'uint32_t', 'initial_value': '0'},
    ])
    # The existing HA clock reads the common RTC; local-router SNTP can set it
    # before the HA client reconnects. It never trusts a saved timestamp as now.
    cfg['time'].append({'platform': 'sntp', 'id': 'boot_time', 'timezone': 'Europe/Stockholm',
                        'servers': ['router.example.invalid'], 'update_interval': '6h',
                        'on_time_sync': [{'lambda': 'if (!id(boot_ntp_ms)) id(boot_ntp_ms) = millis();'},
                                         {'script.execute': 'clock_update'}, {'script.execute': 'boot_cache_refresh'}]})
    sensor_map = {s['id']: s for s in cfg['text_sensor']}
    render = {}
    for index, name in enumerate(COHORT):
        sid = 'boot_render_' + name
        render[name] = sid
        actions = sensor_map[name]['on_value']
        cfg['script'].append({'id': sid, 'mode': 'single', 'parameters': {'x': 'string'}, 'then': actions})
        sensor_map[name]['on_value'] = [
            {'lambda': f'id(boot_seen) |= uint32_t(1) << {index};'},
            {'if': {'condition': {'lambda': 'return !id(boot_cache_active);'},
                    'then': [{'lambda': f'id({sid}).execute(x);'}]}},
            {'script.execute': 'boot_cache_refresh'}]
    # Reuse the real UI formatters, not sensor.publish_state: HA freshness remains real.
    def draw(name, value):
        return {'lambda': f'id({render[name]}).execute({value.removeprefix("return ").removesuffix(";")});'}
    clear = [draw(name, 'return std::string{};') for name in COHORT]
    clear += [{'lambda': 'lv_label_set_text(id(cal_head), "Inväntar kalender");\n'
                        'lv_label_set_text(id(sl_walk), "Uppdaterar avgångar");'}]
    cached = [draw(name, f'return boot_cache::panel_cache.field({i});') for i, name in enumerate(CACHED)]
    cached += [draw(name, 'return std::string{};') for name in COHORT if name not in CACHED]
    cached += [{'lambda': 'lv_label_set_text(id(cal_head), "SPARAD KALENDER");\n'
                         'lv_label_set_text(id(sl_walk), "Uppdaterar avgångar");\n'
                         'if (!id(ha_time).now().is_valid()) { lv_label_set_text(id(clock_lbl), "--:--"); '
                         'lv_label_set_text(id(date_lbl), "Inväntar klockan"); }\n'
                         }]
    cfg['esphome'].setdefault('on_boot', []).append({'priority': 380, 'then': [
        {'lambda': 'boot_cache::panel_cache.load(esphome::global_preferences->make_preference<boot_cache::Record>(boot_cache::PREFERENCE_KEY));\n'
                   'auto t = id(ha_time).now();\n'
                   'id(boot_cache_available) = boot_cache::panel_cache.usable(t.is_valid() ? t.timestamp : 0);\n'
                   'id(boot_cache_active) = id(boot_cache_available);'},
        {'if': {'condition': {'lambda': 'return id(boot_cache_available);'}, 'then': cached}}]})
    fresh = f'id(boot_seen) == {ALL_SEEN} && id(ha_time).now().is_valid() && ' + ' && '.join(f'id({name}).has_state()' for name in COHORT)
    cfg['script'].append({'id': 'boot_cache_refresh', 'mode': 'single', 'then': [
        {'if': {'condition': {'lambda': 'return ' + fresh + ';'}, 'then': [
            {'lambda': 'id(boot_live_ready) = true;\nif (!id(boot_live_ms)) id(boot_live_ms) = millis();'},
            {'if': {'condition': {'lambda': 'return id(boot_cache_active);'}, 'then': [
                {'lambda': 'id(boot_cache_active) = false;'},
                *[draw(name, f'return id({name}).state;') for name in COHORT]]}}], 'else': [
            {'if': {'condition': {'lambda': 'return id(boot_cache_active) && id(boot_cache_available) && '
                                                   'id(ha_time).now().is_valid() && '
                                                   '!boot_cache::panel_cache.usable(id(ha_time).now().timestamp);'},
                    'then': [{'lambda': 'id(boot_cache_available) = false;'}, *clear]}}]}}]})
    cfg.setdefault('interval', []).extend([
        {'interval': '1s', 'then': [{'if': {'condition': {'lambda': 'return !id(boot_live_ready);'},
                                        'then': [{'script.execute': 'boot_cache_refresh'}]}}]},
        {'interval': '60s', 'startup_delay': '70s', 'then': [{'if': {
            'condition': {'lambda': f'return id(boot_live_ready) && id(boot_ha_connected) && id(boot_seen) == {ALL_SEEN} && !id(ha_lost) && id(ha_time).now().is_valid();'},
            'then': [{'lambda': 'auto t = id(ha_time).now();\n'
                      'boot_cache::panel_cache.save(t.timestamp, millis(), t.strftime("%Y-%m-%d %H:%M"), { '
                      + ', '.join(f'id({name}).state' for name in CACHED) + ' });'}]}}]},
    ])
