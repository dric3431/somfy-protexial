import re
from .abstract_api import AbstractApi
from .const import Page, Selector, Zone

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
            Page.JOURNAL: "/fr/journal.htm", # AJOUT
        }
        self.selectors = {
            Selector.CONTENT_TYPE: "meta[http-equiv='content-type']",
            Selector.LOGIN_CHALLENGE: "#form_id table tr:nth-child(4) td:nth-child(1) b",
            Selector.ERROR_CODE: "#infobox b",
            Selector.FOOTER: "[id^='menu_footer']",
            Selector.CHALLENGE_CARD: "td:not([class])",
        }
        self.encoding = "iso-8859-15"

    def get_login_payload(self, username, password, code):
        return {
            "login": username,
            "password": password,
            "key": code,
            "btn_login": "Connexion",
        }

    # --- NOUVELLE MÉTHODE DE PARSING ---
    def parse_journal(self, html_content):
        """Extrait le dernier événement du journal JavaScript."""
        dates = re.findall(r'var eventdate\s*=\s*\["(.*?)"', html_content)
        times = re.findall(r'var eventtime\s*=\s*\["(.*?)"', html_content)
        names = re.findall(r'var eventname\s*=\s*\["(.*?)"', html_content)
        places = re.findall(r'var eventplace\s*=\s*\["(.*?)"', html_content)

        if not (dates and times and names and places):
            return None

        last_name = names[0]
        last_place = places[0]
        
        # Identification simplifiée
        user = last_place.replace("Badge ", "").strip() if "Badge" in last_place else "Système"
        
        event_type = "Info"
        if "Mise ON" in last_name:
            event_type = "Activation"
        elif "Mise OFF" in last_name:
            event_type = "Désactivation"

        return {
            "event_type": event_type,
            "user": user,
            "timestamp": f"{dates[0]} {times[0]}",
            "full_name": last_name
        }

    # --- GARDER LE RESTE DU CODE ORIGINAL ---
    def get_reset_session_payload(self):
        return {"btn_ok": "OK"}

    def get_arm_payload(self, zone):
        btnZone = ""
        match zone:
            case Zone.A:
                btnZone = "btn_zone_on_A"
            case Zone.B:
                btnZone = "btn_zone_on_B"
            case Zone.C:
                btnZone = "btn_zone_on_C"
            case Zone.ABC:
                btnZone = "btn_zone_on_ABC"
        return {"hidden": "hidden", btnZone: "Marche"}

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
