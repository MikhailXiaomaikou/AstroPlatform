import { useState, useEffect, useRef } from "react";

interface PresenceUser { name: string; status: string }

export default function PresenceBar({ teamId, userId, userName }: { teamId: string; userId: string; userName: string }) {
  const [users, setUsers] = useState<Record<string, PresenceUser>>({});
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!teamId) return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const baseUrl = import.meta.env.VITE_API_URL || `${window.location.protocol}//${window.location.hostname}:8000`;
    const wsUrl = baseUrl.replace(/^https?:/, protocol);
    const ws = new WebSocket(`${wsUrl}/ws/collab/${teamId}`);
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "identify", user_id: userId, name: userName }));
    };
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "presence") setUsers(data.users || {});
    };
    return () => { ws.close(); };
  }, [teamId, userId, userName]);

  const onlineUsers = Object.entries(users).filter(([, u]) => u.status === "online");
  if (onlineUsers.length === 0) return null;

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: "0.8rem", padding: "4px 8px" }}>
      {onlineUsers.map(([uid, u]) => (
        <span key={uid} style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#22c55e", display: "inline-block" }} />
          {u.name}
        </span>
      ))}
    </div>
  );
}
