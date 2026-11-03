"use strict";

const http = require("http");
const crypto = require("crypto");
const WebSocket = require("ws");

const PORT = Number(process.env.PORT || 8787);
const SECRET = process.env.SMARTLINK_SECRET || "";

if (SECRET.length < 32) {
  console.error(
    "ERROR: Set SMARTLINK_SECRET to a secret of at least 32 characters."
  );
  process.exit(1);
}

const peers = new Map();

const server = http.createServer((req, res) => {
  res.setHeader("Cache-Control", "no-store");

  if (req.url === "/health") {
    res.writeHead(200, {
      "Content-Type": "application/json"
    });

    return res.end(JSON.stringify({
      ok: true,
      service: "SmartLink PC Relay",
      transport: "TLS/WSS reverse proxy required",
      remoteControlReady: false
    }));
  }

  res.writeHead(404, {
    "Content-Type": "text/plain; charset=utf-8"
  });

  res.end("Not found");
});

const wss = new WebSocket.Server({
  server,
  path: "/ws",
  maxPayload: 2 * 1024 * 1024
});

function safeEqual(a, b) {
  const x = Buffer.from(String(a || ""));
  const y = Buffer.from(String(b || ""));

  return (
    x.length === y.length &&
    crypto.timingSafeEqual(x, y)
  );
}

function send(ws, data) {
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(data));
  }
}

function removePeer(ws) {
  if (!ws.deviceId || !peers.has(ws.deviceId)) return;

  const group = peers.get(ws.deviceId);
  group.delete(ws);

  for (const peer of group) {
    if (peer.role !== ws.role) {
      send(peer, {
        type: "peer_offline",
        role: ws.role,
        deviceId: ws.deviceId
      });
    }
  }

  if (group.size === 0) {
    peers.delete(ws.deviceId);
  }
}

wss.on("connection", (ws) => {
  ws.authenticated = false;
  ws.role = null;
  ws.deviceId = null;

  const authTimer = setTimeout(() => {
    if (!ws.authenticated) {
      send(ws, {
        type: "error",
        message: "Authentication timeout"
      });

      ws.close(1008, "Authentication timeout");
    }
  }, 8000);

  ws.on("message", (raw) => {
    let msg;

    try {
      msg = JSON.parse(raw.toString());
    } catch {
      send(ws, {
        type: "error",
        message: "Invalid JSON"
      });
      return;
    }

    if (!ws.authenticated) {
      const validDevice =
        typeof msg.deviceId === "string" &&
        /^[a-zA-Z0-9_-]{1,64}$/.test(msg.deviceId);

      const validRole =
        msg.role === "controller" ||
        msg.role === "device";

      if (
        msg.type !== "authenticate" ||
        !validDevice ||
        !validRole ||
        !safeEqual(msg.secret, SECRET)
      ) {
        ws.close(1008, "Authentication failed");
        return;
      }

      ws.authenticated = true;
      ws.role = msg.role;
      ws.deviceId = msg.deviceId;

      clearTimeout(authTimer);

      if (!peers.has(ws.deviceId)) {
        peers.set(ws.deviceId, new Set());
      }

      const group = peers.get(ws.deviceId);
      group.add(ws);

      send(ws, {
        type: "authenticated",
        role: ws.role,
        deviceId: ws.deviceId
      });

      for (const peer of group) {
        if (peer !== ws && peer.role !== ws.role) {
          send(peer, {
            type: "peer_online",
            role: ws.role,
            deviceId: ws.deviceId
          });
        }
      }

      return;
    }

    const allowed = new Set([
      "status_request",
      "status_response",
      "device_status",
      "screen_start",
      "screen_stop",
      "screen_frame",
      "clipboard_write",
      "mouse_click",
      "mouse_double_click",
      "mouse_move",
      "mouse_scroll",
      "key_press",
      "keyboard_text",
      "power",
      "voice_command",
      "command_result"
    ]);

    if (!allowed.has(msg.type)) {
      send(ws, {
        type: "error",
        message: "Message type not allowed"
      });
      return;
    }

    const controllerOnly = [
      "screen_start",
      "screen_stop",
      "clipboard_write",
      "mouse_click",
      "mouse_double_click",
      "mouse_move",
      "mouse_scroll",
      "key_press",
      "keyboard_text",
      "power",
      "voice_command",
      "status_request"
    ];

    const deviceOnly = [
      "status_response",
      "device_status",
      "screen_frame",
      "command_result"
    ];

    if (
      ws.role === "controller" &&
      deviceOnly.includes(msg.type)
    ) {
      send(ws, {
        type: "error",
        message: "Controller cannot send this message"
      });
      return;
    }

    if (
      ws.role === "device" &&
      controllerOnly.includes(msg.type)
    ) {
      send(ws, {
        type: "error",
        message: "Device cannot issue controller commands"
      });
      return;
    }

    const group = peers.get(ws.deviceId) || new Set();
    let delivered = 0;

    for (const peer of group) {
      if (
        peer !== ws &&
        peer.role !== ws.role
      ) {
        send(peer, msg);
        delivered++;
      }
    }

    if (
      msg.type === "status_request" &&
      delivered === 0
    ) {
      send(ws, {
        type: "error",
        message: "Windows Companion is offline"
      });
    }
  });

  ws.on("close", () => {
    clearTimeout(authTimer);
    removePeer(ws);
  });

  ws.on("error", () => {
    // Avoid exposing internal errors to remote clients.
  });
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(
    `SmartLink PC relay listening on port ${PORT}`
  );

  console.log(
    "Deploy behind a trusted TLS/WSS reverse proxy."
  );
});
