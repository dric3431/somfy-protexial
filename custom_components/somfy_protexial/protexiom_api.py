from .abstract_api import AbstractApi
from .const import Page, Selector, Zone


class ProtexiomApi(AbstractApi):
    def __init__(self) -> None:
        self.pages = {
            Page.LOGIN: "/login.htm",
            Page.LOGOUT: "/logout.htm",
            Page.PILOTAGE: "/u_pilotage.htm",
            Page.STATUS: "/status.xml",
            Page.ERROR: "/error.htm",
            Page.ELEMENTS: "/u_plistelmt.htm",
            Page.CHALLENGE_CARD: "/u_print.htm",
            Page.VERSION: None,
            Page.DEFAULT: "/default.htm",
        }
        self.selectors = {
            Selector.CONTENT_TYPE: "meta[http-equiv='content-type']",
            Selector.LOGIN_CHALLENGE: "#form_id table tr:nth-child(4) td:nth-child(1) b",
            Selector.ERROR_CODE: "#infobox b",
            Selector.FOOTER: "[id^='menu_footer']",
            Selector.CHALLENGE_CARD: "td:not([class])",
        }
        self.encoding = "iso-8859-15"
    #new
    def parse_journal(self, html_content):
        """Parse les variables JS du journal Somfy avec support des espaces flexibles."""
        try:
            # Regex flexibles pour correspondre à vos logs (var x = ["..."])
            dates = re.findall(r'var\s+eventdate\s*=\s*\[\s*"(.*?)"', html_content)
            times = re.findall(r'var\s+eventtime\s*=\s*\[\s*"(.*?)"', html_content)
            names = re.findall(r'var\s+eventname\s*=\s*\[\s*"(.*?)"', html_content)
            places = re.findall(r'var\s+eventplace\s*=\s*\[\s*"(.*?)"', html_content)

            if not names or not names[0]:
                return None

            user_raw = places[0] if places else "Système"
            user_clean = user_raw.replace("Badge ", "").strip()
            
            return {
                "event": names[0],
                "user": user_clean,
                "timestamp": f"{dates[0]} {times[0]}" if (dates and times) else "Inconnu",
                "place": user_raw
            }
        except Exception as e:
            _LOGGER.error("Erreur lors du parsing du journal : %s", e)
            return None

    #old
    def get_login_payload(self, username, password, code):
        return {
            "login": username,
            "password": password,
            "key": code,
            "action": "Connexion",
        }

    def get_reset_session_payload(self):
        return {"action": "OK"}

    def get_arm_payload(self, zone):
        value = ""
        match zone:
            case Zone.A:
                value = "Marche A"
            case Zone.B:
                value = "Marche B"
            case Zone.C:
                value = "Marche C"
            case Zone.ABC:
                value = "Marche A B C"

        return {"hidden": "hidden", "zone": value}

    def get_disarm_payload(self):
        return {"hidden": "hidden", "zone": "Arrêt A B C"}

    def get_turn_light_on_payload(self):
        return {"hidden": "hidden", "action_lum": "ON"}

    def get_turn_light_off_payload(self):
        return {"hidden": "hidden", "action_lum": "OFF"}

    def get_open_cover_payload(self):
        return {"hidden": "hidden", "action_vol_montee": ""}

    def get_close_cover_payload(self):
        return {"hidden": "hidden", "action_vol_descente": ""}

    def get_stop_cover_payload(self):
        return {"hidden": "hidden", "action_vol_stop": ""}
