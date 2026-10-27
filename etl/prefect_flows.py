"""Prefect 3에서 호출할 CarePong 데이터 갱신 Flow."""

import os

from prefect import flow, task

from refresh_pipeline import DATASETS, run_dataset


@task(retries=2, retry_delay_seconds=300)
def refresh_one(dataset: str, apply: bool = True, refresh: bool = True):
    return run_dataset(dataset, apply=apply, refresh=refresh)


@flow(name="carepong-data-refresh", log_prints=True)
def refresh_all(apply: bool = True, refresh: bool = True):
    # RDS와 외부 API 부하를 낮추기 위해 의도적으로 순차 실행한다.
    return {dataset: refresh_one(dataset, apply, refresh) for dataset in DATASETS}


if __name__ == "__main__":
    cron = os.getenv("CAREPONG_REFRESH_CRON", "0 3 * * 0")
    refresh_all.serve(
        name="carepong-weekly-data-refresh",
        cron=cron,
        timezone="Asia/Seoul",
        pause_on_shutdown=False,
        limit=1,
    )
