from pydantic import BaseModel, RootModel, StringConstraints
from typing import Annotated, Literal, Optional, Union, List, Dict

MQTT_ENDPOINT_REGEX = r"^mqtt://.+:\d+$"
OPCUA_ENDPOINT_REGEX = r"^opc\.tcp://.+:\d+$"
MESSAGE_FORMATS = Literal["csi:1.0"]

class AnonymousCredentialsSchema(BaseModel):
    type: Literal["Anonymous"]


class UserNameCredentialsSchema(BaseModel):
    type: Literal["UserName"]
    userName: str
    password: str


class CertificateCredentialsSchema(BaseModel):
    type: Literal["Certificate"]
    privateKey: str
    certificate: str


class ApplicationData(RootModel[Dict[str, Union[str, "ApplicationData"]]]):
    pass


class EdgeHubOutputConfiguration(BaseModel):
    messageFormat: MESSAGE_FORMATS
    applicationData: Optional[ApplicationData] = None


class MqttOutputConfiguration(BaseModel):
    topic: str
    messageFormat: MESSAGE_FORMATS
    applicationData: Optional[ApplicationData] = None


class MonitoredItemConfiguration(BaseModel):
    nodeId: str
    discardOldest: bool | None = None
    queueSize: int | None = None
    samplingInterval: int | None = None


class SubscriptionConfigurationSchema(BaseModel):
    maxNotificationsPerPublish: int
    publishingEnabled: bool
    requestedLifetimeCount: int
    requestedMaxKeepAliveCount: int
    requestedPublishingInterval: int
    priority: int
    monitoredItemsDiscardOldest: bool
    monitoredItemsQueueSize: int
    monitoredItemsSamplingInterval: int
    edgeHubOutput: List[EdgeHubOutputConfiguration] | None = None
    mqttOutput: List[MqttOutputConfiguration] | None = None
    monitoredItems: Dict[str, MonitoredItemConfiguration] | None = None


class SessionConfigurationSchema(BaseModel):
    credentials: Union[
        AnonymousCredentialsSchema,
        UserNameCredentialsSchema,
        CertificateCredentialsSchema,
    ]
    subscriptions: Dict[str, SubscriptionConfigurationSchema]
    dynamicDeclarations: Dict[str, str] | None = None


class ClientConfigurationSchema(BaseModel):
    endpoint: Annotated[str, StringConstraints(pattern=OPCUA_ENDPOINT_REGEX)]
    securityPolicy: Literal[
        "Invalid",
        "None",
        "Basic128",
        "Basic192",
        "Basic192Rsa15",
        "Basic256Rsa15",
        "Basic256Sha256",
        "Aes128_Sha256_RsaOaep",
        "Aes256_Sha256_RsaPss",
        "Aes128Sha256RsaOaep",
        "Aes256Sha256RsaPss",
        "PubSub_Aes128_CTR",
        "PubSub_Aes256_CTR",
        "Basic128Rsa15",
        "Basic256",
    ] = "None"
    messageSecurityMode: Literal["Invalid", "None", "Sign", "SignAndEncrypt"] = "None"
    clientCertificate: str | None = None
    clientPrivateKey: str | None = None
    serverCertificate: str | None = None
    rootCertificates: List[str] | None = None
    rootCrls: List[str] | None = None
    sessions: Dict[str, SessionConfigurationSchema]

class OpcuaClientModuleConfigV1(BaseModel):
    schemaVersion: Literal["1.0"]
    mqttEndpoint: Annotated[str, StringConstraints(pattern=MQTT_ENDPOINT_REGEX)] | None = None
    clients: Dict[str, ClientConfigurationSchema]