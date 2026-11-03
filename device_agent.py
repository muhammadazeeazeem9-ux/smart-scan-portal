"""
SmartLink PC - Windows Companion
Secure remote connection agent.

Requirements:
    pip install -r requirements.txt

Required environment variables:
    SMARTLINK_WS_URL=wss://your-domain.com/ws
    SMARTLINK_SECRET=your-long-random-secret
    SMARTLINK_DEVICE_ID=my-pc

Optional:
    SMARTLINK_ENABLE_INPUT=1
    SMARTLINK_ENABLE_SCREEN=1
"""

import asyncio
import base64
import io
import json
import os
import platform
import shutil
import time

import websockets


# ==============================
# CONNECTION SETTINGS
# ==============================

SERVER_URL = os.environ.get("SMARTLINK_WS_URL", "").strip()
SECRET = os.environ.get("SMARTLINK_SECRET", "")
DEVICE_ID = os.environ.get("SMARTLINK_DEVICE_ID", "my-pc").strip()

ENABLE_INPUT = os.environ.get("SMARTLINK_ENABLE_INPUT", "0") == "1"
ENABLE_SCREEN = os.environ.get("SMARTLINK_ENABLE_SCREEN", "0") == "1"

SCREEN_FPS = max(
    1,
    min(3, int(os.environ.get("SMARTLINK_SCREEN_FPS", "1")))
)


# ==============================
# VALIDATION
# ==============================

if not SERVER_URL.startswith("wss://"):
    raise SystemExit(
        "ERROR: Set SMARTLINK_WS_URL to a trusted wss:// address."
    )

if len(SECRET) < 32:
    raise SystemExit(
        "ERROR: SMARTLINK_SECRET must contain at least 32 characters."
    )

if not DEVICE_ID or len(DEVICE_ID) > 64:
    raise SystemExit("ERROR: Invalid device ID.")

try:
    import pyautogui
except ImportError:
    pyautogui = None

try:
    import pyperclip
except ImportError:
    pyperclip = None

try:
    import mss
    from PIL import Image
except ImportError:
    mss = None
    Image = None


# ==============================
# DEVICE STATUS
# ==============================

def get_status():

    usage = shutil.disk_usage(os.path.abspath(os.sep))

    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "hostname": platform.node(),

        "disk_root_total_bytes": usage.total,
        "disk_root_free_bytes": usage.free,

        "timestamp_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime()
        ),

        "capabilities": {
            "status": True,
            "remote_mouse": bool(ENABLE_INPUT and pyautogui),
            "remote_keyboard": bool(ENABLE_INPUT and pyautogui),
            "unicode_text": bool(
                ENABLE_INPUT and pyautogui and pyperclip
            ),
            "remote_power": False,
            "clipboard_sync": bool(ENABLE_INPUT and pyperclip),
            "screen_stream": bool(
                ENABLE_SCREEN and mss and Image
            ),
            "file_transfer": False
        }
    }


# ==============================
# SAFE INPUT CONTROL
# ==============================

def do_input(msg):

    if not ENABLE_INPUT or pyautogui is None:
        return False, "Remote input is disabled on this computer."

    kind = msg.get("type")

    if kind == "mouse_move":

        x = max(0, min(1000, int(msg.get("x", 500))))
        y = max(0, min(1000, int(msg.get("y", 500))))

        width, height = pyautogui.size()

        pyautogui.moveTo(
            round(x * (width - 1) / 1000),
            round(y * (height - 1) / 1000),
            duration=0.05
        )

    elif kind in ("mouse_click", "mouse_double_click"):

        button = msg.get("button", "left")

        if button not in ("left", "right", "middle"):
            return False, "Unsupported mouse button."

        pyautogui.click(
            button=button,
            clicks=2 if kind == "mouse_double_click" else 1,
            interval=0.12
        )

    elif kind == "mouse_scroll":

        amount = max(
            -10,
            min(10, int(msg.get("amount", -3)))
        )

        pyautogui.scroll(amount)

    elif kind == "key_press":

        shortcuts = {
            "enter": lambda: pyautogui.press("enter"),
            "escape": lambda: pyautogui.press("esc"),
            "tab": lambda: pyautogui.press("tab"),
            "backspace": lambda: pyautogui.press("backspace"),
            "copy": lambda: pyautogui.hotkey("ctrl", "c"),
            "paste": lambda: pyautogui.hotkey("ctrl", "v"),
            "select_all": lambda: pyautogui.hotkey("ctrl", "a")
        }

        key = str(msg.get("key", "")).lower()

        if key not in shortcuts:
            return False, "Shortcut not allowed."

        shortcuts[key]()

    elif kind == "keyboard_text":

        text = msg.get("text", "")

        if not isinstance(text, str) or len(text) > 2000:
            return False, "Text must be 2000 characters or less."

        if not text:
            return False, "Text is empty."

        if not text.isascii():

            if pyperclip is None:
                return False, "Unicode typing requires pyperclip."

            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")

        else:
            pyautogui.write(text, interval=0.01)

    elif kind == "voice_command":

        command = str(
            msg.get("text", "")
        ).strip().lower()

        actions = {
            "open run dialog": lambda: pyautogui.hotkey("win", "r"),
            "show desktop": lambda: pyautogui.hotkey("win", "d"),
            "switch window": lambda: pyautogui.hotkey("alt", "tab"),
            "go back": lambda: pyautogui.hotkey("alt", "left"),
            "scroll down": lambda: pyautogui.scroll(-4),
            "scroll up": lambda: pyautogui.scroll(4),
            "press enter": lambda: pyautogui.press("enter"),
            "press escape": lambda: pyautogui.press("esc"),
            "copy": lambda: pyautogui.hotkey("ctrl", "c"),
            "paste": lambda: pyautogui.hotkey("ctrl", "v")
        }

        if command not in actions:
            return False, "Voice command is not approved."

        actions[command]()

    else:
        return False, "Unsupported command."

    return True, "Command executed."


# ==============================
# SCREEN CAPTURE
# ==============================

async def stream_screen(ws, stop_event):

    if not ENABLE_SCREEN or mss is None or Image is None:

        await ws.send(json.dumps({
            "type": "command_result",
            "ok": False,
            "message": "Screen streaming is disabled or dependencies are missing."
        }))

        return

    try:

        with mss.mss() as capture:

            monitor = capture.monitors[1]

            while not stop_event.is_set():

                shot = capture.grab(monitor)

                image = Image.frombytes(
                    "RGB",
                    shot.size,
                    shot.rgb
                )

                image.thumbnail((960, 540))

                buffer = io.BytesIO()

                image.save(
                    buffer,
                    format="JPEG",
                    quality=45,
                    optimize=True
                )

                await ws.send(json.dumps({
                    "type": "screen_frame",
                    "mime": "image/jpeg",
                    "data": base64.b64encode(
                        buffer.getvalue()
                    ).decode("ascii"),
                    "timestamp": time.time()
                }))

                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=1 / SCREEN_FPS
                    )
                except asyncio.TimeoutError:
                    pass

    except Exception as exc:

        await ws.send(json.dumps({
            "type": "command_result",
            "ok": False,
            "message": "Screen capture failed: " + str(exc)
        }))


# ==============================
# MAIN CONNECTION
# ==============================

async def main():

    delay = 2

    while True:

        try:

            async with websockets.connect(
                SERVER_URL,
                max_size=65536,
                ping_interval=20,
                ping_timeout=20
            ) as ws:

                await ws.send(json.dumps({
                    "type": "authenticate",
                    "role": "device",
                    "deviceId": DEVICE_ID,
                    "secret": SECRET
                }))

                reply = json.loads(
                    await asyncio.wait_for(ws.recv(), timeout=10)
                )

                if reply.get("type") != "authenticated":
                    raise RuntimeError("Server authentication failed.")

                print("SmartLink PC connected successfully.")
                print("Input enabled:", bool(ENABLE_INPUT and pyautogui))
                print("Screen enabled:", bool(ENABLE_SCREEN and mss and Image))

                delay = 2

                screen_task = None
                screen_stop = asyncio.Event()

                async for raw in ws:

                    try:
                        msg = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue

                    kind = msg.get("type")

                    if kind == "status_request":

                        await ws.send(json.dumps({
                            "type": "status_response",
                            "deviceId": DEVICE_ID,
                            "status": get_status()
                        }))

                    elif kind == "screen_start":

                        if screen_task and not screen_task.done():
                            continue

                        screen_stop = asyncio.Event()

                        screen_task = asyncio.create_task(
                            stream_screen(ws, screen_stop)
                        )

                    elif kind == "screen_stop":

                        screen_stop.set()

                        if screen_task:
                            screen_task.cancel()
                            screen_task = None

                        await ws.send(json.dumps({
                            "type": "command_result",
                            "ok": True,
                            "message": "Screen stream stopped."
                        }))

                    elif kind in (
                        "clipboard_write",
                        "mouse_click",
                        "mouse_double_click",
                        "mouse_move",
                        "mouse_scroll",
                        "key_press",
                        "keyboard_text",
                        "voice_command",
                        "power"
                    ):

                        if kind == "power":

                            ok = False
                            detail = "Power actions are disabled for safety."

                        elif kind == "clipboard_write":

                            text = msg.get("text", "")

                            if not ENABLE_INPUT:
                                ok = False
                                detail = "Input is disabled."

                            elif pyperclip is None:
                                ok = False
                                detail = "Install pyperclip first."

                            elif not isinstance(text, str) or len(text) > 10000:
                                ok = False
                                detail = "Clipboard text is invalid or too long."

                            else:

                                try:
                                    pyperclip.copy(text)
                                    ok = True
                                    detail = "Text copied to PC clipboard."

                                except Exception as exc:
                                    ok = False
                                    detail = "Clipboard failed: " + str(exc)

                        else:

                            try:
                                ok, detail = do_input(msg)

                            except Exception as exc:
                                ok = False
                                detail = "Action failed: " + str(exc)

                        await ws.send(json.dumps({
                            "type": "command_result",
                            "deviceId": DEVICE_ID,
                            "ok": ok,
                            "message": detail
                        }))

                screen_stop.set()

                if screen_task and not screen_task.done():
                    screen_task.cancel()

        except Exception as exc:

            print(
                f"Connection error: {exc}. Retrying in {delay} seconds."
            )

            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)


# ==============================
# START
# ==============================

if __name__ == "__main__":
    asyncio.run(main())
