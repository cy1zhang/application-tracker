import { useRef, useState } from "react";

import type { AutoApplyMode, ReviewItem } from "../api/types";
import { GmailConnection } from "../gmail/GmailConnection";
import { LocalOllamaPanel } from "../reviews/LocalOllamaPanel";
import { AiSettings } from "../settings/AiSettings";
import { DeleteAccount } from "../settings/DeleteAccount";
import { PlanBanner } from "./PlanBanner";

type Props = {
  reviews: ReviewItem[];
  onReload: () => Promise<void> | void;
  onAccountDeleted?: () => void;
  autoApplyMode?: AutoApplyMode;
  onModeChange?: (mode: AutoApplyMode) => void;
};

export function SettingsPanel({ reviews, onReload, onAccountDeleted, autoApplyMode, onModeChange }: Props) {
  const [showOllama, setShowOllama] = useState(false);
  const failedIds = useRef(new Set<string>());
  return (
    <section className="sheet-settings" aria-label="Settings panel">
      {autoApplyMode === "off" && <PlanBanner mode="off" onModeChange={onModeChange} />}
      <GmailConnection />
      <AiSettings />
      <div className="sheet-ollama">
        <button type="button" aria-expanded={showOllama} onClick={() => setShowOllama((shown) => !shown)}>Analyze with Ollama</button>
        {showOllama && <LocalOllamaPanel reviews={reviews} failedIds={failedIds.current} onSuggested={() => {}} onFinished={async () => { await onReload(); }} />}
      </div>
      {onAccountDeleted && <DeleteAccount onAccountDeleted={onAccountDeleted} />}
    </section>
  );
}
