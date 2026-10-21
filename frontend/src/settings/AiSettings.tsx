import { useEffect, useState } from "react";

import { ApiError, deleteAiSettings, getSettings, saveAiSettings } from "../api/client";
import type { AiProviderName, AiSettings as AiSettingsData } from "../api/types";

const CONSENT_TEXT =
  "Email text from candidate messages is sent to your chosen provider and billed by them";

export function AiSettings() {
  const [settings, setSettings] = useState<AiSettingsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const [provider, setProvider] = useState<AiProviderName | "">("");
  const [apiKey, setApiKey] = useState("");
  const [consent, setConsent] = useState(false);

  async function load() {
    setLoadFailed(false);
    try {
      setSettings(await getSettings());
    } catch {
      setLoadFailed(true);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  function failure(caught: unknown, fallback: string) {
    setError(caught instanceof ApiError && caught.status !== 0 ? `${fallback} ${caught.message}` : fallback);
  }

  async function save() {
    if (!settings) return;
    const chosen = settings.provider ?? (provider || settings.available_providers[0]);
    if (!chosen) return;
    if (settings.provider && !window.confirm("Replace the stored API key?")) return;
    setBusy(true);
    setError(null);
    try {
      setSettings(
        await saveAiSettings({
          provider: chosen,
          api_key: apiKey,
          consent,
          enabled: settings.provider ? settings.enabled : true,
        }),
      );
      setReplacing(false);
    } catch (caught) {
      failure(caught, "Could not save the API key.");
    } finally {
      setApiKey("");
      setConsent(false);
      setBusy(false);
    }
  }

  async function toggle() {
    if (!settings?.provider) return;
    setBusy(true);
    setError(null);
    try {
      setSettings(await saveAiSettings({ provider: settings.provider, enabled: !settings.enabled }));
    } catch (caught) {
      failure(caught, "Could not update AI settings.");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!window.confirm("Delete the stored API key and turn AI off?")) return;
    setBusy(true);
    setError(null);
    try {
      await deleteAiSettings();
      await load();
    } catch (caught) {
      failure(caught, "Could not delete the API key.");
    } finally {
      setBusy(false);
    }
  }

  const configured = settings?.provider != null;
  const showForm = settings !== null && settings.available_providers.length > 0 && (!configured || replacing);
  const selectedProvider = settings?.provider ?? (provider || settings?.available_providers[0]);

  return (
    <section className="ai-settings" aria-labelledby="ai-settings-title">
      <h2 id="ai-settings-title">AI assistance</h2>
      {loadFailed ? (
        <>
          <p role="alert" className="error">Could not load AI settings.</p>
          <button type="button" onClick={() => void load()}>Retry AI settings</button>
        </>
      ) : settings === null ? (
        <p>Loading AI settings…</p>
      ) : (
        <>
          {settings.available_providers.length === 0 && (
            <p>AI assistance is not available in this deployment.</p>
          )}
          {selectedProvider === "huggingface" && (
            <p className="muted">Hugging Face forwards candidate email text to {settings.hugging_face_host || "the model host"}.</p>
          )}
          {configured && (
            <>
              <p>
                Provider: <strong>{settings.provider === "huggingface" ? "Hugging Face" : settings.provider}</strong> · key ends in {settings.key_hint}
              </p>
              <p>AI is {settings.enabled ? "on" : "off"}.</p>
              {settings.key_status === "invalid" ? (
                <p role="alert" className="error">Key rejected, replace it</p>
              ) : (
                <p>Key status: working</p>
              )}
              <p className="muted">
                {settings.requests_today} of {settings.daily_limit} AI requests used today (UTC).
                This is a request cap, not a monetary cap; your provider bills you separately.
              </p>
              <div className="ai-actions">
                <button type="button" disabled={busy} onClick={() => void toggle()}>
                  {settings.enabled ? "Disable AI" : "Enable AI"}
                </button>
                {settings.available_providers.length > 0 && (
                  <button type="button" disabled={busy} onClick={() => setReplacing(true)}>Replace key</button>
                )}
                <button type="button" disabled={busy} onClick={() => void remove()}>Delete key</button>
              </div>
            </>
          )}
          {showForm && (
            <form
              className="ai-form"
              onSubmit={(event) => {
                event.preventDefault();
                void save();
              }}
            >
              {!configured && (
                <>
                  <label htmlFor="ai-provider">Provider</label>
                  <select
                    id="ai-provider"
                    value={provider || settings.available_providers[0]}
                    onChange={(event) => setProvider(event.target.value as AiProviderName)}
                  >
                    {settings.available_providers.map((name) => (
                      <option key={name} value={name}>{name === "huggingface" ? "Hugging Face" : name}</option>
                    ))}
                  </select>
                </>
              )}
              <label htmlFor="ai-key">API key</label>
              <input
                id="ai-key"
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
              />
              <label>
                <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
                {CONSENT_TEXT}
              </label>
              <button type="submit" disabled={busy || !consent || apiKey === ""}>Save key</button>
              {replacing && <button type="button" onClick={() => setReplacing(false)}>Cancel</button>}
            </form>
          )}
          {error && <p role="alert" className="error">{error}</p>}
        </>
      )}
    </section>
  );
}
