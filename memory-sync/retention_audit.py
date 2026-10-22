"""Read-only coverage and explicit staging retention run audit."""
import json
from recall_memory import open_readonly
from shared_memory_context import pending_ids
from summarize_memory import DEFAULT_CONSUMER


def audit(database, consumer=DEFAULT_CONSUMER):
    from staging_retention import settings, run_status
    _, configured = settings()
    with open_readonly(database) as c:
        total = c.execute('SELECT count(*) FROM observation_events').fetchone()[0]
        eligible = c.execute("SELECT count(*) FROM observation_events WHERE kind IN ('user_request','assistant_response')").fetchone()[0]
        uncovered = len(pending_ids(c, consumer))
        linked = c.execute('SELECT count(DISTINCT event_id) FROM memory_sources').fetchone()[0]
        receipts = c.execute("SELECT count(*) FROM summary_state WHERE substr(consumer,1,length(?))=?", (consumer + ':event:',) * 2).fetchone()[0]
        return dict(**run_status(c, configured=configured), deletions=0, events=total, eligible_events=eligible,
                    uncovered_events=uncovered, covered_eligible_events=eligible-uncovered,
                    evidence_linked_events=linked, receipt_rows=receipts, consumer=consumer,
                    warning='Coverage alone is not permission to delete. Age, every summary consumer, evidence, completed session, tombstone and packet-sync-off gates remain required. Peer delivery is retired.')


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('database')
    p.add_argument('--consumer', default=DEFAULT_CONSUMER)
    args = p.parse_args(argv)
    print(json.dumps(audit(args.database, args.consumer)))


if __name__ == '__main__':
    main()
