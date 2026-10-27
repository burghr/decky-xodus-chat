import asyncio
import base64
import json
import os
import re
import time

import decky

# The Nexus backend CLI (xodus-cli) does the Xbox calls. It reads the
# signed-in account from the system wallet, so this plugin never sees tokens.
NEXUS_CONFIG = os.path.join(decky.DECKY_USER_HOME, ".config", "xodus-nexus")
BACKEND_POINTER = os.path.join(NEXUS_CONFIG, "xodus-backends", "current.json")
STATE_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "state.json")
# Friends and messages from the last session, so that the panel shows them at
# once after a restart. Base64 is only an encoding against a casual read, not
# encryption. The file mode 0600 keeps other users out.
CACHE_FILE = os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "cache.b64")
# Wait after the plugin start before the first refresh, so that the boot stays fast.
WARM_DELAY = 20

XUID = re.compile(r"^[1-9][0-9]{0,19}$")
ERROR_LINE = re.compile(r"^NEXUS_SOCIAL_ERROR_V1 ([a-z-]+)\r?$")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
MAX_OUTPUT = 2 * 1024 * 1024
TIMEOUT = 65
# Each CLI call needs about 2 s (token step + HTTP). Calls in parallel take
# about the same time as one call, so run a small number at the same time.
PARALLEL_READS = 4
# Without force, check_unread skips the Xbox calls when the last check is newer.
CHECK_MIN_AGE = 60
# Seconds between two background checks.
POLL_INTERVAL = 120

ERRORS = {
    "privacy-or-access-denied": "Xbox denied this request. Check your privacy and communication settings.",
    "sign-in-required": "Xbox needs a new sign-in. Open Xodus Nexus and sign in again.",
    "rate-limited": "Xbox limits the requests now. Wait, then try again.",
    "not-found": "This conversation is not available from Xbox.",
}
UNAVAILABLE = "Xbox social services are not available. Try again later."
UNCONFIRMED = "Xbox did not confirm the message. Refresh the conversation before you send it again."


class SocialError(Exception):
    pass


def valid_xuid(value) -> bool:
    return isinstance(value, str) and bool(XUID.match(value)) and int(value) <= 18446744073709551615


def valid_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 256 and not CONTROL.search(value)


def find_cli() -> str:
    try:
        with open(BACKEND_POINTER, encoding="utf-8") as f:
            binary = json.load(f).get("binaryPath")
    except (OSError, ValueError):
        raise SocialError("Xodus Nexus is not installed, or it has no backend. Open Xodus Nexus one time.")
    root = os.path.realpath(os.path.join(NEXUS_CONFIG, "xodus-backends"))
    if not isinstance(binary, str) or not os.path.realpath(binary).startswith(root + os.sep) \
            or not os.access(binary, os.X_OK):
        raise SocialError("The Xodus Nexus backend is missing. Open Xodus Nexus to repair it.")
    return binary


def cli_env() -> dict:
    # Decky starts plugins without the desktop session variables.
    # The CLI needs the session bus to read the wallet.
    env = dict(os.environ)
    uid = os.getuid()
    env["HOME"] = decky.DECKY_USER_HOME
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    return env


async def social(request: dict) -> dict:
    proc = await asyncio.create_subprocess_exec(
        find_cli(), "social", "--json",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=cli_env(), cwd=decky.DECKY_USER_HOME,
    )
    try:
        # Do not log the request or the output: they hold messages and friends.
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(request).encode()), TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise SocialError(UNCONFIRMED if request["op"] == "send" else UNAVAILABLE)
    stage = None
    for line in err.decode("utf-8", "replace").splitlines()[-50:]:
        match = ERROR_LINE.match(line)
        if match and match.group(1) in ERRORS:
            stage = match.group(1)
    if proc.returncode != 0:
        decky.logger.warning("social %s failed: code %s, stage %s", request["op"], proc.returncode, stage)
        if request["op"] == "send" and stage is None:
            raise SocialError(UNCONFIRMED)
        raise SocialError(ERRORS.get(stage, UNAVAILABLE))
    if len(out) > MAX_OUTPUT:
        raise SocialError(UNAVAILABLE)
    try:
        value = json.loads(out)
    except ValueError:
        raise SocialError(UNAVAILABLE)
    if not isinstance(value, dict) or value.get("schema") != 1 or not isinstance(value.get("result"), dict) \
            or not isinstance(value.get("account"), str):
        raise SocialError(UNAVAILABLE)
    return value


def clean_friends(result: dict) -> list:
    friends = []
    for f in result.get("friends") or []:
        if not isinstance(f, dict) or not valid_xuid(f.get("xuid")) or not isinstance(f.get("gamertag"), str):
            continue
        presence = f.get("presence") if f.get("presence") in ("online", "offline") else "unknown"
        activity = f.get("activity") if isinstance(f.get("activity"), str) else None
        friends.append({"xuid": f["xuid"], "gamertag": f["gamertag"], "presence": presence, "activity": activity})
    friends.sort(key=lambda f: (f["presence"] != "online", f["gamertag"].lower()))
    return friends


def clean_conversation(result: dict, xuid: str) -> dict:
    if result.get("xuid") != xuid or not isinstance(result.get("messages"), list):
        raise SocialError(UNAVAILABLE)
    messages = []
    for m in result["messages"]:
        if not isinstance(m, dict) or not isinstance(m.get("id"), str) or not isinstance(m.get("at"), str):
            continue
        hidden = m.get("hidden") is True or not isinstance(m.get("text"), str)
        messages.append({
            "id": m["id"], "at": m["at"], "mine": m.get("sender") != xuid,
            "hidden": hidden, "text": None if hidden else m["text"],
        })
    messages.sort(key=lambda m: m["at"])
    return {"xuid": xuid, "messages": messages, "hasMore": result.get("hasMore") is True}


class Plugin:
    async def _main(self):
        self.send_lock = asyncio.Lock()
        # Xbox limits the request rate, so keep the number of parallel CLIs small.
        self.read_slots = asyncio.Semaphore(PARALLEL_READS)
        self.check_lock = asyncio.Lock()
        self.last_check = 0.0
        self.conversations = {}
        self.friends_cache = []
        self.account = None
        self.friends_fresh = False
        self.state = {"seen": {}, "unread": {}, "background": False}
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                self.state.update(json.load(f))
            # Older versions saved an interval. The interval is now fixed.
            self.state.pop("interval", None)
        except (OSError, ValueError):
            pass
        self.load_cache()
        loop = asyncio.get_event_loop()
        self.poller = loop.create_task(self.poll_loop())
        self.warmer = loop.create_task(self.warm())
        decky.logger.info("XBOX Chat started")

    async def _unload(self):
        self.poller.cancel()
        self.warmer.cancel()

    async def _uninstall(self):
        for path in (CACHE_FILE, STATE_FILE):
            try:
                os.remove(path)
            except OSError:
                pass

    def load_cache(self):
        try:
            with open(CACHE_FILE, "rb") as f:
                data = json.loads(base64.b64decode(f.read()))
            if isinstance(data.get("account"), str) and isinstance(data.get("friends"), list) \
                    and isinstance(data.get("conversations"), dict):
                self.account = data["account"]
                self.friends_cache = data["friends"]
                self.conversations = data["conversations"]
        except (OSError, ValueError, AttributeError):
            pass

    def save_cache(self):
        if not self.account:
            return
        data = {"account": self.account, "friends": self.friends_cache, "conversations": self.conversations}
        os.makedirs(decky.DECKY_PLUGIN_RUNTIME_DIR, exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(base64.b64encode(json.dumps(data).encode()))
        os.replace(tmp, CACHE_FILE)

    async def warm(self):
        """Fill the cache in the background after the plugin start."""
        await asyncio.sleep(WARM_DELAY)
        result = await self.check_unread(force=True)
        if result["ok"]:
            await decky.emit("xodus_unread", result["unread"], result["new"], result["friends"])
        else:
            decky.logger.info("first refresh failed: %s", result["error"])

    def save_state(self):
        os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f)
        os.replace(tmp, STATE_FILE)

    async def call(self, request: dict) -> dict:
        if request["op"] == "send":
            value = await social(request)
        else:
            async with self.read_slots:
                value = await social(request)
        if value["account"] != self.account:
            # A different Xbox account: never show the cache of the old account.
            self.account = value["account"]
            self.friends_cache = []
            self.conversations = {}
            self.state.update(seen={}, unread={})
        return value["result"]

    async def read_conversation(self, xuid: str) -> dict:
        conv = clean_conversation(await self.call({"op": "conversation", "xuid": xuid}), xuid)
        self.conversations[xuid] = conv
        return conv

    def note_conversation(self, conv: dict, opened: bool) -> bool:
        """Update the unread count. Return True when a new message from the friend arrived."""
        theirs = [m for m in conv["messages"] if not m["mine"]]
        xuid = conv["xuid"]
        if opened or not theirs:
            if theirs:
                self.state["seen"][xuid] = theirs[-1]["id"]
            self.state["unread"].pop(xuid, None)
            return False
        seen = self.state["seen"].get(xuid)
        if seen is None:
            # First check for this friend: accept the history as read.
            self.state["seen"][xuid] = theirs[-1]["id"]
            return False
        ids = [m["id"] for m in theirs]
        count = len(ids) - 1 - ids.index(seen) if seen in ids else len(ids)
        before = self.state["unread"].get(xuid, 0)
        if count:
            self.state["unread"][xuid] = count
        else:
            self.state["unread"].pop(xuid, None)
        return count > before

    # Methods for the frontend. Each returns {"ok": ..., "error": ...} so the
    # frontend can show a fixed message and never a Python trace.

    def friend_rows(self) -> list:
        """Friends with the newest message of each cached conversation. The frontend sorts them."""
        rows = []
        for f in self.friends_cache:
            msgs = (self.conversations.get(f["xuid"]) or {}).get("messages") or []
            last = msgs[-1] if msgs else None
            rows.append({**f, "last": last and {k: last[k] for k in ("at", "mine", "hidden", "text")}})
        return rows

    async def cached(self, xuid=None):
        """Return the memory cache at once, so that the frontend can show it before a refresh."""
        return {
            "friends": self.friend_rows(), "unread": self.state["unread"],
            "conversation": self.conversations.get(xuid) if xuid else None,
        }

    async def friends(self):
        try:
            self.friends_cache = clean_friends(await self.call({"op": "friends"}))
            self.friends_fresh = True
            self.save_cache()
            return {"ok": True, "friends": self.friend_rows(), "unread": self.state["unread"]}
        except SocialError as e:
            return {"ok": False, "error": str(e)}

    async def conversation(self, xuid):
        if not valid_xuid(xuid):
            return {"ok": False, "error": "Not a valid Xbox user."}
        try:
            conv = await self.read_conversation(xuid)
        except SocialError as e:
            return {"ok": False, "error": str(e)}
        self.note_conversation(conv, opened=True)
        self.save_state()
        self.save_cache()
        return {"ok": True, "conversation": conv}

    async def send(self, xuid, text):
        if not valid_xuid(xuid) or not valid_text(text):
            return {"ok": False, "error": "Enter a message of 256 characters or less."}
        if self.send_lock.locked():
            return {"ok": False, "error": "A message is already in transit."}
        async with self.send_lock:
            try:
                result = await self.call({"op": "send", "xuid": xuid, "text": text})
            except SocialError as e:
                return {"ok": False, "error": str(e)}
        if result.get("sent") is not True:
            return {"ok": False, "error": UNCONFIRMED}
        return {"ok": True}

    async def check_unread(self, force=False):
        """Read the conversation of each friend in parallel and update the unread counts."""
        async with self.check_lock:
            if not force and time.monotonic() - self.last_check < CHECK_MIN_AGE:
                return {"ok": True, "unread": self.state["unread"], "new": [], "friends": self.friend_rows()}
            try:
                # The friends from the disk cache can be old, so read them one time per session.
                if not self.friends_fresh or not self.friends_cache:
                    self.friends_cache = clean_friends(await self.call({"op": "friends"}))
                    self.friends_fresh = True
                friends = list(self.friends_cache)
                account = self.account
                results = await asyncio.gather(
                    *(self.read_conversation(f["xuid"]) for f in friends), return_exceptions=True)
                if self.account != account:
                    # The account changed during the reads, and call() cleared the cache. Start again.
                    self.friends_cache = clean_friends(await self.call({"op": "friends"}))
                    friends = list(self.friends_cache)
                    results = await asyncio.gather(
                        *(self.read_conversation(f["xuid"]) for f in friends), return_exceptions=True)
                new = []
                for friend, conv in zip(friends, results):
                    if isinstance(conv, SocialError) and str(conv) == ERRORS["rate-limited"]:
                        raise conv
                    if isinstance(conv, dict) and self.note_conversation(conv, opened=False):
                        new.append(friend["gamertag"])
                self.last_check = time.monotonic()
                self.save_state()
                self.save_cache()
                return {"ok": True, "unread": self.state["unread"], "new": new, "friends": self.friend_rows()}
            except SocialError as e:
                return {"ok": False, "error": str(e)}

    async def get_settings(self):
        return {"background": self.state["background"], "interval": POLL_INTERVAL}

    async def set_background(self, enabled):
        self.state["background"] = enabled is True
        self.save_state()
        return await self.get_settings()

    async def poll_loop(self):
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            if not self.state["background"]:
                continue
            result = await self.check_unread(force=True)
            if result["ok"]:
                await decky.emit("xodus_unread", result["unread"], result["new"], result["friends"])
            else:
                decky.logger.info("background check failed: %s", result["error"])
