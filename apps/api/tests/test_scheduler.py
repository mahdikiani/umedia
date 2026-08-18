from server.scheduler import build_scheduler


def test_scheduler_has_maintenance_job() -> None:
    scheduler = build_scheduler()

    jobs = scheduler.get_jobs()
    assert [job.id for job in jobs] == ["maintenance"]
    assert jobs[0].max_instances == 1
