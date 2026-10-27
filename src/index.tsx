import {
  ButtonItem,
  DialogButton,
  Focusable,
  PanelSection,
  PanelSectionRow,
  TextField,
  ToggleField,
  staticClasses,
} from "@decky/ui";
import { addEventListener, removeEventListener, callable, definePlugin, toaster } from "@decky/api";
import { useEffect, useRef, useState } from "react";
import { FaArrowRight, FaComments } from "react-icons/fa";

interface LastMessage { at: string; mine: boolean; hidden: boolean; text: string | null }
interface Friend {
  xuid: string; gamertag: string; presence: "online" | "offline" | "unknown"; activity: string | null; last: LastMessage | null;
}
interface Message { id: string; at: string; mine: boolean; hidden: boolean; text: string | null }
interface Conversation { xuid: string; messages: Message[]; hasMore: boolean }
// A message that the user sent, shown before Xbox returns it in the conversation.
interface Pending { id: string; at: string; text: string; sent: boolean }
type Unread = Record<string, number>;
type Result<T> = ({ ok: true } & T) | { ok: false; error: string };

const getFriends = callable<[], Result<{ friends: Friend[]; unread: Unread }>>("friends");
const getConversation = callable<[xuid: string], Result<{ conversation: Conversation }>>("conversation");
const sendMessage = callable<[xuid: string, text: string], Result<{}>>("send");
const checkUnread = callable<[force: boolean], Result<{ unread: Unread; new: string[]; friends: Friend[] }>>("check_unread");
const getCached = callable<[xuid?: string], { friends: Friend[]; unread: Unread; conversation: Conversation | null }>("cached");
const getSettings = callable<[], { background: boolean; interval: number }>("get_settings");
const setBackground = callable<[enabled: boolean], { background: boolean; interval: number }>("set_background");

// The Quick Access panel unmounts the content when it closes.
// Keep the open chat and the draft here, so that they are there again on the next open.
const store: {
  friends: Friend[]; unread: Unread; chat?: Friend; drafts: Record<string, string>; pending: Record<string, Pending[]>;
} = { friends: [], unread: {}, drafts: {}, pending: {} };

const STYLE = `
.xc-row { display: flex; margin: 3px 0; }
.xc-row.mine { justify-content: flex-end; }
.xc-bubble { max-width: 85%; padding: 6px 10px; border-radius: 14px; background: #3d4450;
  white-space: pre-wrap; word-break: break-word; font-size: 14px; line-height: 1.3; }
.xc-row.mine .xc-bubble { background: #1a73d9; color: #fff; }
.xc-bubble.xc-focus { outline: 2px solid #fff; }
.xc-meta { font-size: 10px; opacity: 0.7; margin-top: 2px; }
.xc-row.mine .xc-meta { text-align: right; }
.xc-hidden { font-style: italic; opacity: 0.8; }
`;

const REFRESH_MS = 20_000;

/** Newest message first. Friends without messages follow: online first, then by name. */
function sortFriends(friends: Friend[]): Friend[] {
  const at = (f: Friend) => (f.last ? Date.parse(f.last.at) || 0 : 0);
  return [...friends].sort((a, b) => at(b) - at(a)
    || Number(b.presence === "online") - Number(a.presence === "online")
    || a.gamertag.localeCompare(b.gamertag, undefined, { sensitivity: "base" }));
}

function preview(last: LastMessage): string {
  const text = last.hidden ? "Message not shown" : (last.text ?? "").replace(/\s+/g, " ");
  const short = Array.from(text).length > 40 ? Array.from(text).slice(0, 40).join("") + "..." : text;
  return `${last.mine ? "You: " : ""}${short} · ${time(last.at)}`;
}
const SHOWN_MESSAGES = 30;

function time(at: string): string {
  const d = new Date(at);
  const today = new Date().toDateString() === d.toDateString();
  return today ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : d.toLocaleDateString([], { month: "short", day: "numeric" });
}

function ErrorRow({ error }: { error?: string }) {
  if (!error) return null;
  return <PanelSectionRow><div style={{ color: "#ff8a80", fontSize: "12px" }}>{error}</div></PanelSectionRow>;
}

function FriendList({ open }: { open: (f: Friend) => void }) {
  const [friends, setSorted] = useState(store.friends);
  const setFriends = (list: Friend[]) => { store.friends = sortFriends(list); setSorted(store.friends); };
  const [unread, setUnread] = useState(store.unread);
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string>();
  const [background, setBg] = useState(false);
  const [interval, setInterval_] = useState(300);

  const load = async () => {
    setLoading(true);
    const r = await getFriends();
    setLoading(false);
    if (!r.ok) return setError(r.error);
    setError(undefined);
    store.unread = r.unread;
    setFriends(r.friends);
    setUnread(r.unread);
  };

  const check = async (force: boolean) => {
    setChecking(true);
    const r = await checkUnread(force);
    setChecking(false);
    if (!r.ok) return setError(r.error);
    store.unread = r.unread;
    setUnread(r.unread);
    setFriends(r.friends);
  };

  useEffect(() => {
    getCached().then(c => {
      if (c.friends.length) { setFriends(c.friends); setUnread(c.unread); }
    });
    // The check also fills the conversation cache, so that a chat opens at once.
    load().then(() => check(false));
    getSettings().then(s => { setBg(s.background); setInterval_(s.interval); });
    const listener = addEventListener<[Unread, string[], Friend[]]>("xodus_unread", (u, _names, list) => {
      store.unread = u;
      setUnread(u);
      setFriends(list);
    });
    return () => { removeEventListener("xodus_unread", listener); };
  }, []);

  return (
    <>
      <PanelSection title="New messages">
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={checking} onClick={() => check(true)}>
            {checking ? "Checking..." : "Check for new messages"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Check in the background"
            description={`Reads each conversation every ${interval < 120 ? `${interval} seconds` : `${Math.round(interval / 60)} minutes`} and shows a notification.`}
            checked={background}
            onChange={async v => setBg((await setBackground(v)).background)}
          />
        </PanelSectionRow>
      </PanelSection>
      <PanelSection title="Friends" spinner={loading}>
        <ErrorRow error={error} />
        {friends.length === 0 && !loading && !error && (
          <PanelSectionRow><div>No friends found.</div></PanelSectionRow>
        )}
        {friends.map(f => (
          <PanelSectionRow key={f.xuid}>
            <ButtonItem
              layout="below"
              description={<>
                {f.last && <div>{preview(f.last)}</div>}
                {f.presence === "online" && <div style={{ color: "#7fd36b" }}>{f.activity ?? "Online"}</div>}
              </>}
              onClick={() => open(f)}
            >
              <span style={{ color: f.presence === "online" ? "#7fd36b" : undefined }}>
                {f.gamertag}
              </span>
              {unread[f.xuid] ? <span style={{ marginLeft: "8px", fontWeight: "bold" }}>({unread[f.xuid]} new)</span> : null}
            </ButtonItem>
          </PanelSectionRow>
        ))}
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={loading} onClick={load}>Refresh friends</ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

/**
 * Give an element the Steam gamepad focus. A DOM focus (focusOnMount) shows the cursor,
 * but the gamepad focus stays on the first button, so A presses that button.
 * The Steam navigation node is the context value of a React fiber above the element.
 * This uses Steam internals, so it falls back to a DOM focus when it finds no node.
 */
function takeGamepadFocus(el: HTMLElement) {
  const key = Object.keys(el).find(k => k.startsWith("__reactFiber"));
  let fiber = key ? (el as any)[key] : undefined;
  for (let i = 0; fiber && i < 10; i++, fiber = fiber.return) {
    const node = fiber.memoizedProps?.value;
    if (node && typeof node.BTakeFocus === "function") {
      node.BTakeFocus();
      return;
    }
  }
  el.focus();
}

function Bubble({ mine, text, hidden, meta }: { mine: boolean; text: string | null; hidden?: boolean; meta: string }) {
  return (
    <div className={mine ? "xc-row mine" : "xc-row"}>
      {/* Focusable so that the d-pad can scroll up through the history. */}
      <Focusable className="xc-bubble" focusClassName="xc-focus" noFocusRing onActivate={() => {}}>
        <div className={hidden ? "xc-hidden" : undefined}>{hidden ? "Message not shown" : text}</div>
        <div className="xc-meta">{meta}</div>
      </Focusable>
    </div>
  );
}

function Chat({ friend, back }: { friend: Friend; back: () => void }) {
  const [conv, setConv] = useState<Conversation>();
  const [pending, setPending] = useState<Pending[]>(store.pending[friend.xuid] ?? []);
  const [draft, setDraft] = useState(store.drafts[friend.xuid] ?? "");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const alive = useRef(true);
  const bottom = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLDivElement>(null);

  const updatePending = (next: Pending[]) => {
    store.pending[friend.xuid] = next;
    if (alive.current) setPending(next);
  };

  const load = async () => {
    setLoading(true);
    const r = await getConversation(friend.xuid);
    if (!alive.current) return;
    setLoading(false);
    if (!r.ok) return setError(r.error);
    setError(undefined);
    setConv(r.conversation);
    // The conversation now holds the sent messages.
    updatePending((store.pending[friend.xuid] ?? []).filter(p => !p.sent));
    delete store.unread[friend.xuid];
  };

  useEffect(() => {
    alive.current = true;
    getCached(friend.xuid).then(c => { if (alive.current && c.conversation) setConv(c.conversation); });
    load();
    // Wait one moment, so that Steam first handles the removal of the friend button that had the focus.
    const focus = setTimeout(() => {
      const el = input.current?.querySelector("input");
      if (el) takeGamepadFocus(el);
    }, 50);
    const timer = setInterval(load, REFRESH_MS);
    return () => { alive.current = false; clearTimeout(focus); clearInterval(timer); };
  }, [friend.xuid]);

  const count = (conv?.messages.length ?? 0) + pending.length;
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [count]);

  const edit = (value: string) => {
    setDraft(value);
    store.drafts[friend.xuid] = value;
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || pending.some(p => !p.sent)) return;
    const item: Pending = { id: `pending-${Date.now()}`, at: new Date().toISOString(), text, sent: false };
    updatePending([...(store.pending[friend.xuid] ?? []), item]);
    edit("");
    setError(undefined);
    const r = await sendMessage(friend.xuid, text);
    const rest = (store.pending[friend.xuid] ?? []).filter(p => p.id !== item.id);
    if (!r.ok) {
      // Give the text back, so that the user can send it again.
      updatePending(rest);
      if (alive.current) { setError(r.error); edit(text); }
      return;
    }
    updatePending([...rest, { ...item, sent: true }]);
    if (alive.current) load();
  };

  const messages = conv?.messages.slice(-SHOWN_MESSAGES) ?? [];
  const sending = pending.some(p => !p.sent);
  const tooLong = Array.from(draft).length > 256;

  // Decky closes the plugin on B. Take B here first, so that B goes back to the friends list.
  const onCancel = (e: { stopPropagation(): void }) => {
    e.stopPropagation();
    back();
  };

  return (
    <Focusable onCancelButton={onCancel}>
      <style>{STYLE}</style>
      <PanelSection>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={back} description="Or press B">Back to friends</ButtonItem>
        </PanelSectionRow>
      </PanelSection>
      <PanelSection title={friend.gamertag} spinner={loading}>
        {!conv && loading && <PanelSectionRow><div>Loading the conversation...</div></PanelSectionRow>}
        {conv && messages.length === 0 && pending.length === 0 && (
          <PanelSectionRow><div>No messages yet. Write the first message below.</div></PanelSectionRow>
        )}
        <PanelSectionRow>
          <div style={{ display: "flex", flexDirection: "column" }}>
            {messages.map(m => (
              <Bubble key={m.id} mine={m.mine} text={m.text} hidden={m.hidden} meta={time(m.at)} />
            ))}
            {pending.map(p => (
              <Bubble key={p.id} mine text={p.text} meta={p.sent ? `${time(p.at)} · Sent` : "Sending..."} />
            ))}
          </div>
        </PanelSectionRow>
        <ErrorRow error={error} />
        <PanelSectionRow>
          {/* The text box and the send arrow in one row. Right on the d-pad moves to the arrow. */}
          <Focusable flow-children="horizontal" style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <div ref={input} style={{ flex: 1, minWidth: 0 }}>
          <TextField
            // The Steam TextField passes extra props to the <input>, but its types do not list placeholder.
            {...({ placeholder: "Message" } as {})}
            value={draft}
            onChange={e => edit(e.target.value)}
            // Enter on a hardware keyboard or the Steam keyboard sends the message.
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!tooLong) send(); } }}
            description={tooLong ? "The limit is 256 characters." : undefined}
          />
          </div>
          <div ref={bottom}>
            <DialogButton
              aria-label="Send"
              disabled={sending || !draft.trim() || tooLong}
              onClick={send}
              style={{ minWidth: 0, width: "40px", height: "40px", padding: 0, borderRadius: "50%",
                display: "flex", alignItems: "center", justifyContent: "center" }}
            >
              {sending ? "..." : <FaArrowRight />}
            </DialogButton>
          </div>
          </Focusable>
        </PanelSectionRow>
      </PanelSection>
    </Focusable>
  );
}

function Content() {
  const [chat, setChat] = useState<Friend | undefined>(store.chat);
  const open = (f?: Friend) => { store.chat = f; setChat(f); };
  return chat ? <Chat friend={chat} back={() => open(undefined)} /> : <FriendList open={open} />;
}

export default definePlugin(() => {
  const listener = addEventListener<[Unread, string[]]>("xodus_unread", (unread, names) => {
    store.unread = unread;
    if (names.length) toaster.toast({
      title: "Xbox message",
      body: names.length === 1 ? `New message from ${names[0]}` : `New messages from ${names.join(", ")}`,
      icon: <FaComments />,
    });
  });

  return {
    name: "XBOX Chat",
    titleView: <div className={staticClasses.Title}>XBOX Chat</div>,
    content: <Content />,
    icon: <FaComments />,
    onDismount() {
      removeEventListener("xodus_unread", listener);
    },
  };
});
