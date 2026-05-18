from ipaddress import IPv4Address
from routers.smart_ems.schemas import NatConfig, NatRule

class TestNatConfig:
    def test_nat_config_with_rules(self):
        nat_rules = [
            NatRule(name="machine1", extIp=IPv4Address("192.168.178.201"), intIp=IPv4Address("172.22.220.100")),
            NatRule(name="machine2", extIp=IPv4Address("192.168.178.202"), intIp=IPv4Address("172.22.220.101"))
        ]
        nat_config = NatConfig(nat_enabled=True, nat_rules=nat_rules)
        assert nat_config.nat_enabled is True
        assert len(nat_config.get_nat_rules()) == 2
        assert nat_config.get_nat_rules()[0].name == "machine1"
        assert str(nat_config.get_nat_rules()[1].extIp) == "192.168.178.202"

    def test_nat_config_without_rules(self):
        nat_config = NatConfig(nat_enabled=True)
        assert nat_config.nat_enabled is True
        assert nat_config.get_nat_rules() == []
