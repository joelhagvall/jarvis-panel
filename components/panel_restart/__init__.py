"""ESP32-P4 restart cache-sync guard, independent of the agent running the build."""
import esphome.codegen as cg
import esphome.config_validation as cv

DEPENDENCIES = ["esp32"]
CONFIG_SCHEMA = cv.Schema({})


async def to_code(config):
    cg.add_build_flag("-Wl,--wrap=esp_restart_noos")
