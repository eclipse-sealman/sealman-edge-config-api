import logging
from routers.smart_ems.device_type_config_repo import DeviceTypeConfigRepo
from routers.smart_ems.template_configurator import TemplateConfigurator
from smart_ems import SmartEMS


logger = logging.getLogger("EdgeConfigAPI")


async def post_smart_ems_apply_default_template(device: str):

    template_configurator = TemplateConfigurator(SmartEMS, False)
    config = await DeviceTypeConfigRepo.get_default_config()
    sems_device = await SmartEMS.get_device_by_serial(device, require_template=False)
    return await template_configurator.configure_template(sems_device, config)
