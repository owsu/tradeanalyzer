from __future__ import annotations

import json
from datetime import datetime

from config import DATABASE_PATH
from database import Database
from models import Proof, RawProof
from proofs.validator import validate_proof


def main() -> None:
    database = Database(DATABASE_PATH)
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT source, channel_id, message_id, parsed_proof,
                   author, message_timestamp
            FROM proof_messages
            WHERE status='succeeded' AND parsed_proof IS NOT NULL
            """
        ).fetchall()

    rebuilt = 0
    for row in rows:
        proof = validate_proof(Proof.model_validate(json.loads(row["parsed_proof"])))
        raw = RawProof(
            source=row["source"],
            channel_id=row["channel_id"],
            message_id=row["message_id"],
            author=row["author"],
            timestamp=(
                datetime.fromisoformat(row["message_timestamp"])
                if row["message_timestamp"]
                else None
            ),
        )
        database.complete_proof_message(raw, proof.model_dump(mode="json"))
        rebuilt += 1

    with database.connect() as connection:
        implied = connection.execute(
            """
            SELECT COUNT(observation_key) AS count
            FROM item_value_observations WHERE observation_kind='implied'
            """
        ).fetchone()["count"]
    print(f"Rebuilt {rebuilt} proofs; {implied} implied observations available")


if __name__ == "__main__":
    main()
