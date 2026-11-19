"""Durable, idempotent acceptance of one validated envelope."""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.ingest.semantic import parse_timestamp
from app.models import TelemetryEnvelope


class Outcome(enum.Enum):
    CREATED = "created"      # first time this envelope_id was seen; committed
    DUPLICATE = "duplicate"  # already accepted with equivalent content; nothing stored


class EnvelopeConflict(Exception):
    """Same envelope_id already accepted with different content."""


def store_envelope(session_factory: sessionmaker[Session], envelope: dict, digest: str) -> Outcome:
    """Insert-or-compare in ONE transaction. Returns only after COMMIT succeeded.

    The unique index on envelope_id is the arbiter, not an application-level check-then-insert:

      INSERT ... ON CONFLICT (envelope_id) DO NOTHING RETURNING id

    * row returned      -> we won; commit makes it durable; CREATED.
    * nothing returned  -> another transaction owns the id. Under READ COMMITTED a concurrent
      inserter makes this statement wait until that transaction commits, so by the time it returns
      the winner's row is committed and visible to the follow-up SELECT. Equal hash -> DUPLICATE,
      different hash -> EnvelopeConflict. A duplicate is therefore never answered before the
      original envelope is committed.

    Any database error propagates (transaction rolled back); the caller must not acknowledge.
    """
    envelope_id = uuid.UUID(envelope["envelope_id"])
    values = dict(
        envelope_id=envelope_id,
        agent_id=envelope["agent_id"],
        host_id=envelope["host_id"],
        hostname=envelope["hostname"],
        schema_version=envelope["schema_version"],
        envelope_timestamp=parse_timestamp(envelope["timestamp"]),
        service=envelope.get("service"),
        metric_count=len(envelope["metrics"]),
        content_hash=digest,
        payload=envelope,
    )
    for _ in range(3):  # the loop only repeats if the winning row vanished (manual deletion)
        with session_factory() as session:
            with session.begin():
                inserted = session.execute(
                    pg_insert(TelemetryEnvelope)
                    .values(**values)
                    .on_conflict_do_nothing(index_elements=["envelope_id"])
                    .returning(TelemetryEnvelope.id)
                ).scalar_one_or_none()
                if inserted is not None:
                    outcome: Outcome | None = Outcome.CREATED
                else:
                    stored = session.execute(
                        select(TelemetryEnvelope.content_hash).where(
                            TelemetryEnvelope.envelope_id == envelope_id
                        )
                    ).scalar_one_or_none()
                    if stored is None:
                        continue
                    outcome = Outcome.DUPLICATE if stored == digest else None
            # COMMIT happened when the begin() block exited without error.
        if outcome is None:
            raise EnvelopeConflict(str(envelope_id))
        return outcome
    raise RuntimeError("envelope row disappeared during idempotency check")
