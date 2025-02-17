import json
import logging
from typing import TYPE_CHECKING, Callable, List, Optional, Union

try:
    from awscrt import mqtt
except ImportError:  # pragma: no cover
    pass

from pysesame3.auth import CognitoAuth
from pysesame3.const import CHSesame2CMD, CHSesame2ShadowStatus
from pysesame3.device import SesameLocker
from pysesame3.helper import CHProductModel, CHSesameBotMechStatus

if TYPE_CHECKING:
    from pysesame3.auth import WebAPIAuth
    from pysesame3.history import CHSesame2History

logger = logging.getLogger(__name__)


class CHSesameBot(SesameLocker):
    def __init__(
        self,
        authenticator: Union["WebAPIAuth", "CognitoAuth"],
        device_uuid: str,
        secret_key: str,
    ) -> None:
        """SESAME bot Device Specific Implementation.

        Args:
            authenticator (Union[WebAPIAuth, CognitoAuth]):
            device_uuid (str): The UUID of the device
            secret_key (str): The secret key of the device
        """
        super().__init__(authenticator)

        self.setDeviceUUID(device_uuid)
        self.setSecretKey(secret_key)
        self.setProductModel(CHProductModel.SesameBot1)
        self._callback: Optional[
            Callable[[CHSesameBot, CHSesameBotMechStatus], None]
        ] = None

        # Initial sync of `self._deviceShadowStatus`
        self.mechStatus
        logger.debug("Initialized={}".format(str(self)))

    @property
    def mechStatus(self) -> CHSesameBotMechStatus:
        """Return a mechanical status of a device.

        Returns:
            CHSesameBotMechStatus: Current mechanical status of the device.
        """
        status = CHSesameBotMechStatus(
            self.authenticator.sesame_cloud.getMechStatus(self)
        )
        logger.debug("UUID={}, mechStatus={}".format(self.getDeviceUUID(), str(status)))

        if status.isInLockRange():
            self.setDeviceShadowStatus(CHSesame2ShadowStatus.LockedWm)
        else:
            self.setDeviceShadowStatus(CHSesame2ShadowStatus.UnlockedWm)

        return status

    def _iot_shadow_callback(self, topic: str, payload: bytes, *_):
        """Callback for updated shadows.

        Args:
            message (dict): The device shadow
        """
        try:
            logger.info("UUID={}, Shadow updated".format(self.getDeviceUUID()))
            shadow = json.loads(payload.decode("utf-8"))
            status = CHSesameBotMechStatus(
                rawdata=shadow["state"]["reported"]["mechst"]
            )
            logger.debug(
                "UUID={}, reported mechst={}".format(self.getDeviceUUID(), str(status))
            )

            original_status = self.getDeviceShadowStatus()

            # It is possible that both isInLockRange and isInUnlockRange are true.
            # This probably indicates that the key is rotating.
            # We have to carefully check the status just to make sure that
            # it has been definitely toggled.
            if not status.isInLockRange() and status.isInUnlockRange():
                self.setDeviceShadowStatus(CHSesame2ShadowStatus.UnlockedWm)
            if status.isInLockRange() and not status.isInUnlockRange():
                self.setDeviceShadowStatus(CHSesame2ShadowStatus.LockedWm)

            if original_status != self.getDeviceShadowStatus():
                if self._callback is not None and callable(self._callback):
                    logger.debug(
                        "UUID={}, Custom callback triggered".format(
                            self.getDeviceUUID()
                        )
                    )
                    self._callback(self, status)
        except Exception as err:  # noqa: F841
            # TODO: handle exceptions correctly
            pass

    def subscribeMechStatus(
        self,
        callback: Optional[
            Callable[["CHSesameBot", CHSesameBotMechStatus], None]
        ] = None,
    ) -> None:
        """Subscribe to a topic at AWS IoT

        Args:
            callback (Callable[[CHSesameBot, CHSesameBotMechStatus], None], optional): The registered callback will be executed once an update is delivered. Defaults to `None`.

        Raises:
            NotImplementedError: If the authenticator is not `AuthType.SDK`.
        """
        if not isinstance(self.authenticator, CognitoAuth):
            raise NotImplementedError("This feature is not suppoted by the Web API.")

        logger.info("UUID={}, Subscribe to the topic...".format(self.getDeviceUUID()))
        if callable(callback) or callback is None:
            self._callback = callback
        else:
            raise TypeError("callback should be callable.")

        aws_iot = self.authenticator.aws_iot
        try:
            aws_iot.connect()
        except RuntimeError as e:
            if "AWS_ERROR_MQTT_ALREADY_CONNECTED" in str(e):
                logger.debug("The connection to AWS IoT is already open")
                pass
            else:
                raise e

        subscribe_future, _ = aws_iot.mqtt_connection.subscribe(
            topic="$aws/things/sesame2/shadow/name/{}/update/accepted".format(
                self.getDeviceUUID()
            ),
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=self._iot_shadow_callback,
        )
        subscribe_future.result()
        logger.info("UUID={}, Subscription established".format(self.getDeviceUUID()))

    @property
    def historyEntries(self) -> List["CHSesame2History"]:
        """Return the history of all events with a device.

        Returns:
            list[CHSesame2History]: A list of events.
        """
        return self.authenticator.sesame_cloud.getHistoryEntries(self)

    def getDeviceShadowStatus(self) -> CHSesame2ShadowStatus:
        """Return a cached shadow status of a device.
        In order to refresh the shadow, run `mechStatus`.

        Returns:
            CHSesame2ShadowStatus: Shadow (assumed) status of the device.
        """
        return self._deviceShadowStatus

    def setDeviceShadowStatus(self, status: CHSesame2ShadowStatus) -> None:
        """Set a shadow status of a device.

        Args:
            status (CHSesame2ShadowStatus): Desired shadow (assumed) status

        Raises:
            ValueError: If `status` is invalid.
        """
        if not isinstance(status, CHSesame2ShadowStatus):
            raise ValueError("Invalid CHSesame2ShadowStatus")
        logger.debug("UUID={}, set={}".format(self.getDeviceUUID(), status))
        self._deviceShadowStatus = status

    def click(self, history_tag: str = "pysesame3") -> bool:
        """Locking.

        Args:
            history_tag (str): The key tag to sent when locking and unlocking. Defaults to `pysesame3`.

        Returns:
            bool: `True` if it is successfully locked, `False` if not.
        """
        logger.info("UUID={}, Clicking...".format(self.getDeviceUUID()))
        result = self.authenticator.sesame_cloud.sendCmd(
            self, CHSesame2CMD.CLICK, history_tag
        )
        if result:
            self.setDeviceShadowStatus(CHSesame2ShadowStatus.LockedWm)
        return result

    def __str__(self) -> str:
        """Return a string representation of an object.

        Returns:
            str: The string representation of the object.
        """
        return f"CHSesameBot(deviceUUID={self.getDeviceUUID()}, deviceModel={self.productModel}, mechStatus={self.mechStatus})"
