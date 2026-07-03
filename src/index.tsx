import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  staticClasses,
  Spinner,
  Field,
} from "@decky/ui";
import { callable, definePlugin, toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { FaCouch, FaRotate, FaKey, FaTrash } from "react-icons/fa6";
import { QRCodeSVG } from "qrcode.react";

// ---- backend bridges (names match main.py's Plugin methods) --------------
type Status = { installed: boolean; running: boolean; port: number; agent_version?: string | null };
type Pairing = { ok: boolean; host?: string; port?: number; token?: string; pair_url?: string; error?: string };
type Result = { ok: boolean; error?: string };

const bInstall = callable<[], Result>("install");
const bStatus = callable<[], Status>("status");
const bPairing = callable<[], Pairing>("get_pairing");
const bRestart = callable<[], Result>("restart_agent");
const bRegen = callable<[], Result & { token?: string }>("regenerate_token");
const bUninstall = callable<[boolean], Result>("uninstall");

function Content() {
  const [status, setStatus] = useState<Status | null>(null);
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = async () => {
    try {
      const s = await bStatus();
      setStatus(s);
      if (s.installed) setPairing(await bPairing());
    } catch (e) {
      setStatus({ installed: false, running: false, port: 8787 });
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const withBusy = async (label: string, fn: () => Promise<Result>, ok: string) => {
    setBusy(label);
    try {
      const r = await fn();
      toaster.toast({ title: "Couchside", body: r.ok ? ok : `Failed: ${r.error ?? "unknown error"}` });
    } catch (e) {
      toaster.toast({ title: "Couchside", body: `Error: ${e}` });
    } finally {
      setBusy(null);
      await refresh();
    }
  };

  if (!status) {
    return (
      <PanelSection title="Couchside">
        <PanelSectionRow>
          <Spinner style={{ width: 24, height: 24 }} />
        </PanelSectionRow>
      </PanelSection>
    );
  }

  // ---- not installed: single install button -----------------------------
  if (!status.installed) {
    return (
      <PanelSection title="Couchside">
        <PanelSectionRow>
          <Field label="Agent" bottomSeparator="standard">Not installed</Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy !== null}
            onClick={() => withBusy("install", bInstall, "Agent installed. Scan the QR to pair.")}
          >
            {busy === "install" ? "Installing…" : "Install Couchside agent"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label="What this does" focusable={false}>
            Installs the open-source agent to your home folder, enables its service,
            and sets a scoped, audited sudoers rule. Everything runs on your box,
            with no cloud service and no account.
          </Field>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  // ---- installed: status + QR + actions ---------------------------------
  return (
    <>
      <PanelSection title="Couchside">
        <PanelSectionRow>
          <Field label="Status" bottomSeparator="none">
            {status.running ? `Running · v${status.agent_version ?? "?"}` : "Stopped"}
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label="Port" focusable={false}>{status.port}</Field>
        </PanelSectionRow>
      </PanelSection>

      {pairing?.ok && pairing.pair_url && (
        <PanelSection title="Pair your phone">
          <PanelSectionRow>
            <div style={{ display: "flex", justifyContent: "center", padding: "8px 0" }}>
              <div style={{ background: "#fff", padding: 12, borderRadius: 12 }}>
                <QRCodeSVG value={pairing.pair_url} size={180} includeMargin={false} />
              </div>
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="Host" focusable={false}>{pairing.host}:{pairing.port}</Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="Token" focusable={false}>
              <span style={{ fontFamily: "monospace", fontSize: 12, wordBreak: "break-all" }}>
                {pairing.token}
              </span>
            </Field>
          </PanelSectionRow>
        </PanelSection>
      )}

      <PanelSection title="Manage">
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy !== null}
            onClick={() => withBusy("restart", bRestart, "Agent restarted.")}
          >
            <FaRotate style={{ marginRight: 8 }} />
            {busy === "restart" ? "Restarting…" : "Restart agent"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy !== null}
            onClick={() => withBusy("regen", bRegen, "New token generated. Re-pair your phones.")}
          >
            <FaKey style={{ marginRight: 8 }} />
            {busy === "regen" ? "Regenerating…" : "Regenerate token"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy !== null}
            onClick={() => withBusy("install", bInstall, "Agent updated.")}
          >
            {busy === "install" ? "Updating…" : "Re-install / update"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy !== null}
            onClick={() => withBusy("uninstall", () => bUninstall(false), "Agent removed.")}
          >
            <FaTrash style={{ marginRight: 8 }} />
            {busy === "uninstall" ? "Removing…" : "Uninstall agent"}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

export default definePlugin(() => ({
  name: "Couchside",
  titleView: <div className={staticClasses.Title}>Couchside</div>,
  content: <Content />,
  icon: <FaCouch />,
}));
