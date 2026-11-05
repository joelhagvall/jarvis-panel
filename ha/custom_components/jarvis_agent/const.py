"""Constants for the Jarvis integration."""

DOMAIN = "jarvis_agent"
CONF_URL = "url"
CONF_TOKEN = "token"
DEFAULT_URL = "http://127.0.0.1:8765"
SIGNAL_NEW_MESSAGE = f"{DOMAIN}_new_message"
SIGNAL_WORKING = f"{DOMAIN}_working"     # what Jarvis said before his tools, for the panel
HISTORY_LIMIT = 20
HOME_INTERVAL_S = 15
VOICE_SILENCE_S = 0.8
VOICE_EARLY_S = 0.4          # the recording is sent this long before the pause runs out; dropped if speech resumes
VOICE_SPEECH_RESET_S = 0.3   # speech this long after a pause restores the whole pause (Home Assistant: 1.0)
VOICE_MAX_RECORDING_S = 60.0
VOICE_START_TIMEOUT_S = 5.0
