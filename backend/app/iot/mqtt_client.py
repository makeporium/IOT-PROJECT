"""DOORSAFE Phase 4: Resilient Automotive MQTT Gateway Client."""

import json
import time
import logging
import threading
from collections import deque
from typing import Dict, Any, Optional, Callable, List
from pydantic import BaseModel

import paho.mqtt.client as mqtt

from app.config import settings
from app.iot.topics import MQTTTopics, HeartbeatPayload

logger = logging.getLogger("doorsafe.iot.mqtt")


class DoorSafeMQTTClient:
    """
    Manages robust asynchronous MQTT communication between the DOORSAFE backend
    and physical / simulated ESP32 automotive microcontrollers.
    """

    def __init__(
        self,
        broker_host: Optional[str] = None,
        broker_port: Optional[int] = None,
        client_id: Optional[str] = None,
        keepalive: int = 60,
    ):
        self.broker_host = broker_host or settings.MQTT_BROKER_HOST
        self.broker_port = broker_port or settings.MQTT_BROKER_PORT
        self.client_id = client_id or f"{settings.MQTT_CLIENT_ID}_{int(time.time())}"
        self.keepalive = keepalive

        # State tracking
        self.is_connected = False
        self.last_connected_time: Optional[float] = None
        self.last_error: Optional[str] = None
        self.messages_published = 0
        self.messages_received = 0

        # Live message audit ring buffer (last 50 messages for UI display)
        self.recent_messages: deque = deque(maxlen=50)
        self._lock = threading.Lock()

        # Topic Callback registry: topic -> List[Callable[[Dict[str, Any]], None]]
        self._callbacks: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}

        # Initialize Paho MQTT Client (supports Paho 2.x CallbackAPIVersion)
        try:
            # Paho 2.x API
            self._client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=self.client_id
            )
        except AttributeError:
            # Paho 1.x fallback
            self._client = mqtt.Client(client_id=self.client_id)

        self._setup_handlers()

    def _setup_handlers(self):
        """Configures MQTT event handlers and Last Will and Testament (LWT)."""
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        # Configure Last Will and Testament (LWT)
        lwt_payload = HeartbeatPayload(
            device_id=self.client_id,
            timestamp=time.time(),
            status="OFFLINE",
            uptime_seconds=0,
            firmware_version=settings.VERSION
        ).model_dump_json()

        self._client.will_set(
            topic=MQTTTopics.SYSTEM_HEARTBEAT.value,
            payload=lwt_payload,
            qos=1,
            retain=False
        )

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        """Callback triggered on successful broker connection."""
        # In Paho 2.x, rc is a ReasonCode object; in 1.x it is an int
        rc_code = getattr(rc, "value", rc) if hasattr(rc, "value") else rc
        if rc_code == 0:
            self.is_connected = True
            self.last_connected_time = time.time()
            self.last_error = None
            logger.info(f"Connected to MQTT broker at {self.broker_host}:{self.broker_port}")

            # Subscribe to ESP32 sensor and heartbeat topics
            topics = [
                (MQTTTopics.SENSOR_TOUCH.value, 1),
                (MQTTTopics.SENSOR_RADAR.value, 1),
                (MQTTTopics.SYSTEM_HEARTBEAT.value, 1),
                (MQTTTopics.ACTUATOR_STATE.value, 1),
            ]
            self._client.subscribe(topics)
            logger.info(f"Subscribed to MQTT topics: {[t[0] for t in topics]}")

            # Emit initial online heartbeat
            self.publish_heartbeat(status="ONLINE")
        else:
            self.is_connected = False
            self.last_error = f"Connection refused with code {rc}"
            logger.warning(f"MQTT connection refused: {rc}")

    def _on_disconnect(self, client, userdata, disconnect_flags, rc=None, properties=None):
        """Callback triggered on broker disconnection."""
        self.is_connected = False
        logger.warning(f"Disconnected from MQTT broker (rc: {rc})")

    def _on_message(self, client, userdata, msg):
        """Callback triggered when an incoming MQTT message arrives."""
        self.messages_received += 1
        payload_str = msg.payload.decode("utf-8", errors="replace")
        topic = msg.topic

        try:
            data = json.loads(payload_str)
        except Exception:
            data = {"raw_payload": payload_str}

        # Log to recent message buffer
        entry = {
            "direction": "IN",
            "topic": topic,
            "timestamp": time.time(),
            "payload": data
        }
        with self._lock:
            self.recent_messages.append(entry)

        logger.debug(f"MQTT IN [{topic}]: {payload_str}")

        # Dispatch to registered callbacks
        with self._lock:
            handlers = self._callbacks.get(topic, []).copy()

        for handler in handlers:
            try:
                handler(data)
            except Exception as e:
                logger.error(f"Error executing MQTT handler for topic {topic}: {e}")

    def register_callback(self, topic: str, callback: Callable[[Dict[str, Any]], None]):
        """Registers a listener for messages matching a specific MQTT topic."""
        with self._lock:
            if topic not in self._callbacks:
                self._callbacks[topic] = []
            self._callbacks[topic].append(callback)

    def start(self):
        """Starts the MQTT network loop asynchronously in a background thread."""
        try:
            logger.info(f"Connecting to MQTT broker {self.broker_host}:{self.broker_port}...")
            self._client.connect_async(self.broker_host, self.broker_port, self.keepalive)
            self._client.loop_start()
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"Failed to start MQTT loop (network unavailable or broker down): {e}")

    def stop(self):
        """Gracefully shuts down MQTT client and stops the network loop."""
        try:
            if self.is_connected:
                self.publish_heartbeat(status="OFFLINE")
            self._client.loop_stop()
            self._client.disconnect()
            self.is_connected = False
            logger.info("MQTT client stopped cleanly.")
        except Exception as e:
            logger.error(f"Error stopping MQTT client: {e}")

    def publish(self, topic: str, payload: Any, qos: int = 1) -> bool:
        """Publishes a structured dictionary or Pydantic model to an MQTT topic."""
        if isinstance(payload, BaseModel):
            payload_str = payload.model_dump_json()
            payload_dict = payload.model_dump()
        elif isinstance(payload, dict):
            payload_str = json.dumps(payload)
            payload_dict = payload
        elif isinstance(payload, str):
            payload_str = payload
            payload_dict = {"text": payload}
        else:
            payload_str = str(payload)
            payload_dict = {"value": payload_str}

        entry = {
            "direction": "OUT",
            "topic": topic,
            "timestamp": time.time(),
            "payload": payload_dict
        }
        with self._lock:
            self.recent_messages.append(entry)

        if not self.is_connected:
            # Buffer message in history even if offline
            logger.debug(f"MQTT offline: skipped dispatch of [{topic}]")
            return False

        try:
            res = self._client.publish(topic, payload_str, qos=qos)
            self.messages_published += 1
            return res.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Failed to publish to MQTT topic {topic}: {e}")
            return False

    def publish_heartbeat(self, status: str = "ONLINE"):
        """Publishes backend gateway health status."""
        payload = HeartbeatPayload(
            device_id=self.client_id,
            timestamp=time.time(),
            status=status,
            uptime_seconds=int(time.time() - (self.last_connected_time or time.time())),
            firmware_version=settings.VERSION
        )
        self.publish(MQTTTopics.SYSTEM_HEARTBEAT.value, payload, qos=0)

    def get_status(self) -> Dict[str, Any]:
        """Returns connection health telemetry and recent message buffer."""
        with self._lock:
            messages_snapshot = list(self.recent_messages)[-15:]

        return {
            "is_connected": self.is_connected,
            "broker_host": self.broker_host,
            "broker_port": self.broker_port,
            "client_id": self.client_id,
            "messages_published": self.messages_published,
            "messages_received": self.messages_received,
            "last_connected_time": self.last_connected_time,
            "last_error": self.last_error,
            "recent_messages": messages_snapshot
        }


# Global singleton instance
_mqtt_gateway: Optional[DoorSafeMQTTClient] = None


def get_mqtt_gateway() -> DoorSafeMQTTClient:
    """Returns singleton DoorSafeMQTTClient instance."""
    global _mqtt_gateway
    if _mqtt_gateway is None:
        _mqtt_gateway = DoorSafeMQTTClient()
    return _mqtt_gateway
