def test_index_queue_deduplicates_same_fingerprint_and_claims_in_order(tmp_path, monkeypatch):
    from tools import document_index_queue

    monkeypatch.setattr(document_index_queue, "_DB_PATH", tmp_path / "jobs.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = workspace / "memo.txt"
    path.write_text("same content")

    first_id = document_index_queue.enqueue(path, workspace=str(workspace), matter="m1")
    second_id = document_index_queue.enqueue(path, workspace=str(workspace), matter="m1")
    assert first_id == second_id
    assert document_index_queue.pending_count(str(workspace), "m1") == 1

    job = document_index_queue.claim(str(workspace), "m1")
    assert job["id"] == first_id
    assert job["fingerprint"]
    assert document_index_queue.claim(str(workspace), "m1") is None

    document_index_queue.complete(job["id"])
    assert document_index_queue.pending_count(str(workspace), "m1") == 0


def test_index_queue_new_content_gets_new_job(tmp_path, monkeypatch):
    from tools import document_index_queue

    monkeypatch.setattr(document_index_queue, "_DB_PATH", tmp_path / "jobs.db")
    path = tmp_path / "memo.txt"
    path.write_text("one")
    first_id = document_index_queue.enqueue(path, workspace=str(tmp_path))
    path.write_text("two")
    second_id = document_index_queue.enqueue(path, workspace=str(tmp_path))
    assert first_id != second_id


def test_index_queue_forget_path_removes_unfinished_and_finished_jobs(tmp_path, monkeypatch):
    from tools import document_index_queue

    monkeypatch.setattr(document_index_queue, "_DB_PATH", tmp_path / "jobs.db")
    path = tmp_path / "memo.txt"
    path.write_text("one")
    job_id = document_index_queue.enqueue(path, workspace=str(tmp_path))
    assert document_index_queue.forget_path(path) == 1
    assert document_index_queue.claim(str(tmp_path)) is None
    assert job_id is not None


def test_index_queue_recovers_stale_processing_job(tmp_path, monkeypatch):
    from tools import document_index_queue

    monkeypatch.setattr(document_index_queue, "_DB_PATH", tmp_path / "jobs.db")
    path = tmp_path / "memo.txt"
    path.write_text("one")
    job_id = document_index_queue.enqueue(path, workspace=str(tmp_path))
    claimed = document_index_queue.claim(str(tmp_path))
    assert claimed["id"] == job_id
    with document_index_queue._conn() as conn:
        conn.execute("UPDATE index_jobs SET claimed_at=? WHERE id=?", (0, job_id))

    assert document_index_queue.recover_stale(60) == 1
    assert document_index_queue.claim(str(tmp_path))["id"] == job_id
