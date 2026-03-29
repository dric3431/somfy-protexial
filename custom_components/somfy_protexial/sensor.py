# Sensors (GSM + zones from elements + Journal)
import logging
from typing import Any, Iterable

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import SENSORS, COORDINATOR, DEVICE_INFO, DOMAIN

_LOGGER = logging.getLogger(__name__)


# ---------- Utils ----------
def _collect_zone_options(elements: Iterable[dict]) -> list[str]:
    """Build an enum options list from actually observed zones."""
    zones = {e.get("zone", "") for e in (elements or []) if e.get("zone")}
    # keep stable order: SYS first, then alpha
    ordered = ["SYS"] + sorted(z for z in zones if z != "SYS")
    return ordered or ["SYS"]


def _find_element_by_code(elements: list[dict], code: str) -> dict | None:
    """Find an element dict by its 'code' field."""
    for e in elements or []:
        if e.get("code") == code:
            return e
    return None


# ---------- Setup ----------
async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform (GSM sensors + Journal)."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    device_info = hass.data[DOMAIN][config_entry.entry_id][DEVICE_INFO]

    entities: list[SensorEntity] = []

    # 1) Predefined sensors in SENSORS (GSM provider, signal, etc.)
    for sensor in SENSORS:
        entities.append(ProtexialSensor(device_info, coordinator, sensor))

    # 2) NOUVEAU : Ajout du capteur de Journal des événements
    entities.append(ProtexialJournalSensor(device_info, coordinator))

    # 3) Per-element zone sensors (ENUM) from u_plistelmt.htm (kept commented by design)
    # elements = (coordinator.data or {}).get("elements", [])
    # zone_options = _collect_zone_options(elements)

    if entities:
        async_add_entities(entities)
    else:
        _LOGGER.debug("No sensors to add.")


# ---------- Existing sensors (GSM, etc.) ----------
class ProtexialSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Protexial sensor (e.g., GSM operator, RecGSM)."""

    def __init__(self, device_info, coordinator, sensor: Any) -> None:
        """Build the entity using static sensor metadata and the coordinator."""
        super().__init__(coordinator)
        self._attr_id = f"{DOMAIN}_{sensor['id']}"
        self._attr_unique_id = f"{DOMAIN}_{sensor['id']}"
        self._attr_device_info = device_info
        self._sensor_id = sensor["id"]
        self._name = sensor["name"]
        self._icon = sensor.get("icon")
        self._device_class = sensor.get("device_class")
        self._native_value = None
        
        if "entity_category" in sensor:
            self._attr_entity_category = sensor["entity_category"]
        self._attr_suggested_display_precision = sensor.get(
            "suggested_display_precision"
        )

    @property
    def name(self):
        return self._name

    @property
    def icon(self):
        return self._icon

    @property
    def device_class(self):
        return self._device_class

    @property
    def native_value(self):
        return self._native_value

    def _handle_coordinator_update(self) -> None:
        """Handle updated coordinator data."""
        data = self.coordinator.data
        if data:
            value = (data or {}).get(self._sensor_id)

            if self._sensor_id == "recgsm" and value is not None:
                try:
                    self._native_value = int(value)
                except (ValueError, TypeError):
                    self._native_value = None
            elif self._sensor_id == "opegsm" and value is not None:
                self._native_value = str(value).replace('"', "").strip()
            else:
                self._native_value = value

        self.async_write_ha_state()


# ---------- NOUVEAU : Capteur Journal ----------
class ProtexialJournalSensor(CoordinatorEntity, SensorEntity):
    """Capteur pour le journal des événements Somfy (Badges, etc.)."""

    def __init__(self, device_info, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Somfy Journal"
        self._attr_unique_id = f"{DOMAIN}_event_journal"
        self._attr_device_info = device_info
        self._attr_icon = "mdi:format-list-bulleted-type"

    @property
    def native_value(self):
        """L'état principal est le dernier événement (ex: Mise OFF)."""
        journal = self.coordinator.data.get("journal")
        if journal:
            return journal.get("event")
        return "Aucun événement"

    @property
    def extra_state_attributes(self):
        """Attributs pour récupérer l'utilisateur du badge."""
        journal = self.coordinator.data.get("journal")
        if not journal:
            return {}
        return {
            "utilisateur": journal.get("user"),
            "horodatage": journal.get("timestamp"),
            "source": journal.get("place"),
            "evenement_brut": journal.get("full_name")
        }

    def _handle_coordinator_update(self) -> None:
        """Mise à jour automatique via le coordinateur."""
        self.async_write_ha_state()
