import logging
import re
from .abstract_api import AbstractApi
from .const import Page, Selector, Zone

_LOGGER = logging.getLogger(__name__)

class ProtexialApi(AbstractApi):
    def __init__(self) -> None:
        self.pages = {
            Page.LOGIN: "/fr/login.htm",
            Page.LOGOUT: "/logout.htm",
            Page.PILOTAGE: "/fr/u_pilotage.htm",
            Page.STATUS: "/status.xml",
            Page.ERROR: "/fr/error.htm",
            Page.ELEMENTS: "/fr/u_plistelmt.htm",
            Page.CHALLENGE_CARD: "/fr/u_print.htm",
            Page.VERSION: "/cfg/vers",
            Page.DEFAULT: "/default.htm",
            Page.JOURNAL: "/fr/journal.htm",
        }
        self.selectors = {
            Selector.CONTENT_TYPE: "meta[http-equiv='content-type']",
            Selector.LOGIN_CHALLENGE: "#form_id table tr:nth-child(4) td:nth-child(1) b",
            Selector.ERROR_CODE: "#infobox b",
            Selector.FOOTER: "[id^='menu_footer']",
            Selector.CHALLENGE_CARD: "td:not([class])",
        }
        self.encoding = "iso-8859-15"

    def parse_journal(self, html_content):
        """Parse les variables JS du journal Somfy Protexial."""
        try:
            def extract_first(var_name):
                # Ajout de \s* pour absorber les espaces visibles dans vos logs
                pattern = rf'var\s+{var_name}\s*=\s*\[\s*"(.*?)"'
                match = re.search(pattern, html_content, re.S)
                return match.group(1).strip() if match else None

            date = extract_first("eventdate")
            time = extract_first("eventtime")
            name = extract_first("eventname")
            place = extract_first("eventplace")

            if not name:
                return None

            user = place.replace("Badge ", "").strip() if place else "Système"
            
            return {
                "event": name,
                "user": user,
                "timestamp": f"{date} {time.replace('h', ':')}" if (date and time) else "Inconnu",
                "place": place
            }
        except Exception as e:
            _LOGGER.error("Erreur parsing journal: %s", e)
            return None

    def get_login_payload(self, username, password, code):
        return {"login": username, "password": password, "key": code, "btn_login": "Connexion"}

    def get_reset_session_payload(self):
        return {"btn_ok": "OK"}

    def get_arm_payload(self, zone):
        mapping = {Zone.A: "btn_zone_on_A", Zone.B: "btn_zone_on_B", Zone.C: "btn_zone_on_C", Zone.ABC: "btn_zone_on_ABC"}
        return {"hidden": "hidden", mapping.get(zone, "btn_zone_on_ABC"): "Marche"}

    def get_disarm_payload(self):
        return {"hidden": "hidden", "btn_zone_off_ABC": "Arrêt A B C"}

    def get_turn_light_on_payload(self):
        return {"hidden": "hidden", "btn_lum_on": "ON"}

    def get_turn_light_off_payload(self):
        return {"hidden": "hidden", "btn_lum_off": "OFF"}

    def get_open_cover_payload(self):
        return {"hidden": "hidden", "btn_vol_up": ""}

    def get_close_cover_payload(self):
        return {"hidden": "hidden", "btn_vol_down": ""}

    def get_stop_cover_payload(self):
        return {"hidden": "hidden", "btn_vol_stop": ""}
