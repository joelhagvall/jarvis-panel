import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import audio, microphone
from esphome.const import CONF_ID, CONF_MICROPHONE, CONF_SAMPLE_RATE

DEPENDENCIES = ["esp32"]
AUTO_LOAD = ["audio"]
ns = cg.esphome_ns.namespace("panel_audio")
PanelMicrophone = ns.class_("PanelMicrophone", cg.Component, microphone.Microphone)

def stream_limits(config):
    audio.set_stream_limits(min_bits_per_sample=16, max_bits_per_sample=16,
                            min_channels=1, max_channels=1)(config)
    return config


CONFIG_SCHEMA = cv.All(microphone.MICROPHONE_SCHEMA.extend({
    cv.GenerateID(): cv.declare_id(PanelMicrophone),
    cv.Required(CONF_MICROPHONE): cv.use_id(microphone.Microphone),
    cv.Optional(CONF_SAMPLE_RATE, default=16000): cv.one_of(16000),
}).extend(cv.COMPONENT_SCHEMA), stream_limits)

FINAL_VALIDATE_SCHEMA = audio.final_validate_audio_schema(
    "panel_audio", audio_device=CONF_MICROPHONE, bits_per_sample=16, channels=1, sample_rate=48000)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await microphone.register_microphone(var, config)
    source = await cg.get_variable(config[CONF_MICROPHONE])
    cg.add(var.set_source(source))
