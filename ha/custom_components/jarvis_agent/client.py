"""Talks to the Jarvis server (server/hallpanel_server.py in the hallpanel repo) on the same machine."""

import json
import io
import wave

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import VOICE_SILENCE_S
from .speech import TURN_ID


class JarvisAuthError(HomeAssistantError):
    """The server refused the token."""


class JarvisClient:
    """POST and GET against the Jarvis server, with its token."""

    def __init__(self, hass: HomeAssistant, url: str, token: str) -> None:
        self._session = async_get_clientsession(hass)
        self._url = url.rstrip("/")
        self._headers = {"X-Hallpanel-Token": token}
        self.silence_seconds = VOICE_SILENCE_S

    async def _call(self, method: str, path: str, payload=None, timeout: float = 15, headers=None) -> bytes:
        body = {"data": payload} if isinstance(payload, bytes) else {"json": payload}
        try:
            async with self._session.request(
                method, self._url + path, **body, headers={**self._headers, **(headers or {})},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                body = await resp.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(f"Cannot reach Jarvis at {self._url}: {err}") from err
        if resp.status == 401:
            raise JarvisAuthError("Jarvis refused the token")
        if resp.status >= 400:
            try:                            # the server's own reason, such as "Länken pekar inte på en bild."
                reason = json.loads(body).get("error")
            except ValueError:
                reason = None
            raise HomeAssistantError(reason or f"Jarvis answered {resp.status} on {path}")
        return body

    async def warm(self) -> None:
        """Open the agent's connections ahead of a question."""
        await self._call("POST", "/warm", timeout=10)

    async def wakeup(self, action: str, payload=None) -> dict:
        if action == "status":
            return json.loads(await self._call("GET", "/wakeup/status", timeout=10))
        if action not in ("prepare", "claim", "reserves"):
            raise ValueError("Unknown wakeup action")
        return json.loads(await self._call("POST", "/wakeup/" + action, payload or {}, timeout=120 if action in ("prepare", "reserves") else 15))

    async def converse(self, text: str, conversation_id: str | None, language: str | None = None) -> str:
        """Ask the agent. The server stores both sides in the chat history."""
        body = await self._call("POST", "/converse", {"text": text, "conversation_id": conversation_id,
                                                       "language": language, "turn": TURN_ID.get()}, timeout=90)
        return str(json.loads(body).get("reply") or "")

    async def converse_stream(self, text: str, conversation_id: str | None, language: str | None = None):
        """Ask the agent and yield ("delta", text) while it writes and ("tools", [names]) as a round of
        its tools starts, then ("done", {"reply", "ok"})."""
        try:
            async with self._session.post(
                self._url + "/converse", headers=self._headers,
                json={"text": text, "conversation_id": conversation_id, "stream": True, "language": language,
                      "turn": TURN_ID.get()},
                timeout=aiohttp.ClientTimeout(total=90, sock_read=60),
            ) as resp:
                if resp.status == 401:
                    raise JarvisAuthError("Jarvis refused the token")
                if resp.status >= 400:
                    raise HomeAssistantError(f"Jarvis answered {resp.status} on /converse")
                async for line in resp.content:
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    if msg.get("done"):
                        yield "done", msg
                    elif msg.get("delta"):
                        yield "delta", msg["delta"]
                    elif msg.get("tools"):
                        yield "tools", msg["tools"]
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise HomeAssistantError(f"Lost Jarvis while it answered: {err}") from err

    async def clear(self) -> None:
        """Rensa: Jarvis forgets the conversation (the history stays on Jarvis)."""
        await self._call("POST", "/clear", timeout=10)

    async def reminder_cancel(self, reminder_id: int) -> bool:
        """Ta bort on Hemmet's reminder page: True when it was pending and is now cancelled, False when it no longer
        waits (POST /reminders/cancel answers 404)."""
        try:
            await self._call("POST", "/reminders/cancel", {"id": reminder_id}, timeout=10)
        except HomeAssistantError as err:
            if "404" in str(err):
                return False
            raise
        return True

    async def panel(self) -> dict:
        """The home as short strings for the panel's Hemmet page (GET /panel)."""
        return json.loads(await self._call("GET", "/panel", timeout=10))

    async def background(self, choice: str | None = None, url: str | None = None) -> dict:
        """The panel's background, the choices and the last link; with choice or url, change it first. A new
        picture is baked on Jarvis first, which takes about 8 s."""
        if choice is None and url is None:
            return json.loads(await self._call("GET", "/background"))
        payload = {"url": url} if url is not None else {"choice": choice}
        return json.loads(await self._call("POST", "/background", payload, timeout=90))

    async def radio(self, channel: str, connect: bool = False) -> dict:
        """Sveriges Radio where the sound plays (POST /radio); connect first connects the paired Bluetooth speaker
        if none is, which can take 25 s."""
        return json.loads(await self._call("POST", "/radio", {"channel": channel, "connect": connect}, timeout=60))

    async def speaker(self, choice: str | None = None) -> dict:
        """Where the radio plays: {"available", "choice", "options"}; with choice, connect that Bluetooth speaker on
        Jarvis, or "Panelen" to disconnect. Connecting can take 25 s (bluetoothctl connect, then a check)."""
        if choice is None:
            return json.loads(await self._call("GET", "/speaker"))
        return json.loads(await self._call("POST", "/speaker", {"choice": choice}, timeout=40))

    async def speaker_reconnect(self) -> dict:
        """Koppla om: disconnect and connect the Bluetooth speaker on Jarvis again. Jarvis answers once it is
        connected (up to about 55 s, with a restart of its Bluetooth adapter) and heals the sound path for half a
        minute after that."""
        return json.loads(await self._call("POST", "/speaker/reconnect", {}, timeout=75))

    async def speed(self, rpm: int | None = None) -> dict:
        """The turntable's speed (server/speed.py): {"rpm": 33 or 45, "available"}; with rpm, set it first."""
        if rpm is None:
            return json.loads(await self._call("GET", "/speed"))
        return json.loads(await self._call("POST", "/speed", {"rpm": rpm}, timeout=15))

    async def transcribe(self, pcm: bytes) -> dict:
        wav = io.BytesIO()
        with wave.open(wav, 'wb') as audio:
            audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            audio.writeframes(pcm)
        turn = TURN_ID.get()
        return json.loads(await self._call("POST", "/transcribe", wav.getvalue(), timeout=40,
                                           headers={"X-Hallpanel-Turn": turn} if turn else None))

    async def report_turn(self, turn: str, stage: str, report: dict) -> None:
        """How a voice turn's stage ended, for the server's voice_turns table (stt.py, tts.py)."""
        await self._call("POST", "/turn", {"turn": turn, "stage": stage, "report": report}, timeout=5)

    async def speak(self, text: str, language: str | None = None) -> bytes:
        """WAV of Mattias reading the text, with shortened pauses."""
        return await self._call("POST", "/speak", {"text": text, "language": language}, timeout=30)

    async def history(self, limit: int) -> list[dict]:
        """The latest messages, oldest first."""
        body = await self._call("GET", f"/history?limit={limit}")
        return json.loads(body).get("messages", [])
