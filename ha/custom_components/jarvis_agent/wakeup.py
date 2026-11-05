"""HA-owned wakeup schedules, from native helpers. Preparation never blocks the alarm clock."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from aioesphomeapi import MediaPlayerCommand
from homeassistant.components import media_player
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change, async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

TZ = ZoneInfo("Europe/Stockholm")
PREFIX = "jarvis_vackning_"
DAYS = ("mandag", "tisdag", "onsdag", "torsdag", "fredag", "lordag", "sondag")
# "Egen väckning" (only the one-off offers it) is Joel's free text in input_text.jarvis_vackning_engang_text.
KINDS = {"Vanlig morgon": "normal", "Kontorsmorgon": "office", "Egen väckning": "custom"}
WISH = "input_text." + PREFIX + "engang_text"
PLAYER = "media_player.hallpanel_hallpanel"
SATELLITE = "assist_satellite.hallpanel_assist_satellite"
NIGHT_END = "time.hallpanel_natt_slutar"
NIGHT_START = "time.hallpanel_natt_borjar"
SNOOZE_WINDOW = 30 * 60
# Outcomes of a finished wakeup. They stay visible during the snooze window, then the status
# shows the armed clock again instead of an old "stopped" until the next alarm has played.
FINISHED = ("Redo", "Väckning klar", "Väckning stoppad", "Väckningen är redan hanterad")
_LOGGER = logging.getLogger(__name__)
CONTROLS = (["input_boolean." + PREFIX + x for x in ("engang", "snooze", *DAYS)] +
            ["input_datetime." + PREFIX + x for x in ("engang", "tid", "snooze", *(day + "_tid" for day in DAYS))] +
            ["input_select." + PREFIX + x for x in ("engang_typ", "typ", *(day + "_typ" for day in DAYS))] +
            ["input_number." + PREFIX + "volym", WISH])
WEEKDAYS = ("måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag")
MONTHS = ("jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec")
LABELS = {kind: label for label, kind in KINDS.items()}
NOTHING = "Ingen väckning – inga dagar valda"
# Settings worth a log line when they change; CONTROLS are those that also reschedule.
SETTINGS = CONTROLS + ["input_select." + PREFIX + "radio"]
# The permanent wakeup log, one Swedish line per event; never rotated or pruned by HA (docs/wakeups.md).
LOG_PATH = ("jarvis_logs", "vackning.log")
SOURCES = {"weekly": "vecka", "oneoff": "engång", "snooze": "snooze", "preview": "provlyssning"}
WRITERS = {"claude": "förberedd text (Sonnet)", "template": "förberedd faktamall", "reserve": "reservhälsning"}
FACTS = {"weather": "väder", "calendar": "kalender", "sunrise": "soluppgång"}


def log_line(when, text):
    """One log line: local time, two spaces, the event on one line."""
    return f"{when.astimezone(TZ):%Y-%m-%d %H:%M:%S}  {' '.join(str(text).split())}"


def plan_text(plan):
    due = plan.due.astimezone(TZ)
    return (f"{WEEKDAYS[due.weekday()]} {due.day} {MONTHS[due.month - 1]} {due:%H:%M} · "
            f"{LABELS.get(plan.kind, 'Vanlig morgon')} ({SOURCES.get(plan.source, plan.source)})")


def append_lines(path, lines):
    """Runs in HA's executor, never in the event loop."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:
        log.write("".join(line + "\n" for line in lines))


@dataclass(frozen=True)
class Plan:
    due: datetime
    kind: str
    source: str
    wish: str = ""


def plans(states, now, last_kind="normal", last_wish=""):
    """Next occurrences, including a two-minute restart catch-up, with one alarm per instant."""
    now = now.astimezone(TZ)
    def value(domain, name):
        state = states.get(domain + "." + PREFIX + name)
        return state.state if state is not None else "unknown"
    found = []
    for source, field in (("oneoff", "engang"), ("snooze", "snooze")):
        if value("input_boolean", field) != "on":
            continue
        try:
            due = datetime.fromisoformat(value("input_datetime", field)).replace(tzinfo=TZ)
            kind, wish = ((last_kind, last_wish) if source == "snooze" else
                          (KINDS[value("input_select", "engang_typ")], " ".join(value("input_text", "engang_text").split())))
            if kind != "custom" or wish in ("", "unknown", "unavailable"):
                kind, wish = ("normal" if kind == "custom" else kind), ""      # an empty wish is a normal morning
            found.append(Plan(due, kind, source, wish))
        except (ValueError, KeyError):
            pass
    # Each day that is on wakes at its own time with its own content. A day without its own helper
    # uses the common fallbacks (jarvis_vackning_tid / _typ); there is no master switch.
    def clock(text):
        return datetime.strptime(text[:5], "%H:%M").time()
    for offset in range(8):
        day = now.date() + timedelta(days=offset)
        name = DAYS[day.weekday()]
        if value("input_boolean", name) != "on":
            continue
        own = value("input_datetime", name + "_tid")
        try:
            day_clock = clock(own if own not in ("unknown", "unavailable", "") else value("input_datetime", "tid"))
        except ValueError:
            continue
        kind = KINDS.get(value("input_select", name + "_typ")) or KINDS.get(value("input_select", "typ"), "normal")
        found.append(Plan(datetime.combine(day, day_clock, TZ), kind, "weekly"))
    # Snooze wins, then an explicit one-off. Fold/nonexistent DST instants are checked in UTC.
    unique = {}
    priority = {"snooze": 0, "oneoff": 1, "weekly": 2}
    for plan in sorted(found, key=lambda p: (p.due.timestamp(), priority[p.source])):
        if (plan.due.timestamp() >= now.timestamp() - 120 and
                datetime.fromtimestamp(plan.due.timestamp(), TZ).replace(fold=plan.due.fold) == plan.due):
            unique.setdefault(plan.due.timestamp(), plan)
    return list(unique.values())


def payload(plan):
    """The server request for a plan; the wish only for an own wakeup, so other requests stay as before."""
    return {"due": plan.due.isoformat(), "kind": plan.kind, **({"wish": plan.wish} if plan.wish else {})}


def describe(plan, now):
    """The next wakeup as one Swedish line for the dashboard, e.g. "Imorgon, torsdag 29 okt 08:45 · Vanlig morgon"."""
    due, today = plan.due.astimezone(TZ), now.astimezone(TZ).date()
    day = WEEKDAYS[due.weekday()]
    when = f"{day} {due.day} {MONTHS[due.month - 1]} {due:%H:%M}"
    ahead = (due.date() - today).days
    when = ("Idag, " + when) if ahead == 0 else ("Imorgon, " + when) if ahead == 1 else when[0].upper() + when[1:]
    extra = {"oneoff": " · extra väckning", "snooze": " · snooze"}.get(plan.source, "")
    return f"{when} · {LABELS.get(plan.kind, 'Vanlig morgon')}{extra}"


SHORT_DAYS = ("mån", "tis", "ons", "tors", "fre", "lör", "sön")


def panel_line(plan, now):
    """The Natt page's short line (2026-11-02): "Väckning 08:45" within a day, "Väckning mån 08:45" later,
    "Snooze till 08:50" for a pending snooze and "Ingen väckning" when nothing is scheduled."""
    if plan is None:
        return "Ingen väckning"
    due = plan.due.astimezone(TZ)
    if plan.source == "snooze":
        return f"Snooze till {due:%H:%M}"
    soon = due.timestamp() - now.timestamp() < 24 * 3600
    return f"Väckning {due:%H:%M}" if soon else f"Väckning {SHORT_DAYS[due.weekday()]} {due:%H:%M}"


class WakeupController:
    def __init__(self, hass, client, entry_id):
        self.hass, self.client = hass, client
        self.store = Store(hass, 1, "jarvis_agent_wakeup_" + entry_id)
        self.saved = {}
        self.phase = "Redo"
        self.tasks = set()
        self.unsub = []
        self.preparing, self.attempted = set(), set()
        self.play_task = None
        self.preview_active = False
        self.announcing = False
        self.alarm_unsub, self.alarm_at = None, None
        self._tick_lock = asyncio.Lock()
        config = getattr(hass, "config", None)
        self.log_path = Path(config.path(*LOG_PATH)) if config is not None else None
        self.log_lines, self._flushing, self._logged_next = [], None, ()

    async def load(self):
        self.saved = await self.store.async_load() or {}

    @callback
    def start(self, _event=None):
        self.unsub.extend([async_track_time_change(self.hass, self.tick, second=0),
                           async_track_state_change_event(self.hass, SETTINGS, self.changed)])
        self.note("Väckningsklockan startad")
        self.spawn(self.tick(), "schemaläggning")

    def start_when_ready(self):
        if self.hass.is_running:
            self.start()
        else:
            self.unsub.append(self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self.start))

    @callback
    def close(self):
        if self.alarm_unsub:
            self.alarm_unsub()
        for remove in self.unsub:
            remove()
        for task in self.tasks:
            task.cancel()

    def note(self, text, now=None):
        """A line in the permanent log. Written in order by one executor job at a time."""
        self.log_lines.append(log_line(now or dt_util.now(), text))
        if self.log_path is not None and (self._flushing is None or self._flushing.done()):
            self._flushing = self.hass.async_create_task(self._flush())

    async def _flush(self):
        while self.log_lines:
            batch, self.log_lines = self.log_lines, []
            try:
                await self.hass.async_add_executor_job(append_lines, self.log_path, batch)
            except OSError as err:
                _LOGGER.warning("Wakeup log %s not written: %s", self.log_path, err)
                return

    def spawn(self, coro, what="väckning"):
        task = self.hass.async_create_task(coro)
        self.tasks.add(task)
        def done(task):
            self.tasks.discard(task)
            if not task.cancelled() and task.exception():
                err = task.exception()
                self.phase = "Fel: " + type(err).__name__
                _LOGGER.error("Wakeup task failed: %s", type(err).__name__)
                self.note(f"Fel ({what}): {type(err).__name__}: {err}")
        task.add_done_callback(done)
        return task

    @callback
    def changed(self, event):
        self.spawn(self.on_change(event), "inställning")

    async def on_change(self, event):
        await self.log_change(event)
        await self.tick()

    async def log_change(self, event):
        data = getattr(event, "data", None) or {}
        old, new = data.get("old_state"), data.get("new_state")
        if old is None or new is None or old.state == new.state:
            return
        who, user_id = "system", getattr(getattr(event, "context", None), "user_id", None)
        if user_id:
            auth = getattr(self.hass, "auth", None)
            user = await auth.async_get_user(user_id) if auth is not None else None
            who = user.name if user is not None and user.name else user_id[:8]
        name = new.attributes.get("friendly_name") or new.entity_id
        self.note(f"Inställning: {name} ({new.entity_id}) {old.state} → {new.state}, av {who}")

    def status(self, now=None):
        now = now or dt_util.now()
        phase = self.phase
        playing = self.play_task is not None and not self.play_task.done()
        snooze = self.hass.states.get("input_boolean." + PREFIX + "snooze")
        if phase == "Snoozad i fem minuter" and not (snooze and snooze.state == "on"):
            phase = "Redo"
        if playing or phase not in FINISHED:
            return phase
        last = self.saved.get("last_due")
        if (phase != "Redo" and last and
                0 <= now.timestamp() - datetime.fromisoformat(last).timestamp() <= SNOOZE_WINDOW):
            return phase
        if self.alarm_at is not None:
            return "Schemalagd"
        return self.nothing_reason()

    def nothing_reason(self):
        switches = (self.hass.states.get("input_boolean." + PREFIX + name) for name in ("engang", "snooze", *DAYS))
        chosen = any(state is not None and state.state == "on" for state in switches)
        return "Ingen väckning schemalagd" if chosen else NOTHING

    def active(self, now=None):
        """A wakeup plays now or finished within the snooze window: Snooze and Stopp are worth showing."""
        if self.play_task is not None and not self.play_task.done() and not self.preview_active:
            return True
        snooze = self.hass.states.get("input_boolean." + PREFIX + "snooze")
        if snooze is not None and snooze.state == "on":
            return True
        last = self.saved.get("last_due")
        now = now or dt_util.now()
        return bool(last) and 0 <= now.timestamp() - datetime.fromisoformat(last).timestamp() <= SNOOZE_WINDOW

    def next_plans(self, now=None):
        return plans(self.hass.states, now or dt_util.now(), self.saved.get("last_kind", "normal"),
                     self.saved.get("last_wish", ""))

    async def save(self):
        await self.store.async_save(self.saved)

    async def service(self, domain, service, data):
        await self.hass.services.async_call(domain, service, data, blocking=True)

    async def tick(self, now=None):
        if self._tick_lock.locked():
            return
        async with self._tick_lock:
            now = (now or dt_util.now()).astimezone(TZ)
            await self.restore_night(now)
            upcoming = self.next_plans(now)
            setting = self.saved.get("night_override")
            if (setting and setting.get("due") and now.timestamp() < datetime.fromisoformat(setting["due"]).timestamp()
                    and all(p.due.isoformat() != setting["due"] for p in upcoming)):
                await self.restore_night(now, force=True)
            following = next((p for p in upcoming if p.due.timestamp() > now.timestamp()), None)
            future = following.due if following else None
            key = (following.due.isoformat(), following.kind, following.source) if following else None
            if key != self._logged_next:            # one line per change, never one per minute
                self._logged_next = key
                self.note("Nästa väckning: " + plan_text(following) if following else self.nothing_reason(), now)
            stamp = future.timestamp() if future else None
            if stamp != self.alarm_at:
                if self.alarm_unsub:
                    self.alarm_unsub()
                self.alarm_at = stamp
                self.alarm_unsub = async_track_point_in_time(self.hass, self.tick, future) if future else None
            for plan in upcoming:
                left = plan.due.timestamp() - now.timestamp()
                slot = plan.due.isoformat()
                if 0 < left <= 180 and (slot, plan.kind, plan.wish) not in self.preparing:
                    self.preparing.add((slot, plan.kind, plan.wish))
                    self.spawn(self.prepare(plan), "förberedelse")
                if (-120 <= left <= 0 and slot not in self.attempted
                        and (self.play_task is None or self.play_task.done())):
                    self.attempted.add(slot)
                    self.play_task = self.spawn(self.play(plan), "väckning")

    async def arm_night(self, due, preview=False, now=None):
        if preview:
            return
        if setting := self.saved.get("night_override"):
            if not setting.get("applied", True):
                await self.apply_night(setting, now)
            return
        first, last = self.hass.states.get(NIGHT_START), self.hass.states.get(NIGHT_END)
        if first is None or last is None:
            return
        try:
            begin = datetime.strptime(first.state[:5], "%H:%M").time()
            end = datetime.strptime(last.state[:5], "%H:%M").time()
        except ValueError:
            return
        at = due.timetz().replace(tzinfo=None)
        night = (at >= begin or at < end) if begin > end else begin <= at < end
        if not night:
            return
        restore_at = datetime.combine(due.date(), end, TZ)
        if restore_at <= due:
            restore_at += timedelta(days=1)
        now = (now or dt_util.now()).astimezone(TZ)
        off_at = due - timedelta(minutes=1)
        if off_at.timestamp() <= now.timestamp() + 2:
            # A restart may have missed the old edge. The firmware changes night mode at a future
            # minute boundary, so wait for the next one rather than leaving an alarm stuck in Natt.
            off_at = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        replacement = off_at.strftime("%H:%M:%S")
        self.saved["night_override"] = {"old": last.state, "set": replacement,
            "restore_at": restore_at.isoformat(), "preview": preview, "due": due.isoformat(), "off_at": off_at.isoformat(),
            "applied": False}
        await self.save()
        await self.apply_night(self.saved["night_override"], now)

    async def apply_night(self, setting, now=None):
        current = self.hass.states.get(NIGHT_END)
        if current and current.state not in (setting["old"], setting["set"]):
            # A manual edit after a failed attempt belongs to the user.
            self.note(f"Nattläget ändrat för hand till {current.state}; väckningen rör det inte")
            self.saved.pop("night_override", None)
            await self.save()
            return
        now = (now or dt_util.now()).astimezone(TZ)
        if datetime.fromisoformat(setting["off_at"]).timestamp() <= now.timestamp() + 2:
            off_at = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
            setting.update(off_at=off_at.isoformat(), set=off_at.strftime("%H:%M:%S"))
            await self.save()
        await self.service("time", "set_value", {"entity_id": NIGHT_END, "time": setting["set"]})
        self.note(f"Nattläget slutar {setting['set'][:5]} i stället för {setting['old'][:5]}, så att väckningen hörs")
        setting["applied"] = True
        await self.save()

    async def consume(self, plan):
        if plan.source not in ("oneoff", "snooze"):
            return
        field = "engang" if plan.source == "oneoff" else "snooze"
        state = self.hass.states.get("input_datetime." + PREFIX + field)
        if state:
            try:
                due = datetime.fromisoformat(state.state).replace(tzinfo=TZ)
            except ValueError:
                return
            if due.timestamp() == plan.due.timestamp():
                await self.service("input_boolean", "turn_off", {"entity_id": "input_boolean." + PREFIX + field})

    async def restore_night(self, now, preview=False, force=False):
        setting = self.saved.get("night_override")
        if not setting or (not force and not preview and now.timestamp() < datetime.fromisoformat(setting["restore_at"]).timestamp()):
            return
        if preview and not setting.get("preview"):
            return
        current = self.hass.states.get(NIGHT_END)
        if current and current.state == setting["set"]:
            await self.service("time", "set_value", {"entity_id": NIGHT_END, "time": setting["old"]})
            self.note(f"Nattlägets slut återställt till {setting['old'][:5]}")
        elif current:
            self.note(f"Nattlägets slut lämnat på {current.state[:5]} (ändrat efter väckningen)")
        self.saved.pop("night_override", None)
        await self.save()

    async def prepare(self, plan):
        try:
            if self.play_task is None or self.play_task.done():
                self.phase = "Förbereder morgonrapport"
            self.note("Förbereder morgonrapport: " + plan_text(plan) + (f", önskemål: {plan.wish}" if plan.wish else ""))
            await self.arm_night(plan.due)
            result = await self.client.wakeup("prepare", payload(plan)) or {}
            if result.get("preparing"):
                self.note("Förberedelsen pågår redan")
            else:
                missing = [FACTS.get(k, k) + f" ({v})" for k, v in (result.get("sources") or {}).items() if v != "ok"]
                self.note(f"Morgonrapport klar: {WRITERS.get(result.get('writer'), result.get('writer') or 'okänd')}"
                          + (", saknas: " + ", ".join(missing) if missing else ""))
            if self.play_task is None or self.play_task.done():
                self.phase = "Morgonrapport förberedd"
        except BaseException:
            self.preparing.discard((plan.due.isoformat(), plan.kind, plan.wish))
            raise

    async def play(self, plan, preview=False):
        previous = None
        volume = 0.65
        claim_started = False
        self.preview_active = preview
        try:
            self.phase = "Väntar på ledig panel"
            for waited in range(30):
                satellite, player = self.hass.states.get(SATELLITE), self.hass.states.get(PLAYER)
                if satellite and player and satellite.state == "idle" and player.state in ("idle", "off"):
                    break
                if waited == 0:
                    self.note(f"Panelen är upptagen (röst {satellite.state if satellite else 'saknas'}, "
                              f"spelare {player.state if player else 'saknas'}); väntar upp till 30 s")
                await asyncio.sleep(1)
            else:
                raise HomeAssistantError("Panelen används; väckningen avbryter inte samtalet")
            await self.arm_night(plan.due, preview)
            setting = self.saved.get("night_override")
            if not preview and setting and setting.get("off_at"):
                delay = datetime.fromisoformat(setting["off_at"]).timestamp() - dt_util.now().timestamp()
                if delay >= -2:
                    await asyncio.sleep(max(0, delay) + 2.5)
            level = self.hass.states.get("input_number." + PREFIX + "volym")
            if level:
                volume = float(level.state) / 100
            if not 0.05 <= volume <= 1:
                raise HomeAssistantError("Ogiltig väckningsvolym")
            player = self.hass.states.get(PLAYER)
            previous = player.attributes.get("volume_level") if player else None
            for _ in range(50):
                await self.service("media_player", "volume_set", {"entity_id": PLAYER, "volume_level": volume})
                await asyncio.sleep(0.2)
                player = self.hass.states.get(PLAYER)
                if player and abs(player.attributes.get("volume_level", -1) - volume) < .01:
                    break
            else:
                raise HomeAssistantError("Väckningsvolymen kunde inte bekräftas")
            claim_started = True
            result = await self.client.wakeup("claim", {**payload(plan), "preview": preview})
            if not result.get("claimed"):
                self.phase = "Väckningen är redan hanterad"
                self.note("Redan hanterad, spelas inte igen: " + plan_text(plan))
                if not preview:
                    await self.consume(plan)
                return
            if not preview:
                self.saved.update(last_due=plan.due.isoformat(), last_kind=plan.kind, last_wish=plan.wish)
                await self.save()
            self.phase = "Spelar väckning"
            self.announcing = True
            await self.service("assist_satellite", "announce", {"entity_id": SATELLITE,
                "media_id": result["media_id"], "message": result["message"], "preannounce": True})
            self.phase = "Väckning klar"
            self.note(("Provlyssning spelad: " if preview else "Spelad: ") + plan_text(plan)
                      + f", volym {round(volume * 100)} %, {WRITERS.get(result.get('writer'), result.get('writer') or 'okänd text')}")
            if not preview:
                await self.consume(plan)
                await self.radio_after()
        except asyncio.CancelledError:
            self.phase = "Väckning stoppad"
            self.note("Väckningen avbröts: " + plan_text(plan))
            raise
        except Exception:
            if not preview and not claim_started:
                self.attempted.discard(plan.due.isoformat())
            raise
        finally:
            self.announcing = False
            if preview:
                current = self.hass.states.get(PLAYER)
                if isinstance(previous, (int, float)) and current and abs(current.attributes.get("volume_level", -1) - volume) < .01:
                    await self.service("media_player", "volume_set", {"entity_id": PLAYER, "volume_level": previous})
                await self.restore_night(dt_util.now(), preview=True)
            if self.play_task is asyncio.current_task():
                self.play_task = None
            self.preview_active = False

    async def radio_after(self):
        """The radio after the wakeup (input_select Radio efter väckningen): on the Bluetooth speaker if it is on and
        answers, else on the panel. Never fails the wakeup itself."""
        choice = self.hass.states.get("input_select." + PREFIX + "radio")
        if choice is None or choice.state in ("Av", "unknown", "unavailable"):
            return
        self.phase = f"Startar {choice.state}"
        try:
            await self.client.radio(choice.state, connect=True)
            self.phase = f"Väckning klar, {choice.state} spelar"
            self.note(f"Radio efter väckningen: {choice.state} startad")
        except HomeAssistantError as err:
            _LOGGER.warning("The radio after the wakeup did not start: %s", err)
            self.note(f"Radio efter väckningen startade inte: {type(err).__name__}: {err}")
            self.phase = "Väckning klar, radion startade inte"

    async def stop(self):
        task = self.play_task
        self.note("Stopp tryckt" + ("" if task is not None and not task.done() else ", ingen väckning spelade"))
        if task is not None and not task.done():
            if self.announcing:
                # HA's ordinary media_stop targets the music pipeline. Wakeups are announcements.
                component = self.hass.data.get(media_player.DATA_COMPONENT)
                player = component.get_entity(PLAYER) if component else None
                if player is None or not hasattr(player, "_client"):
                    raise HomeAssistantError("Når inte panelens uppläsningskanal")
                player._client.media_player_command(player._key, command=MediaPlayerCommand.STOP,
                    announcement=True, device_id=player._static_info.device_id)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.service("input_boolean", "turn_off", {"entity_id": "input_boolean." + PREFIX + "snooze"})

    async def snooze(self):
        last = self.saved.get("last_due")
        if not last or not 0 <= (dt_util.now().timestamp() - datetime.fromisoformat(last).timestamp()) <= SNOOZE_WINDOW:
            raise HomeAssistantError("Ingen aktuell väckning att snooza")
        await self.stop()
        due = (dt_util.now().astimezone(TZ) + timedelta(minutes=5)).replace(microsecond=0)
        await self.service("input_datetime", "set_datetime", {"entity_id": "input_datetime." + PREFIX + "snooze", "datetime": due.strftime("%Y-%m-%d %H:%M:%S")})
        await self.service("input_boolean", "turn_on", {"entity_id": "input_boolean." + PREFIX + "snooze"})
        self.phase = "Snoozad i fem minuter"
        self.note(f"Snooze: ny väckning {due:%H:%M:%S}")

    async def preview(self):
        if self.play_task and not self.play_task.done():
            raise HomeAssistantError("En väckning pågår redan")
        upcoming = self.next_plans()
        kind = upcoming[0].kind if upcoming else "normal"      # what the next real wakeup will sound like
        wish = upcoming[0].wish if upcoming else ""
        self.note(f"Provlyssning startad: {LABELS.get(kind, 'Vanlig morgon')}")
        self.play_task = self.spawn(self.play(Plan(dt_util.now().astimezone(TZ), kind, "preview", wish), preview=True), "provlyssning")
