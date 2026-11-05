import re

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components.esp32 import add_idf_sdkconfig_option, include_builtin_idf_component
from esphome.const import CONF_ID

DEPENDENCIES = ["esp32", "lvgl"]
ns = cg.esphome_ns.namespace("panel_debug")
PanelDebug = ns.class_("PanelDebug", cg.Component)


def build_id(value):
    value = cv.string_strict(value)
    if value != "unversioned" and not re.fullmatch(r"[0-9a-f]{32}", value):
        raise cv.Invalid("build_id must be 32 lowercase hexadecimal characters")
    return value


CONFIG_SCHEMA = cv.Schema({
    cv.GenerateID(): cv.declare_id(PanelDebug),
    # A standalone generated board is disabled until panel.yaml supplies its private token.
    cv.Optional("token", default=""): cv.Any("", cv.All(cv.string_strict, cv.Length(min=32, max=96))),
    cv.Optional("port", default=8080): cv.port,
    # CI's content digest for this candidate; reported as build_id in /status.
    cv.Optional("build_id", default="unversioned"): build_id,
}).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    cg.add(var.set_token(config["token"]))
    cg.add(var.set_port(config["port"]))
    cg.add(var.set_build_id(config["build_id"]))
    # Before App.setup(): the log ring (LP RAM, survives a restart) then holds every setup line.
    cg.add(var.start_log_capture())
    # /tasks: uxTaskGetSystemState needs the trace facility (task numbers only; run-time stats stay off,
    # CPU share is sampled by a timer only while measuring).
    add_idf_sdkconfig_option("CONFIG_FREERTOS_USE_TRACE_FACILITY", True)
    # /crash: keep panic_abort's text ("assert failed: ...") for the next boot (diagnostics.cpp).
    cg.add_build_flag("-Wl,--wrap=panic_abort")
    include_builtin_idf_component("esp_http_server")
    include_builtin_idf_component("esp_driver_jpeg")
    include_builtin_idf_component("esp_driver_ppa")
    include_builtin_idf_component("esp_driver_gptimer")
