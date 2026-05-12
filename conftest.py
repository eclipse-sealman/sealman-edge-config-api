"""
Root-level pytest configuration.

Ensures that LAN_EDGE_TEMPLATE_VERSIONS and NAT_TEMPLATE_SUPPORT contain at least
the template value used in mock_device_info.json ("MOCK_TEST_TEMPLATE") so that
happy-path tests pass in the CI pipeline where no .env file is present.
"""

import constants

_TEST_TEMPLATE = "MOCK_TEST_TEMPLATE"

constants.LAN_EDGE_TEMPLATE_VERSIONS.append(_TEST_TEMPLATE)
constants.NAT_TEMPLATE_SUPPORT.append(_TEST_TEMPLATE)