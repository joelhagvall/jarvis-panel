"""The latest chat messages, so the panel can show the chat again after a reboot, and the home for the
panel's Hemmet page."""

from datetime import timedelta
import json
import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import HISTORY_LIMIT, HOME_INTERVAL_S, SIGNAL_NEW_MESSAGE, SIGNAL_WORKING
from .entity import jarvis_device
from . import chat_view, moon
from .media import JarvisMedia
from .wakeup import LABELS, describe, panel_line

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=60)
CHAT_FRESH = timedelta(minutes=10)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Add the chat sensor and the home sensor."""
    entry.runtime_data.media = JarvisMedia(entry)
    async_add_entities([JarvisChat(entry), JarvisHome(entry), JarvisWakeup(entry), entry.runtime_data.media,
                        JarvisMoon(entry), JarvisMoonEvent(entry, "full"), JarvisMoonEvent(entry, "new")],
                       update_before_add=True)


# The moon (moon.py, Joel 2026-10-28): its phase for Jarvis's "manen" tool and the dashboard, and the next full and
# new moon as timestamps. Computed every minute, no service.
MOON_ICONS = {"Nymåne": "mdi:moon-new", "Tilltagande månskära": "mdi:moon-waxing-crescent",
              "Halvmåne, tilltagande": "mdi:moon-first-quarter", "Tilltagande måne": "mdi:moon-waxing-gibbous",
              "Fullmåne": "mdi:moon-full", "Avtagande måne": "mdi:moon-waning-gibbous",
              "Halvmåne, avtagande": "mdi:moon-last-quarter", "Avtagande månskära": "mdi:moon-waning-crescent"}


class JarvisMoon(SensorEntity):
    _attr_name = "Jarvis månen"
    _attr_should_poll = True

    def __init__(self, entry):
        self._attr_unique_id = f"{entry.entry_id}-moon"
        self._attr_device_info = jarvis_device(entry)

    async def async_update(self):
        state = moon.now()
        self._attr_native_value = state["phase"]
        self._attr_icon = MOON_ICONS.get(state["phase"], "mdi:moon-full")
        self._attr_extra_state_attributes = {
            "illumination": state["illumination"], "age_days": state["age_days"],
            "next_full": state["next_full"].isoformat(timespec="minutes"),
            "next_new": state["next_new"].isoformat(timespec="minutes")}


class JarvisMoonEvent(SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_should_poll = True

    def __init__(self, entry, kind):
        self._kind = kind
        self._attr_name = "Jarvis nästa fullmåne" if kind == "full" else "Jarvis nästa nymåne"
        self._attr_icon = "mdi:moon-full" if kind == "full" else "mdi:moon-new"
        self._attr_unique_id = f"{entry.entry_id}-moon-{kind}"
        self._attr_device_info = jarvis_device(entry)

    async def async_update(self):
        now = dt_util.utcnow()
        self._attr_native_value = (moon.next_full if self._kind == "full" else moon.next_new)(now)


class JarvisWakeup(SensorEntity):
    _attr_name = "Jarvis nästa väckning"
    _attr_icon = "mdi:alarm"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_should_poll = False

    def __init__(self, entry):
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-wakeup"
        self._attr_device_info = jarvis_device(entry)

    async def async_added_to_hass(self):
        self.async_on_remove(async_track_time_interval(self.hass,
            self._refresh, timedelta(seconds=15)))

    @callback
    def _refresh(self, _now):
        self.async_schedule_update_ha_state(True)

    async def async_update(self):
        controller = self._entry.runtime_data.wakeups
        upcoming = controller.next_plans()
        self._attr_native_value = upcoming[0].due if upcoming else None
        try:
            status = await self._entry.runtime_data.wakeup("status")
            self._attr_available = True
        except HomeAssistantError:
            self._attr_available = False
            return
        self._attr_extra_state_attributes = {"status": controller.status(),
            "klocka_aktiv": controller.alarm_at is not None,
            "alarmtid": dt_util.utc_from_timestamp(controller.alarm_at).isoformat() if controller.alarm_at is not None else None,
            "morgontyp": LABELS.get(upcoming[0].kind, "Vanlig morgon") if upcoming else "",
            # The dashboard's headline; "" when nothing is scheduled (then status says why).
            "text": describe(upcoming[0], dt_util.now()) if upcoming else "",
            # The panel's Natt page ("Väckning 08:45"); the next one still ahead, not one that plays now.
            "panel": panel_line(next((p for p in upcoming if p.due > dt_util.now()), None), dt_util.now()),
            "pagar": controller.active(),
            "reserver_klara": status.get("reserves_ready", []),
            "senast_forberedd": status.get("latest", {}).get("due"),
            "manus": status.get("latest", {}).get("writer")}


class JarvisChat(SensorEntity):
    """State: when the last message was said. Attributes: the latest messages, for screens only.

    last_said and last_reply, the panel's two bubbles, only hold messages from the last CHAT_FRESH
    (10 minutes), so a guest in the hallway does not read old questions; read every minute, so a bubble
    goes within a minute of that. last_card is the cards last_reply showed on the voice page, or ""
    (the server's agents/screen.py), and working what Jarvis said before his tools while he still works,
    "" once the reply is in. earlier is what came before them in the same window (chat_view.py), so the panel's
    chat scrolls back through the conversation. `messages` keeps them all.

    `messages` is a JSON string (not a list) so ESPHome can read it as one text attribute. The
    attributes are kept out of Home Assistant's recorder: the history already lives on Jarvis.
    """

    _attr_name = "Jarvis chat"
    _attr_icon = "mdi:chat-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({"messages", "last_said", "last_reply", "last_card", "working", "earlier"})

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-chat"
        self._attr_device_info = jarvis_device(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_NEW_MESSAGE, self._new_message))
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_WORKING, self._working))

    @callback
    def _new_message(self) -> None:
        """Runs in the event loop (a plain lambda would run in a worker thread)."""
        self.async_schedule_update_ha_state(True)

    @callback
    def _working(self, text: str) -> None:
        """Jarvis has said what he checks and runs his tools: show it at once, without asking the server."""
        self._attr_extra_state_attributes = {**(getattr(self, "_attr_extra_state_attributes", None) or {}),
                                             "working": text}
        self.async_write_ha_state()

    async def async_update(self) -> None:
        try:
            messages = await self._entry.runtime_data.history(HISTORY_LIMIT)
        except HomeAssistantError as err:
            _LOGGER.debug("No chat history from Jarvis: %s", err)
            self._attr_available = False
            return
        self._attr_available = True
        self._attr_native_value = dt_util.parse_datetime(messages[-1]["ts"]) if messages else None
        now = dt_util.utcnow()
        fresh = [m for m in messages if now - dt_util.parse_datetime(m["ts"]) < CHAT_FRESH]
        said = [m["text"] for m in fresh if m["role"] == "user"]
        replies = [m for m in fresh if m["role"] != "user"]
        self._attr_extra_state_attributes = {
            "messages": json.dumps([{"ts": m["ts"], "role": m["role"], "text": m["text"]} for m in messages],
                                   ensure_ascii=False),
            "last_said": said[-1] if said else "",
            "last_reply": replies[-1]["text"] if replies else "",
            "last_card": replies[-1].get("card") or "" if replies else "",
            "earlier": chat_view.earlier(fresh),     # the bubbles above them, to scroll back to
            "working": "",
        }


class JarvisHome(SensorEntity):
    """State: one sentence about the home ("Allt lugnt hemma."). Attributes: short strings for the panel
    (door, inside, pm25, air, purifier, smoke, water, laundry, sun, moon, outside, reminders, online, alert, and
    art_title, art_credit, art_text and the Earth view's earth_*/neo* for Tavla, and the dashboard's weather w_*, metro sl0..sl3 and sl_note and calendar
    cal_head, cal0..cal2, the calendar page's cal_week, the Jarvis card's next_up, Hemmet's house line and its
    reminder page's rem_list),
    from the server's GET /panel. Read every 15 s on its own clock, so the chat sensor keeps its minute.
    The attributes are kept out of the recorder: they change all the time and only the panel needs them.
    """

    _attr_name = "Jarvis hemmet"
    _attr_icon = "mdi:home-heart"
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"alert", "door", "door_since", "inside", "pm25", "air", "purifier", "smoke",
                                        "smoke_on", "water", "water_on", "laundry", "sun", "moon", "outside", "reminders",
                                        "online", "art_title", "art_credit", "art_text", "earth_title",
                                        "earth_line", "earth_version", "neo_head", "neo0", "neo1", "neo2",
                                        "laundry_rooms", "summary",
                                        "w_temp", "w_line", "w_tip", "w_icon", "w_days", "wx_sky", "wx_sub",
                                        "wx_wet", "wx_facts", "wx_hours", "wx_week", "sl0", "sl1", "sl2", "sl3", "sl_walk", "sl_note",
                                        "cal_head", "cal0", "cal1", "cal2", "cal_week", "bg_version", "next_up",
                                        "house", "rem_list"})

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-home"
        self._attr_device_info = jarvis_device(entry)

    async def async_added_to_hass(self) -> None:
        async def tick(_now) -> None:
            await self.async_update()
            self.async_write_ha_state()

        self.async_on_remove(async_track_time_interval(self.hass, tick, timedelta(seconds=HOME_INTERVAL_S)))

    async def async_update(self) -> None:
        try:
            panel = await self._entry.runtime_data.panel()
        except (HomeAssistantError, ValueError) as err:
            _LOGGER.debug("No home status from Jarvis: %s", err)
            self._attr_available = False
            return
        self._attr_available = True
        self._attr_native_value = str(panel.pop("status", "") or "")[:255]
        self._attr_extra_state_attributes = {k: str(v) for k, v in panel.items()}
