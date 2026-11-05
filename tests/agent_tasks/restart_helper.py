"""Run as a SEPARATE OS process by test_checkpointing.py, so "restart" is a
real process boundary (fresh interpreter, no shared memory), not two
objects in one test process.

    python -m tests.agent_tasks.restart_helper pause  <repository_id> <root_path> <thread_id>
    python -m tests.agent_tasks.restart_helper resume <thread_id> <interrupt_id> <diff_sha256>

Prints one JSON line.
"""

import json
import sys

from agent.checkpointing import connect_checkpoint_database
from vectorstore.store import VectorStore

from tests.agent_tasks.conftest import MODIFY_INSTRUCTION, TEST_DATABASE_URL, make_service


def main(argv) -> None:
    conn = connect_checkpoint_database(TEST_DATABASE_URL)
    try:
        service = make_service(VectorStore(TEST_DATABASE_URL), conn)
        if argv[0] == "pause":
            repository_id, root_path, thread_id = int(argv[1]), argv[2], argv[3]
            result = service.run(MODIFY_INSTRUCTION, repository_id, root_path, thread_id)
            print(json.dumps({"status": result.status, **result.approval_binding()}))
        else:
            thread_id, interrupt_id, digest = argv[1], argv[2], argv[3]
            pending = service.pending_approval(thread_id)
            seen = pending.approval_binding() if pending else None
            result = service.resume(thread_id, approved=True, interrupt_id=interrupt_id, diff_sha256=digest)
            print(json.dumps({"pending_seen": seen, "status": result.status, "applied": result.applied}))
    finally:
        conn.close()


if __name__ == "__main__":
    main(sys.argv[1:])
