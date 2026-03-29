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
