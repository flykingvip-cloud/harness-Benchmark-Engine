import { useState } from "react";
import { postJson, routes, type LibraryActionResult } from "./api";
import { remoteAuthReady, targetReady } from "./forms";
import { useI18n } from "./i18n/I18nProvider";
import { isMessageKey, type MessageKey } from "./i18n/messages";

export type InstallTarget = "local" | "remote";
export type AuthMethod = "ssh_key" | "password";

export type LibraryPanel = "preinstall" | "scan" | null;

export interface RemoteAuthPayload {
  username: string;
  auth_method: AuthMethod;
  secret: string;
  secret_env: string;
}

export interface LibraryGate {
  target: InstallTarget;
  choose: (next: InstallTarget) => void;
  host: string;
  editHost: (value: string) => void;
  port: number;
  editPort: (value: number) => void;
  requireAuth: boolean;
  username: string;
  editUsername: (value: string) => void;
  authMethod: AuthMethod;
  editAuthMethod: (value: AuthMethod) => void;
  secret: string;
  editSecret: (value: string) => void;
  secretEnv: string;
  editSecretEnv: (value: string) => void;
  auth: RemoteAuthPayload | null;
  authReady: boolean;
  reachable: boolean | null;
  probing: boolean;
  probe: () => Promise<LibraryActionResult>;
  probeReason: LibraryActionResult["reason"] | null;
  ready: boolean;
  body: { target: InstallTarget; host: string; port: number | null };
}

export function useLibraryGate(options?: { auth?: boolean }): LibraryGate {
  const requireAuth = options?.auth === true;
  const [target, setTarget] = useState<InstallTarget>("local");
  const [host, setHost] = useState("");
  const [port, setPort] = useState(22);
  const [username, setUsername] = useState("");
  const [authMethod, setAuthMethod] = useState<AuthMethod>("ssh_key");
  const [secret, setSecret] = useState("");
  const [secretEnv, setSecretEnv] = useState("");
  const [reachable, setReachable] = useState<boolean | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeReason, setProbeReason] = useState<LibraryActionResult["reason"] | null>(null);

  function clearProbe() {
    setReachable(null);
    setProbeReason(null);
  }

  function choose(next: InstallTarget) {
    setTarget(next);
    clearProbe();
  }

  function editHost(value: string) {
    setHost(value);
    clearProbe();
  }

  function editPort(value: number) {
    setPort(value);
    clearProbe();
  }

  function editUsername(value: string) {
    setUsername(value);
    clearProbe();
  }

  function editAuthMethod(value: AuthMethod) {
    setAuthMethod(value);
    clearProbe();
  }

  function editSecret(value: string) {
    setSecret(value);
    clearProbe();
  }

  function editSecretEnv(value: string) {
    setSecretEnv(value);
    clearProbe();
  }

  const authReady =
    !requireAuth ||
    target !== "remote" ||
    remoteAuthReady({ username, authMethod, secret, secretEnv });
  const auth: RemoteAuthPayload | null = requireAuth
    ? { username, auth_method: authMethod, secret, secret_env: secretEnv }
    : null;

  async function probe() {
    setProbing(true);
    try {
      const payload = requireAuth
        ? { host, port, username, auth_method: authMethod, secret, secret_env: secretEnv }
        : { host, port };
      const result = await postJson<LibraryActionResult>(routes.libraryProbe, payload);
      setReachable(result.reachable === true);
      setProbeReason(result.reason);
      return result;
    } catch (error) {
      setReachable(false);
      setProbeReason("unreachable");
      throw error;
    } finally {
      setProbing(false);
    }
  }

  return {
    target,
    choose,
    host,
    editHost,
    port,
    editPort,
    requireAuth,
    username,
    editUsername,
    authMethod,
    editAuthMethod,
    secret,
    editSecret,
    secretEnv,
    editSecretEnv,
    auth,
    authReady,
    reachable,
    probing,
    probe,
    probeReason,
    ready: targetReady(target, reachable) && authReady,
    body: { target, host, port: target === "remote" ? port : null },
  };
}

export function reasonText(
  t: (key: MessageKey, vars?: Record<string, string | number>) => string,
  reason: string,
): string {
  const key = `library.reason.${reason}`;
  if (isMessageKey(key)) {
    return t(key);
  }
  return reason;
}

export function TargetFields({ gate }: { gate: LibraryGate }) {
  const { t } = useI18n();
  const showAuth = gate.requireAuth && gate.target === "remote";
  return (
    <div className="gate">
      <div className="seg" role="group">
        <button type="button" className={gate.target === "local" ? "on" : ""} onClick={() => gate.choose("local")}>
          {t("library.local")}
        </button>
        <button type="button" className={gate.target === "remote" ? "on" : ""} onClick={() => gate.choose("remote")}>
          {t("library.remote")}
        </button>
      </div>
      {gate.target === "remote" ? (
        <div className="form-grid">
          <label>
            {t("library.host")}
            <input value={gate.host} spellCheck={false} onChange={(event) => gate.editHost(event.target.value)} />
          </label>
          <label>
            {t("library.port")}
            <input
              type="number"
              min={1}
              max={65535}
              value={gate.port}
              onChange={(event) => gate.editPort(Number(event.target.value))}
            />
          </label>
          {showAuth ? (
            <>
              <label>
                {t("library.user")}
                <input value={gate.username} spellCheck={false} onChange={(event) => gate.editUsername(event.target.value)} />
              </label>
              <label>
                {t("library.auth")}
                <select
                  value={gate.authMethod}
                  onChange={(event) => gate.editAuthMethod(event.target.value === "password" ? "password" : "ssh_key")}
                >
                  <option value="ssh_key">{t("library.authKey")}</option>
                  <option value="password">{t("library.authPassword")}</option>
                </select>
              </label>
              <label>
                {t("library.secretEnv")}
                <input value={gate.secretEnv} spellCheck={false} onChange={(event) => gate.editSecretEnv(event.target.value)} />
              </label>
              <label>
                {t("library.secret")}
                <input
                  type="password"
                  value={gate.secret}
                  autoComplete="off"
                  spellCheck={false}
                  onChange={(event) => gate.editSecret(event.target.value)}
                />
              </label>
            </>
          ) : null}
          <div className="row">
            <button
              type="button"
              className="secondary"
              disabled={gate.probing || gate.host.trim() === "" || (showAuth && !gate.authReady)}
              onClick={() => void gate.probe()}
            >
              {gate.probing ? t("library.probing") : t("library.probe")}
            </button>
            {gate.probeReason ? <span className={gate.reachable ? "pill ok" : "pill bad"}>{reasonText(t, gate.probeReason)}</span> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function ActionNote({ result }: { result: LibraryActionResult | null }) {
  const { t } = useI18n();
  if (!result) {
    return null;
  }
  const missing = result.missing.length > 0 ? t("library.missing", { names: result.missing.join(", ") }) : "";
  const count = result.admitted.length > 0 ? t("library.admittedLine", { count: result.admitted.length }) : "";
  return (
    <p className={result.ok ? "banner ok" : "banner bad"}>
      {reasonText(t, result.reason)}
      {result.version && !result.version.endsWith("-unavailable") ? ` · ${result.version}` : ""}
      {count ? ` · ${count}` : ""}
      {missing ? ` · ${missing}` : ""}
    </p>
  );
}
