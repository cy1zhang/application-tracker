import json
from datetime import UTC, datetime

from app.repositories.dynamodb_sync import StaleSyncRun
from app.services.email_processing import process_message
from app.services.gmail_fetch import GmailApiError, HistoryExpired, MessageUnavailable
from app.services.sync_candidates import load_candidates


class RetryableSyncOutcome(Exception):
    pass


def process_sync_page(
    *, owner_id, run_id, generation, lease_id, now, syncs, connections,
    cipher, oauth, fetch, applications, emails, publisher, queue_url,
    escalator_factory=None, auto_apply=None,
):
    lease = syncs.claim(owner_id, run_id, generation, lease_id, now)
    if lease is None:
        return None
    def active_token():
        encrypted = connections.get_active(owner_id, generation)
        if encrypted is None:
            raise StaleSyncRun
        return encrypted

    access_token = oauth.refresh_access_token(
        cipher.decrypt_refresh_token(owner_id, active_token())
    )
    run = lease.run
    if run.mode in {"import", "rescan"}:
        if run.history_snapshot_id is None:
            active_token()
            snapshot = fetch.profile_history_id(access_token)
            run = syncs.set_history_snapshot(
                owner_id, run_id, generation, lease_id, snapshot
            )
        active_token()
        page = fetch.list_import_page(access_token, run.import_after, lease.page_token)
        final_history_id = run.history_snapshot_id
    else:
        active_token()
        try:
            page = fetch.list_history_page(
                access_token, run.history_id, lease.page_token
            )
        except HistoryExpired:
            rescan = syncs.switch_to_rescan(owner_id, run_id, generation, lease_id, now)
            _enqueue(publisher, queue_url, owner_id, run_id, generation)
            return rescan
        final_history_id = page.history_id
    candidates = load_candidates(owner_id, applications)
    mode = auto_apply.get_mode(owner_id) if auto_apply is not None else "off"
    escalator = escalator_factory() if escalator_factory is not None else None
    processed = 0
    offset = lease.message_offset if run.mode == "incremental" else 0
    if offset > len(page.message_ids):
        raise GmailApiError("Gmail history page changed during sync")
    end = min(offset + 10, len(page.message_ids))
    for message_id in page.message_ids[offset:end]:
        active_token()
        try:
            message = fetch.get_message(access_token, message_id)
        except MessageUnavailable:
            emails.record_unavailable(owner_id=owner_id, message_id=message_id)
            processed += 1
            continue
        try:
            received_at = datetime.fromtimestamp(int(message["internalDate"]) / 1000, tz=UTC)
        except (KeyError, TypeError, ValueError, OverflowError):
            received_at = None
        if run.import_after is not None and received_at is not None and received_at < run.import_after:
            continue
        outcome = process_message(
            owner_id, message, candidates, emails, escalator=escalator,
            mode=mode,
        )
        if outcome.outcome == "retryable":
            raise RetryableSyncOutcome
        processed += 1

    if end < len(page.message_ids):
        run = syncs.checkpoint(
            owner_id, run_id, generation, lease_id, lease.page_token,
            processed, now, message_offset=end,
        )
        _enqueue(publisher, queue_url, owner_id, run_id, generation)
        return run
    if page.next_page_token is None:
        return syncs.complete(
            owner_id, run_id, generation, lease_id, processed, now,
            history_id=final_history_id,
        )
    run = syncs.checkpoint(
        owner_id, run_id, generation, lease_id, page.next_page_token, processed, now
    )
    _enqueue(publisher, queue_url, owner_id, run_id, generation)
    return run


def _enqueue(publisher, queue_url, owner_id, run_id, generation):
    publisher.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps({
            "owner_id": owner_id, "run_id": run_id, "generation": generation,
        }),
    )
