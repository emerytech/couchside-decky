import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  staticClasses,
  Spinner,
  Field,
  ConfirmModal,
  showModal,
} from "@decky/ui";
import { callable, definePlugin, toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { FaCouch, FaDownload, FaRotate, FaKey, FaTrash } from "react-icons/fa6";
import { QRCodeSVG } from "qrcode.react";

// ---- backend bridges (names match main.py's Plugin methods) --------------
type Status = { installed: boolean; running: boolean; port: number; agent_version?: string | null; uinput_ready?: boolean };
type Pairing = { ok: boolean; host?: string; port?: number; token?: string; pair_url?: string; error?: string };
type Result = { ok: boolean; error?: string };
type UpdateCheck = { ok: boolean; current: string; latest?: string; update_available: boolean; error?: string };
type UpdateResult = { ok: boolean; updated?: boolean; version?: string; error?: string };

const bInstall = callable<[], Result>("install");
const bStatus = callable<[], Status>("status");
const bPairing = callable<[], Pairing>("get_pairing");
const bRestart = callable<[], Result>("restart_agent");
const bRegen = callable<[], Result & { token?: string }>("regenerate_token");
const bUninstall = callable<[boolean], Result>("uninstall");
const bCheckUpdate = callable<[], UpdateCheck>("check_update");
const bSelfUpdate = callable<[], UpdateResult>("self_update");

function Content() {
  const [status, setStatus] = useState<Status | null>(null);
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [upd, setUpd] = useState<UpdateCheck | null>(null);

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
    // The QAM panel stays mounted after you first open it, so a one-shot read
    // can latch onto a transient state (e.g. "Stopped" caught while the service
    // is mid-restart) and never update. Re-poll so the status corrects itself
    // instead of waiting for a button tap.
    const timer = setInterval(refresh, 5000);
    // Update check is ONCE per mount, deliberately outside the 5s poll: it hits
    // the GitHub API (60 req/h unauthenticated) and a new release mid-session
    // is rare — reopening the panel after a Decky restart re-checks anyway.
    bCheckUpdate().then(setUpd).catch(() => {});
    return () => clearInterval(timer);
  }, []);

  // busy is null or the one active action's label: non-null disables all
  // buttons (actions are mutually exclusive), and === label picks that button's
  // spinner text.
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

  // Gate a destructive action behind a ConfirmModal so a stray Game-Mode D-pad
  // click can't un-pair every phone or remove the service with no undo. ConfirmModal
  // closes itself on OK/Cancel; we only run the action when the user confirms.
  const confirmThen = (
    title: string,
    description: string,
    okText: string,
    run: () => void,
  ) =>
    showModal(
      <ConfirmModal
        strTitle={title}
        strDescription={description}
        strOKButtonText={okText}
        strCancelButtonText="Cancel"
        bDestructiveWarning={true}
        onOK={run}
      />,
    );

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
          <Field label="Service" bottomSeparator="standard">Not installed</Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy !== null}
            onClick={() => withBusy("install", bInstall, "Service installed. Scan the QR to pair.")}
          >
            {busy === "install" ? "Installing…" : "Install Couchside service"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label="What this does" focusable={false}>
            Installs the open-source Couchside service to your home folder, enables its systemd unit,
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
        {status.uinput_ready === false && (
          <PanelSectionRow>
            {/* The #1 silent post-install failure: the service is "Running" but the
                virtual gamepad device isn't usable, so no controller input reaches
                games. Surface it instead of showing a bare green "Running". */}
            <Field label="Virtual gamepad" focusable={false} bottomSeparator="none">
              <span style={{ color: "#f5a623" }}>
                Unavailable — reboot or reinstall
              </span>
            </Field>
          </PanelSectionRow>
        )}
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
            onClick={() => withBusy("restart", bRestart, "Service restarted.")}
          >
            <FaRotate style={{ marginRight: 8 }} />
            {busy === "restart" ? "Restarting…" : "Restart service"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy !== null}
            onClick={() =>
              confirmThen(
                "Regenerate token?",
                "This invalidates the current token. Every paired phone will be signed out and must scan the new QR to pair again.",
                "Regenerate",
                () => withBusy("regen", bRegen, "New token generated. Re-pair your phones."),
              )
            }
          >
            <FaKey style={{ marginRight: 8 }} />
            {busy === "regen" ? "Regenerating…" : "Regenerate token"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy !== null}
            // Reuses bInstall and the "install" busy label on purpose: install
            // is idempotent, so re-running it upgrades in place.
            onClick={() => withBusy("install", bInstall, "Service updated.")}
          >
            {busy === "install" ? "Updating…" : "Re-install / update"}
          </ButtonItem>
        </PanelSectionRow>
        {upd?.update_available && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={busy !== null}
              onClick={() =>
                confirmThen(
                  `Update plugin to v${upd.latest}?`,
                  "Downloads the latest signed release from GitHub, verifies its signature, replaces this plugin (and the bundled service), then restarts Decky. The Steam overlay will reload for a few seconds. After updating, tap Re-install / update to roll the new service onto the box.",
                  "Update",
                  () =>
                    withBusy("selfupdate", async () => {
                      const r = await bSelfUpdate();
                      return { ok: r.ok, error: r.error };
                    }, `Updated to v${upd.latest}. Decky is reloading…`),
                )
              }
            >
              <FaDownload style={{ marginRight: 8 }} />
              {busy === "selfupdate" ? "Updating plugin…" : `Update plugin (v${upd.current} → v${upd.latest})`}
            </ButtonItem>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy !== null}
            onClick={() =>
              confirmThen(
                "Uninstall service?",
                "This stops and removes the Couchside service from this box. Your phones will lose remote control until you reinstall.",
                "Uninstall",
                () => withBusy("uninstall", () => bUninstall(false), "Service removed."),
              )
            }
          >
            <FaTrash style={{ marginRight: 8 }} />
            {busy === "uninstall" ? "Removing…" : "Uninstall service"}
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
