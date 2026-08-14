def test_lance_mutation_lock_is_reentrant_for_sequential_writes(tmp_path):
    from tools.lance_lock import mutation_lock

    lock = tmp_path / "file_index.lance"
    with mutation_lock(lock):
        lock.with_name(lock.name + ".sentinel").write_text("held")
    assert lock.with_name(lock.name + ".lock").exists()
