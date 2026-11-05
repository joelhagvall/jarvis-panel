"""The Jarvis agent as a Home Assistant conversation agent."""

import logging
from dataclasses import asdict
from typing import Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SIGNAL_NEW_MESSAGE, SIGNAL_WORKING
from .entity import jarvis_device
from .speech import TURN_LANGUAGE, language_code, first_sentence_ready

_LOGGER = logging.getLogger(__name__)
UNREACHABLE = "Jag når inte Jarvis just nu."
STREAM_RESPONSE_CHARS = 60  # HA 2026.10 default_pipeline.py; longer text already starts streaming.


def sentence_stream_listener(listener):
    """Wrap HA's listener, retaining its assistant role until all text has passed."""
    return SentenceStreamListener(listener) if listener is not None else None


class SentenceStreamListener:
    """Start HA's TTS stream at a complete short sentence, with unchanged conversation text.

    The external call/result is a pipeline notification only, not a model tool or
    stored chat entry. Keeping it out of ChatLog preserves the whole plain reply,
    real tool results and follow-up questions, and avoids speaking the prefix twice.

    HA ignores content while its listener's role is tool_result. Complete the marker
    only after the conversation stream ends, otherwise every later word is discarded.
    """
    def __init__(self, listener):
        self.listener = listener
        self.text = ""
        self.started = False
        self.call = None
        self.chat_log = None

    def __call__(self, chat_log, value):
        self.listener(chat_log, value)
        if self.started:
            return
        self.text += value.get("content") or ""
        if (value.get("tool_calls") and self.text.strip()) or len(self.text) > STREAM_RESPONSE_CHARS:
            self.started = True
            return
        if not self.text.strip() or not first_sentence_ready(self.text):
            return
        self.started = True
        self.call = llm.ToolInput(tool_name="jarvis_speech_ready", tool_args={}, external=True)
        self.chat_log = chat_log
        self.listener(chat_log, {"tool_calls": [self.call]})

    def finish(self):
        """Complete the notification after the last text delta, including on errors."""
        if self.call is not None:
            call, self.call = self.call, None
            self.listener(self.chat_log, {"role": "tool_result", "tool_call_id": call.id,
                "tool_name": call.tool_name, "result": asdict(llm.ToolResult(data={"speech_ready": True}))})


async def jarvis_deltas(stream, working=None):
    """The chat log's deltas for the server's stream of ("delta", text), ("tools", names) and ("done", ...).

    When Jarvis has said something and then runs tools, they go into the chat log as external tool calls
    (they ran on Jarvis) with their results. Text followed by a tool call is Home Assistant's sign to start
    speaking at once instead of after 60 characters, so the sentence Jarvis says before its tools is heard
    while they run, and working(text) gets that sentence for the panel's screen. What it writes after them
    starts a new assistant message."""
    said = spoken = after_tools = False     # any text, text in this message, a new message is due
    current = []                            # the text of this message

    def content(text):
        nonlocal said, spoken, after_tools
        said = spoken = True
        current.append(text)
        if after_tools:
            after_tools = False
            return {"role": "assistant", "content": text}
        return {"content": text}

    yield {"role": "assistant"}
    try:
        async for kind, value in stream:
            if kind == "delta":
                yield content(value)
            elif kind == "tools":
                if spoken:
                    if working:
                        working("".join(current).strip())
                    current.clear()
                    calls = [llm.ToolInput(tool_name=str(name), tool_args={}, external=True) for name in value]
                    yield {"tool_calls": calls}
                    for call in calls:
                        yield {"role": "tool_result", "tool_call_id": call.id, "tool_name": call.tool_name,
                               "result": llm.ToolResult(data={"ran_on": "Jarvis"})}
                    spoken, after_tools = False, True
            elif not said:                          # nothing streamed: use the final reply
                yield content(value.get("reply") or UNREACHABLE)
            elif after_tools:                       # nothing after the tools; the log must end with Jarvis
                yield content(" ")
    except HomeAssistantError as err:
        _LOGGER.warning("Jarvis did not answer: %s", err)
        yield content((" " if said else "") + UNREACHABLE)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Add the agent."""
    async_add_entities([JarvisConversation(entry)])


class JarvisConversation(conversation.ConversationEntity):
    """Passes each sentence to the Jarvis server and returns its reply."""

    _attr_name = "Jarvis"
    _attr_supports_streaming = True     # the pipeline can start speaking before the reply is complete

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-conversation"
        self._attr_device_info = jarvis_device(entry)

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Any language; Joel speaks Swedish."""
        return MATCH_ALL

    async def _async_handle_message(self, user_input: conversation.ConversationInput,
                                    chat_log: conversation.ChatLog) -> conversation.ConversationResult:
        language = TURN_LANGUAGE.get() or language_code(user_input.language)
        stream = self._entry.runtime_data.converse_stream(user_input.text, chat_log.conversation_id, language=language)
        working = lambda text: async_dispatcher_send(self.hass, SIGNAL_WORKING, text)  # noqa: E731
        original_listener = chat_log.delta_listener
        listener = sentence_stream_listener(original_listener)
        chat_log.delta_listener = listener
        try:
            async for _content in chat_log.async_add_delta_content_stream(user_input.agent_id,
                                                                         jarvis_deltas(stream, working)):
                pass
        finally:
            chat_log.delta_listener = original_listener
            if listener is not None:
                listener.finish()
        async_dispatcher_send(self.hass, SIGNAL_NEW_MESSAGE)
        return conversation.async_get_result_from_chat_log(user_input, chat_log)
