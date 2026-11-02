"""Judge answers stored and read back through the repository."""

from sqlalchemy.orm import Session

from app.db.repositories import JudgeAnswerRepository


def test_an_answer_is_stored_once_and_read_back(db_session: Session) -> None:
    answers = JudgeAnswerRepository(db_session)
    verdict = {"status": "full_match", "capability": "x", "chunk_id": "c", "quote": "q"}

    answers.store("k" * 64, "model", "1", verdict)
    answers.store("k" * 64, "model", "1", {**verdict, "status": "unsupported"})  # kept as first

    assert answers.verdict("k" * 64) == verdict
    assert answers.verdict("z" * 64) is None
