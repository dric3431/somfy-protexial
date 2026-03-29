import asyncio
import logging
import re
import string
from urllib.parse import urlencode
from xml.etree import ElementTree as ET
from aiohttp import ClientError, ClientSession
from pyquery import PyQuery as pq

from .const import (
    CHALLENGE_REGEX, 
    HTTP_TIMEOUT, 
    ApiType, 
    Page, 
    Selector, 
    SomfyError, 
    LIST_ELEMENTS, 
    LIST_ELEMENTS_PRINT, 
    LIST_ELEMENTS_ALT
)
from .protexial_api import ProtexialApi
from .protexial_io_api import ProtexialIOApi
from .protexiom_api import ProtexiomApi
from .somfy_exception import SomfyException

_LOGGER: logging.Logger = logging.getLogger(__name__)
_PRINTABLE_CHARS = set(string.printable)


def _fix_mojibake(text: str) -> str:
    """Correction des problèmes d'accents (mojibake), ex: 'TÃ©l' -> 'Tél'."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except Exception:
        return text


class Status:
    """Conteneur pour les valeurs de status.xml et les événements du journal."""
    zoneA = "off"
    zoneB = "off"
    zoneC = "off"
    battery = "ok"
    radio = "ok"
    door = "ok"
    alarm = "ok"
    box = "ok"
    gsm = "gsm connect au rseau"
    recgsm = "4"
    opegsm = "orange"
    camera = "disabled"
    journal = None  # Reçoit le dict: {event, user, timestamp, place}

    def __getitem__(self, key):
        return getattr(self, key)

    def __str__(self):
        return (f"zoneA:{self.zoneA}, zoneB:{self.zoneB}, zoneC:{self.zoneC}, "
                f"battery:{self.battery}, radio:{self.radio}, door:{self.door}, "
                f"alarm:{self.alarm}, gsm:{self.gsm}, journal:{self.journal}")


class SomfyProtexial:
    """Client API principal pour l'intégration Somfy Protexial/Protexiom."""

    def __init__(self, session: ClientSession, url, api_type=None, username=None, password=None, codes=None) -> None:
        self.url = url.rstrip('/')
        self.api_type = api_type
        self.username = username
        self.password = password
        self.codes = codes
        self.session = session
        self.cookie = None
        self.api = self.load_api(self.api_type)

    async def __do_call(self, method: str, page, headers: dict = None, data: dict = None, 
                        retry: bool = True, login: bool = True, authenticated: bool = True):
        headers = {} if headers is None else dict(headers)

        # 1. Résolution propre du chemin (path)
        if isinstance(page, str):
            path = page
        else:
            # On demande à l'API de traduire l'Enum (ex: Page.JOURNAL) en string ("/fr/journal.htm")
            path = self.api.get_page(page)

        # 2. Sécurité : on s'assure que le path commence par un "/" pour la concaténation
        if not path.startswith("/"):
            path = f"/{path}"

        # 3. Construction de l'URL finale
        full_path = f"{self.url}{path}"
        
        _LOGGER.debug("Appel vers : %s", full_path)
        try:
            if self.cookie and authenticated:
                headers["Cookie"] = self.cookie
            
            payload = None
            if data is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                payload = urlencode(data, encoding=self.api.get_encoding())

            async with asyncio.timeout(HTTP_TIMEOUT):
                if method == "get":
                    response = await self.session.get(full_path, headers=headers)
                else:
                    response = await self.session.post(full_path, data=payload, headers=headers)

            content = await response.text(self.api.get_encoding(), errors="replace")

            if response.status != 200:
                raise SomfyException(f"Erreur HTTP ({response.status})")

            # Gestion de l'expiration de session (redirection vers login/default)
            real_path = getattr(response.real_url, "path", "")
            if real_path == self.api.get_page(Page.DEFAULT) and retry:
                await self.__login()
                return await self.__do_call(method, page, headers, data, False, False, authenticated)

            # Gestion des erreurs applicatives Somfy (codes 0x...)
            if real_path == self.api.get_page(Page.ERROR):
                dom = pq(content)
                code = dom(self.api.get_selector(Selector.ERROR_CODE)).text()

                if code == SomfyError.NOT_AUTHORIZED and retry:
                    await self.__login()
                    return await self.__do_call(method, page, headers, data, False, False, authenticated)

                if code == SomfyError.SESSION_ALREADY_OPEN:
                    if retry:
                        await self.__do_call("post", Page.ERROR, data=self.api.get_reset_session_payload(), retry=False, authenticated=False)
                        self.cookie = None
                        if login: await self.__login()
                        return await self.__do_call(method, page, headers, data, False, login, authenticated)
                    raise SomfyException("Session déjà ouverte (trop de tentatives)")

                if code == SomfyError.WRONG_CREDENTIALS: raise SomfyException("Identifiants incorrects")
                if code == SomfyError.WRONG_CODE: raise SomfyException("Code carte de clés incorrect")
                
                raise SomfyException(f"Erreur centrale: {code}")

            return response

        except (asyncio.TimeoutError, ClientError) as ex:
            _LOGGER.error("Erreur de connexion vers %s: %s", path, ex)
            raise SomfyException(f"Impossible de joindre la centrale sur {path}") from ex

    async def init(self):
        await self.__login()

    def load_api(self, api_type: ApiType):
        if api_type == ApiType.PROTEXIAL: return ProtexialApi()
        if api_type == ApiType.PROTEXIAL_IO: return ProtexialIOApi()
        if api_type == ApiType.PROTEXIOM: return ProtexiomApi()
        return None

    async def guess_and_set_api_type(self):
        for api_type in [ApiType.PROTEXIAL_IO, ApiType.PROTEXIAL, ApiType.PROTEXIOM]:
            self.api = self.load_api(api_type)
            body = await self.do_guess_get(self.api.get_page(Page.LOGIN))
            if body:
                dom = pq(body)
                challenge_el = dom(self.api.get_selector(Selector.LOGIN_CHALLENGE))
                if challenge_el and re.match(CHALLENGE_REGEX, challenge_el.text()):
                    self.api_type = api_type
                    return api_type
        raise SomfyException("Type de centrale non détecté")

    async def do_guess_get(self, page) -> str:
        try:
            async with asyncio.timeout(2):
                resp = await self.session.get(self.url + page, allow_redirects=False)
                if resp.status == 200: return await resp.text()
        except: return None
        return None

    async def __login(self):
        self.cookie = None
        # Récupération du challenge (ex: A1)
        login_resp = await self.__do_call("get", Page.LOGIN, login=False)
        dom = pq(await login_resp.text(self.api.get_encoding()))
        challenge = dom(self.api.get_selector(Selector.LOGIN_CHALLENGE)).text()
        
        if not challenge: raise SomfyException("Challenge non trouvé")
        code = self.codes.get(challenge)
        if not code: raise SomfyException(f"Code manquant pour le challenge {challenge}")

        form = self.api.get_login_payload(self.username, self.password, code)
        resp = await self.__do_call("post", Page.LOGIN, data=form, retry=False, login=False)
        self.cookie = resp.headers.get("SET-COOKIE")

    async def get_status(self):
        """Récupère le statut XML et le journal."""
        # 1. XML Status
        resp = await self.__do_call("get", Page.STATUS, authenticated=False)
        xml_content = await resp.text(self.api.get_encoding())
        root = ET.fromstring(xml_content)
        status = Status()
        
        mapping = {
            "defaut0": "battery", "defaut1": "radio", "defaut2": "door",
            "defaut3": "alarm", "defaut4": "box", "zone0": "zoneA",
            "zone1": "zoneB", "zone2": "zoneC", "gsm": "gsm",
            "recgsm": "recgsm", "opegsm": "opegsm", "camera": "camera"
        }
        for child in root:
            if child.tag in mapping:
                setattr(status, mapping[child.tag], self.filter_ascii(child.text))

        # 2. Journal (Événements récents)
        try:
            # Utilisation correcte de Page.JOURNAL (l'énumération)
            j_resp = await self.__do_call("get", Page.JOURNAL, authenticated=True)
            j_html = await j_resp.text(self.api.get_encoding())
            
            # Appel du parser
            status.journal = self.api.parse_journal(j_html)
            
            _LOGGER.debug("Journal récupéré avec succès: %s", status.journal)
        except Exception as ex:
            # On log l'erreur mais on ne bloque pas le reste du statut
            _LOGGER.error("Erreur lors de la récupération du journal: %s", ex)
            status.journal = None

        return status

    def filter_ascii(self, value) -> str:
        if not value: return ""
        filtered = "".join(filter(lambda x: x in _PRINTABLE_CHARS, value))
        return filtered.lower().strip()

    async def arm(self, zone):
        await self.__do_call("post", Page.PILOTAGE, data=self.api.get_arm_payload(zone))

    async def disarm(self):
        await self.__do_call("post", Page.PILOTAGE, data=self.api.get_disarm_payload())

    async def get_elements(self) -> list[dict]:
        """Analyse la page des éléments pour obtenir la liste des capteurs."""
        candidates = [LIST_ELEMENTS, LIST_ELEMENTS_ALT, LIST_ELEMENTS_PRINT]
        html = None
        for candidate in candidates:
            try:
                resp = await self.__do_call("get", candidate)
                html = await resp.text(self.api.get_encoding())
                if "elt_name" in html: break
            except: continue
        
        if not html: return []

        def extract_array(name: str) -> list[str]:
            m = re.search(rf'var\s+{name}\s*=\s*\[(.*?)\];', html, re.S | re.I)
            if not m: return []
            return [_fix_mojibake(p.strip().strip('"').strip("'")) for p in m.group(1).split(",")]

        names = extract_array("elt_name")
        codes = extract_array("elt_code")
        labels = extract_array("item_label")
        zones = extract_array("elt_zone")
        piles = extract_array("elt_pile")

        elements = []
        for i in range(len(names)):
            elements.append({
                "name": names[i],
                "code": codes[i],
                "label": labels[i] if i < len(labels) else "",
                "zone": zones[i] if i < len(zones) else "",
                "battery": piles[i] if i < len(piles) else "ok"
            })
        return elements
