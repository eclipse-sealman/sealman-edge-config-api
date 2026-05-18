import json
from pathlib import Path
import pytest
from exceptions import UnmatchedDependency
from helper import DeviceInfo, TemplateVariables, is_ip_in_subnet


class TestTemplateVariables:
    def test_get(self):
        # Create an instance of TemplateVariables
        template_vars = TemplateVariables()

        # Add a variable
        template_vars.add("var1", "value1")

        # Assert that the get method returns the correct variables
        assert template_vars.get() == [
            {"name": "var1", "variableValue": "value1"}]

    def test_clear(self):
        # Create an instance of TemplateVariables
        template_vars = TemplateVariables()

        # Add some variables to the instance
        template_vars.add("var1", "value1")
        template_vars.add("var2", "value2")

        # Clear the variables
        template_vars.clear()

        # Assert that the variables list is empty
        assert template_vars.get() == []

    def test_add(self):
        # Create an instance of TemplateVariables
        template_vars = TemplateVariables()

        # Add a variable
        template_vars.add("var1", "value1")

        # Assert that the variable was added correctly
        assert template_vars.get() == [
            {"name": "var1", "variableValue": "value1"}]

    def test_add_bool(self):
        # Create an instance of TemplateVariables
        template_vars = TemplateVariables()

        # Add a boolean variable
        template_vars.add("var_bool", True)

        # Assert that the boolean variable was added correctly as a string
        assert template_vars.get() == [
            {"name": "var_bool", "variableValue": "true"}]

    def test_add_none(self):
        # Create an instance of TemplateVariables
        template_vars = TemplateVariables()

        # Add a None variable
        template_vars.add("var_none", None)

        # Assert that the None variable was added correctly as "null"
        assert template_vars.get() == [
            {"name": "var_none", "variableValue": "null"}]

    def test_add_variables(self):
        # Create an instance of TemplateVariables
        template_vars = TemplateVariables()

        # Add multiple variables
        variables = [
            {"name": "var1", "variableValue": "value1"},
            {"name": "var2", "variableValue": "value2"}
        ]
        template_vars.add_variables(variables)

        # Assert that the variables were added correctly
        assert template_vars.get() == variables

    def test_add_from_dict(self):
        # Create an instance of TemplateVariables
        template_vars = TemplateVariables()

        # Add variables from a dictionary
        variables = {
            "var1": "value1",
            "var2": "value2"
        }
        template_vars.add_from_dict(variables)

        # Assert that the variables were added correctly
        assert template_vars.get() == [
            {"name": "var1", "variableValue": "value1"},
            {"name": "var2", "variableValue": "value2"}
        ]

    def test_convert_to_dict(self):
        # Create an instance of TemplateVariables
        template_vars = TemplateVariables()

        # Add variables
        template_vars.add("var1", "value1")
        template_vars.add("var2", "value2")

        # Convert the variables to a dictionary
        variables_dict = template_vars.convert_to_dict()

        # Assert that the dictionary is correct
        assert variables_dict == {
            "var1": "value1",
            "var2": "value2"
        }

class TestDeviceInfo:
    @pytest.fixture
    def mock_device_info(self):
        mock_device_info = json.loads(
            Path('mocks/mock_device_info.json').read_text())
        mock_device_info["variables"] = [
            {
                "name": "var1",
                "variableValue": "value1"
            },
            {
                "name": "var2",
                "variableValue": "value2"
            }
        ]
        return mock_device_info

    @pytest.fixture
    def device_info(self, mock_device_info):
        return DeviceInfo(mock_device_info)

    def test_get(self, device_info, mock_device_info):
        assert device_info.get() == mock_device_info

    def test_is_sems_enabled(self, device_info):
        assert device_info.is_sems_enabled() is True

    def test_is_template_supported(self, device_info):
        assert device_info.is_template_supported() is True

    def test_get_device_variables_dict(self, device_info):
        expected_dict = {
            "var1": "value1",
            "var2": "value2"
        }
        assert device_info.get_device_variables_dict() == expected_dict

    def test_check_eligibility(self, device_info):
        try:
            device_info.check_eligibility()
        except UnmatchedDependency:
            pytest.fail(
                "check_eligibility() raised UnmatchedDependency unexpectedly!")

    def test_check_eligibility_sems_disabled(self, mock_device_info):
        mock_device_info["enabled"] = False
        device_info = DeviceInfo(mock_device_info)
        with pytest.raises(UnmatchedDependency):
            device_info.check_eligibility()

    def test_check_eligibility_unsupported_template(self, mock_device_info):
        mock_device_info["template"]["representation"] = "unsupported_version"
        device_info = DeviceInfo(mock_device_info)
        with pytest.raises(UnmatchedDependency):
            device_info.check_eligibility()
            
def test_is_ip_in_subnet():
    # Test cases where the IP is within the subnet
    assert is_ip_in_subnet("192.168.1.10", "192.168.1.0", 24) is True
    assert is_ip_in_subnet("192.168.1.1", "192.168.1.0", 24) is True
    assert is_ip_in_subnet("192.168.1.255", "192.168.1.0", 24) is True

    # Test cases where the IP is outside the subnet
    assert is_ip_in_subnet("192.168.2.10", "192.168.1.0", 24) is False
    assert is_ip_in_subnet("10.0.0.1", "192.168.1.0", 24) is False

    # Test cases with different subnet masks
    assert is_ip_in_subnet("192.168.1.10", "192.168.1.0", 16) is True
    assert is_ip_in_subnet("192.10.1.10", "192.168.0.0", 16) is False
    assert is_ip_in_subnet("192.168.1.10", "192.168.0.0", 23) is True

    # Test edge cases
    assert is_ip_in_subnet("192.168.1.0", "192.168.1.0", 24) is True
    assert is_ip_in_subnet("192.168.1.255", "192.168.1.0", 24) is True
    assert is_ip_in_subnet("192.168.1.0", "192.168.1.0", 32) is True
    assert is_ip_in_subnet("192.168.1.1", "192.168.1.1", 32) is True
    assert is_ip_in_subnet("192.168.1.2", "192.168.1.1", 32) is False
