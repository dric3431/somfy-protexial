import asyncio
import logging
import re
import string
import html as html_lib
import unicodedata

from urllib.parse import urlencode
from xml.etree import ElementTree as ET
from aiohttp import ClientError, ClientSession
from pyquery import PyQuery as pq

from .const import CHALLENGE_REGEX, HTTP_TIMEOUT, ApiType, Page, Selector, SomfyError, LIST_ELEMENTS, LIST_ELEMENTS_PRINT, LIST_ELEMENTS_ALT
from .protexial_api import ProtexialApi
from .protexial_io_api import ProtexialIOApi
from .protexiom_api import ProtexiomApi
from .somfy_exception import SomfyException

_LOGGER: logging.Logger = logging.getLogger(__name__)
_PRINTABLE_CHARS = set(string.printable)


def _fix_mojibake(text: str) -> str:
    """Best-effort fix for accent mojibake, e.g. 'TÃ©l' -> 'Tél'."""
    try:
        # Re-decode as if the text was incorrectly encoded in latin-1
        return text.encode("latin-1").decode("utf-8")
    except Exception:
        return text


class Status:
    """Container for parsed status.xml values and journal events."""

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
    journal = None  # Contiendra le dictionnaire de l'événement (badge, heure, etc.)

    def __getitem__(self, key):
        """Allow dict-like access (status['zoneA'])."""
        return getattr(self, key)

    def __str__(self):
        """Readable dump of the status values."""
        return f"zoneA:{self.zoneA}, zoneB:{self.zoneB}, zoneC:{self.zoneC}, battery:{self.battery}, radio:{self.radio}, door:{self.door}, alarm:{self.alarm}, box:{self.box}, gsm:{self.gsm}, recgsm:{self.recgsm}, opegsm:{self.opegsm}, camera:{self.camera}, journal:{self.journal}"


class SomfyProtexial:
    """Main API client used by the integration to interact with the centrale."""

    def __init__(
        self,
        session: ClientSession,
        url,
        api_type=None,
        username=None,
        password=None,
        codes=None,
    ) -> None:
        """Initialize the client with HTTP session, base URL and credentials."""
        self.url = url
        self.api_type = api_type
        self.username = username
        self.password = password
        self.codes = codes
        self.session = session
        self.cookie = None
        self.api = self.load_api(self.api_type)

    async def __do_call(
        self,
        method: str,
        page,
        headers: dict | None = None,
        data: dict | None = None,
        retry: bool = True,
        login: bool = True,
        authenticated: bool = True,
    ):
        """Low-level HTTP wrapper handling cookies, error pages and retries."""
        headers = {} if headers is None else dict(headers)

        if isinstance(page, str) and page.startswith("/"):
            path = page
        else:
            path = self.api.get_page(page)

        full_path = f"{self.url}{path}"

        try:
            if self.cookie and authenticated:
                headers["Cookie"] = self.cookie
            payload = None
            if data is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                payload = urlencode(data, encoding=self.api.get_encoding())

            async with asyncio.timeout(HTTP_TIMEOUT):
                _LOGGER.debug("Call to: %s", full_path)
                if method == "get":
                    response = await self.session.get(full_path, headers=headers)
                elif method == "post":
                    response = await self.session.post(full_path, data=payload, headers=headers)
                else:
                    raise ValueError(f"Unsupported method '{method}'")

            try:
                preview = await response.text(self.api.get_encoding())
            except Exception:
                preview = "<unreadable>"

            if response.status != 200:
                raise SomfyException(f"Http error ({response.status})")

            if getattr(response.real_url, "path", "") == self.api.get_page(Page.DEFAULT) and retry:
                await self.__login()
                return await self.__do_call(
                    method, page, headers=headers, data=data,
                    retry=False, login=False, authenticated=authenticated
                )

            if getattr(response.real_url, "path", "") == self.api.get_page(Page.ERROR):
                dom = pq(preview)
                error_el = dom(self.api.get_selector(Selector.ERROR_CODE))
                if not error_el:
                    raise SomfyException("Unknown error")
                code = error_el.text()

                if code == SomfyError.NOT_AUTHORIZED and not self.cookie and retry:
                    await self.__login()
                    return await self.__do_call(
                        method, page, headers=headers, data=data,
                        retry=False, login=False, authenticated=authenticated
                    )

                if code == SomfyError.SESSION_ALREADY_OPEN:
                    if retry:
                        form = self.api.get_reset_session_payload()
                        await self.__do_call(
                            "post", Page.ERROR, data=form,
                            retry=False, login=False, authenticated=False
                        )
                        self.cookie = None
                        if login:
                            await self.__login()
                        return await self.__do_call(
                            method, page, headers=headers, data=data,
                            retry=False, login=login, authenticated=authenticated
                        )
                    raise SomfyException("Too many login retries")

                if code == SomfyError.WRONG_CREDENTIALS:
                    raise SomfyException("Login failed: Wrong credentials")
                if code == SomfyError.MAX_LOGIN_ATTEMPS:
                    raise SomfyException("Login failed: Max attempt count reached")
                if code == SomfyError.WRONG_CODE:
                    raise SomfyException("Login failed: Wrong code")
                
                raise SomfyException(f"Command failed: Unknown errorCode: {code}")

            return response

        except (asyncio.TimeoutError, ClientError) as ex:
            _LOGGER.error("Error fetching information from %s - %s", path, ex)
            raise SomfyException(f"Error fetching information from {path} - {ex}") from ex

    async def init(self):
        """Log in once at startup."""
        await self.__login()

    async def get_version(self):
        """Return firmware/version string."""
        version_string = "Unknown"
        try:
            error_response = await self.__do_call(
                "get", Page.LOGIN, login=False, authenticated=False
            )
            dom = pq(await error_response.text(self.api.get_encoding()))
            footer_element = dom(self.api.get_selector(Selector.FOOTER))
            if footer_element is not None:
                matches = re.search(r"([0-9]{4}) somfy", footer_element.text(), re.IGNORECASE)
                if matches:
                    version_string = matches.group(1)

            if self.api.get_page(Page.VERSION) is not None:
                response = await self.__do_call(
                    "get", Page.VERSION, login=False, authenticated=False
                )
                version = await response.text(self.api.get_encoding())
                version_string += f" ({version.strip()})"
        except Exception as exception:
            _LOGGER.error("Failed to extract version: %s", exception)
        return version_string

    def load_api(self, api_type: ApiType):
        """Create the proper API adapter based on centrale type."""
        if api_type == ApiType.PROTEXIAL:
            return ProtexialApi()
        elif api_type == ApiType.PROTEXIAL_IO:
            return ProtexialIOApi()
        elif api_type == ApiType.PROTEXIOM:
            return ProtexiomApi()
        elif api_type is not None:
            raise SomfyException(f"Unknown api type: {api_type}")

    async def guess_and_set_api_type(self):
        """Try different API flavors to detect the centrale type."""
        for api_type in [ApiType.PROTEXIAL_IO, ApiType.PROTEXIAL, ApiType.PROTEXIOM]:
            self.api = self.load_api(api_type)
            versionPage = self.api.get_page(Page.VERSION)
            loginPage = self.api.get_page(Page.LOGIN)
            
            login_body = await self.do_guess_get(loginPage)
            if login_body:
                dom = pq(login_body)
                challenge_el = dom(self.api.get_selector(Selector.LOGIN_CHALLENGE))
                if challenge_el and re.match(CHALLENGE_REGEX, challenge_el.text()):
                    self.api_type = api_type
                    return self.api_type
        raise SomfyException("Couldn't detect the centrale type")

    async def do_guess_get(self, page) -> str:
        """Helper for API guessing."""
        try:
            async with asyncio.timeout(HTTP_TIMEOUT):
                response = await self.session.get(self.url + page, headers={}, allow_redirects=False)
            if response.status == 200:
                return await response.text(self.api.get_encoding())
        except Exception:
            return None
        return None

    async def get_challenge(self):
        """Read the login challenge (grid coordinate)."""
        login_response = await self.__do_call("get", Page.LOGIN, login=False)
        dom = pq(await login_response.text(self.api.get_encoding()))
        challenge_element = dom(self.api.get_selector(Selector.LOGIN_CHALLENGE))
        if challenge_element:
            return challenge_element.text()
        raise SomfyException("Challenge not found")

    async def __login(self, username=None, password=None, code=None):
        """Perform login and store the session cookie."""
        self.cookie = None
        if code is None:
            challenge = await self.get_challenge()
            code = self.codes[challenge]

        form = self.api.get_login_payload(
            username if username else self.username,
            password if password else self.password,
            code,
        )
        login_response = await self.__do_call(
            "post", Page.LOGIN, data=form, retry=False, login=False
        )
        self.cookie = login_response.headers.get("SET-COOKIE")

    async def logout(self):
        """Logout and clear session cookie."""
        await self.__do_call("get", Page.LOGOUT, retry=False, login=False)
        self.cookie = None

    async def get_status(self):
        """Fetch and parse status.xml AND journal.htm into a Status object."""
        # 1. Statut XML classique
        status_response = await self.__do_call(
            "get", Page.STATUS, login=False, authenticated=False
        )
        content = await status_response.text(self.api.get_encoding())
        response = ET.fromstring(content)
        status = Status()
        for child in response:
            filteredChildText = self.filter_ascii(child.text)
            match child.tag:
                case "defaut0": status.battery = filteredChildText
                case "defaut1": status.radio = filteredChildText
                case "defaut2": status.door = filteredChildText
                case "defaut3": status.alarm = filteredChildText
                case "defaut4": status.box = filteredChildText
                case "zone0": status.zoneA = filteredChildText
                case "zone1": status.zoneB = filteredChildText
                case "zone2": status.zoneC = filteredChildText
                case "gsm": status.gsm = filteredChildText
                case "recgsm": status.recgsm = filteredChildText
                case "opegsm": status.opegsm = filteredChildText
                case "camera": status.camera = filteredChildText

        # 2. Récupération du journal (Badge/Utilisateur)
        try:
            journal_response = await self.__do_call(
                "get", Page.JOURNAL, login=False, authenticated=True
            )
            journal_html = await journal_response.text(self.api.get_encoding())
            status.journal = self.api.parse_journal(journal_html)
        except Exception as ex:
            _LOGGER.warning("Could not fetch journal: %s", ex)
            status.journal = None

        return status

    def filter_ascii(self, value) -> str:
        """Keep only printable ASCII and lowercase."""
        if value is None:
            return value
        filtered = "".join(filter(lambda x: x in _PRINTABLE_CHARS, value))
        return filtered.lower()

    async def arm(self, zone):
        form = self.api.get_arm_payload(zone)
        await self.__do_call("post", Page.PILOTAGE, data=form)

    async def disarm(self):
        form = self.api.get_disarm_payload()
        await self.__do_call("post", Page.PILOTAGE, data=form)

    async def turn_light_on(self):
        form = self.api.get_turn_light_on_payload()
        await self.__do_call("post", Page.PILOTAGE, data=form)

    async def turn_light_off(self):
        form = self.api.get_turn_light_off_payload()
        await self.__do_call("post", Page.PILOTAGE, data=form)

    async def open_cover(self):
        form = self.api.get_open_cover_payload()
        await self.__do_call("post", Page.PILOTAGE, data=form)

    async def close_cover(self):
        form = self.api.get_close_cover_payload()
        await self.__do_call("post", Page.PILOTAGE, data=form)

    async def stop_cover(self):
        form = self.api.get_stop_cover_payload()
        await self.__do_call("post", Page.PILOTAGE, data=form)

    async def get_elements(self) -> list[dict]:
        """Fetch and parse the elements page."""
        candidates = [LIST_ELEMENTS, LIST_ELEMENTS_ALT, LIST_ELEMENTS_PRINT]
        html = None
        for candidate in candidates:
            try:
                resp = await self.__do_call("get", candidate)
                raw = await resp.read()
                for enc in ("utf-8", "windows-1252", "latin-1"):
                    try:
                        html = raw.decode(enc)
                        break
                    except Exception:
                        continue
                if html: break
            except Exception:
                continue

        if html is None: return []

        def extract_array(name: str) -> list[str]:
            m = re.search(rf'var\s+{name}\s*=\s*\[(.*?)\];', html, re.S | re.I)
            if not m: return []
            parts = [p.strip().strip('"').strip("'") for p in m.group(1).split(",")]
            return [_fix_mojibake(v) for v in parts]

        item_label = extract_array("item_label")
        elt_name = extract_array("elt_name")
        elt_code = extract_array("elt_code")
        elt_pile = extract_array("elt_pile")
        elt_onde = extract_array("elt_onde")
        elt_porte = extract_array("elt_porte")
        elt_zone = extract_array("elt_zone")
        elt_as = extract_array("elt_as")
        elt_maison = extract_array("elt_maison")
        item_pause = extract_array("item_pause")

        n = min(len(item_label), len(elt_name), len(elt_code))
        elements = []
        for i in range(n):
            elements.append({
                "label": _fix_mojibake(item_label[i]),
                "name": _fix_mojibake(elt_name[i]),
                "code": elt_code[i],
                "battery": elt_pile[i] if i < len(elt_pile) else "",
                "comm": elt_onde[i] if i < len(elt_onde) else "itemhidden",
                "door": elt_porte[i] if i < len(elt_porte) else "",
                "zone": _fix_mojibake(elt_zone[i]) if i < len(elt_zone) else "",
                "tamper": elt_as[i] if i < len(elt_as) else "",
                "house": elt_maison[i] if i < len(elt_maison) else "",
                "pause": item_pause[i] if i < len(item_pause) else "",
            })
        return elements
