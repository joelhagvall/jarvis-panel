from esphome import automation
import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import image
from esphome.components.esp32 import include_builtin_idf_component
from esphome.const import CONF_ID, CONF_URL, CONF_ON_ERROR, CONF_TYPE

AUTO_LOAD = ["runtime_image"]
DEPENDENCIES = ["esp32"]
ns = cg.esphome_ns.namespace("async_artwork")
AsyncArtwork = ns.class_("AsyncArtwork", cg.PollingComponent, image.Image_)
CONFIG_SCHEMA = cv.Schema({
    cv.GenerateID(): cv.declare_id(AsyncArtwork),
    cv.Required(CONF_URL): cv.url,
    cv.Optional("on_download_finished"): automation.validate_automation({}),
    cv.Optional(CONF_ON_ERROR): automation.validate_automation({}),
}).extend(cv.polling_component_schema("never"))

async def to_code(config):
    image.add_metadata(config[CONF_ID], 288, 288, "RGB565", "opaque")
    var = cg.new_Pvariable(config[CONF_ID], config[CONF_URL])
    await cg.register_component(var, config)
    cg.add_define("USE_RUNTIME_IMAGE_QOI")
    include_builtin_idf_component("esp_http_client")
    await automation.build_callback_automations(var, config, (
        automation.CallbackAutomation("on_download_finished", "add_on_finished_callback"),
        automation.CallbackAutomation(CONF_ON_ERROR, "add_on_error_callback"),
    ))
