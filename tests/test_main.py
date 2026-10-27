"""Tests for main.py with a fake decky module and a fake xodus-cli.

Run: python3 tests/test_main.py
"""
import asyncio
import base64
import json
import os
import stat
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="xbox-chat-test-")

# main.py reads its paths from the decky module when it is imported.
decky = types.ModuleType("decky")
decky.DECKY_USER_HOME = os.path.join(TMP, "home")
decky.DECKY_PLUGIN_SETTINGS_DIR = os.path.join(TMP, "settings")
decky.DECKY_PLUGIN_RUNTIME_DIR = os.path.join(TMP, "runtime")
decky.logger = __import__("logging").getLogger("test")
decky.events = []


async def emit(*args):
    decky.events.append(args)

decky.emit = emit
sys.modules["decky"] = decky
sys.path.insert(0, ROOT)
import main  # noqa: E402

account = {"value": "A"}


async def fake_social(request):
    result = {"sent": True, "messageId": "m2"}
    if request["op"] == "friends":
        result = {"friends": [{"xuid": "42", "gamertag": "Pal", "presence": "online"}]}
    elif request["op"] == "conversation":
        result = {"xuid": request["xuid"], "hasMore": False, "messages": [
            {"id": "m1", "sender": "42", "at": "2026-10-26T10:00:00Z", "text": "hi", "hidden": False}]}
    return {"schema": 1, "account": account["value"], "result": result}

main.social = fake_social


async def cache_survives_restart():
    main.WARM_DELAY = 0
    plugin = main.Plugin()
    await plugin._main()
    await asyncio.sleep(0.05)
    assert (await plugin.cached("42"))["conversation"]["messages"][0]["text"] == "hi"
    raw = open(main.CACHE_FILE, "rb").read()
    assert stat.S_IMODE(os.stat(main.CACHE_FILE).st_mode) == 0o600
    assert b"hi" not in raw
    assert json.loads(base64.b64decode(raw))["account"] == "A"
    await plugin._unload()

    main.WARM_DELAY = 999
    plugin = main.Plugin()
    await plugin._main()
    cached = await plugin.cached("42")
    assert cached["friends"][0]["gamertag"] == "Pal" and cached["conversation"], "cache loads after a restart"
    assert cached["friends"][0]["last"]["text"] == "hi"

    account["value"] = "B"
    await plugin.friends()
    assert (await plugin.cached("42"))["conversation"] is None, "a new account clears the cache"
    assert (await plugin.send("42", "yo"))["ok"]
    await plugin._unload()


async def account_switch_at_start():
    # The disk cache holds account B, and the CLI now reports account A.
    account["value"] = "A"
    main.WARM_DELAY = 0
    plugin = main.Plugin()
    await plugin._main()
    await asyncio.sleep(0.05)
    cached = await plugin.cached("42")
    assert plugin.account == "A" and cached["friends"] and cached["conversation"]
    await plugin._unload()


async def input_checks():
    plugin = main.Plugin()
    main.WARM_DELAY = 999
    await plugin._main()
    assert not (await plugin.conversation("0"))["ok"]
    assert not (await plugin.send("42", " "))["ok"]
    assert not (await plugin.send("42", "x" * 257))["ok"]
    await plugin._unload()


for test in (cache_survives_restart, account_switch_at_start, input_checks):
    asyncio.run(test())
    print("ok", test.__name__)
