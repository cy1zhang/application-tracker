import { useRef, useState } from "react";

import type { ReviewItem } from "../api/types";
import { GmailConnection } from "../gmail/GmailConnection";
import { LocalOllamaPanel } from "../reviews/LocalOllamaPanel";
import { AiSettings } from "../settings/AiSettings";
import { DeleteAccount } from "../settings/DeleteAccount";

type Props = {
  reviews: ReviewItem[];
  onReload: () => Promise<void> | void;
  onAccountDeleted?: () => void;
};

export function SettingsPanel({ reviews, onReload, onAccountDeleted }: Props) {
  const [showOllama, setShowOllama] = useState(false);
  const failedIds = useRef(new Set<string>());
  return (
    <section className="sheet-settings" aria-label="Settings panel">
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
