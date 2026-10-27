"""CarePong 공공데이터 갱신 실행기.

기존 수집/적재 스크립트를 한 실행 단위로 묶고 sync_run에 성공·실패와 건수를 남긴다.
DDI 원문은 수집만 하며 규칙 승인/활성화는 수행하지 않는다.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATASETS = ("med-ingredients", "hff-ingredients", "dur-raw")


@dataclass
class RunCounts:
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


def number(pattern: str, text: str) -> int:
    match = re.search(pattern, text)
    return int(match.group(1).replace(",", "")) if match else 0


def parse_counts(text: str) -> RunCounts:
    fetched = max(
        number(r"원천 총건수:\s*([\d,]+)", text),
        number(r"received:\s*([\d,]+)", text),
    )
    inserted = max(
        number(r"이번 신규 INSERT:\s*([\d,]+)", text),
        number(r"inserted:\s*([\d,]+)", text),
    )
    updated = max(
        number(r"이번 변경 UPDATE:\s*([\d,]+)", text),
        number(r"실제 UPDATE:\s*([\d,]+)", text),
    )
    unchanged = max(fetched - inserted - updated, 0)
    return RunCounts(fetched, inserted, updated, unchanged)


def run(command: list[str], *, stdin: str | None = None) -> str:
    result = subprocess.run(
        command,
        input=stdin,
        text=True,
        capture_output=True,
        cwd=ROOT,
    )
    output = (result.stdout or "") + (result.stderr or "")
    print(output, end="", flush=True)
    if result.returncode:
        raise RuntimeError(f"명령 실패({result.returncode}): {Path(command[1]).name}")
    return output


def dataset_commands(dataset: str, *, apply: bool, refresh: bool, pages: int) -> list[tuple[list[str], str | None]]:
    page_args = ["--pages", str(pages)] if pages else []
    refresh_args = ["--refresh"] if refresh else []
    python = sys.executable

    if dataset == "med-ingredients":
        commands = [([python, str(ROOT / "collect_med_ingredients.py"), *page_args, *refresh_args], None)]
        if apply:
            commands.append(([python, str(ROOT / "load_med_ingredients.py")], None))
        return commands

    if dataset == "hff-ingredients":
        from carepong_secrets import get_api_key

        key = get_api_key("FOODSAFETY_KEY").strip()
        if not key:
            raise RuntimeError("FOODSAFETY_KEY가 비어 있습니다.")
        command = [python, str(ROOT / "collect_hff.py")]
        if pages:
            command += ["--pages", str(pages)]
        if apply:
            command.append("--apply")
        commands = [(command, key + "\n")]
        if apply:
            commands.append(([python, str(ROOT / "apply_hff_normalization.py"), "--limit", "10000000", "--apply"], None))
        return commands

    if dataset == "dur-raw":
        return [([python, str(ROOT / "collect_dur.py"), *page_args, *refresh_args], None)]

    raise ValueError(dataset)


def run_dataset(dataset: str, *, apply: bool, refresh: bool, pages: int = 0) -> RunCounts:
    import psycopg

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL이 필요합니다.")

    with psycopg.connect(database_url, connect_timeout=10) as conn:
        locked = conn.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s))", (f"carepong-refresh:{dataset}",)
        ).fetchone()[0]
        if not locked:
            raise RuntimeError(f"이미 실행 중인 갱신이 있습니다: {dataset}")

        run_id = conn.execute(
            """
            INSERT INTO sync_run(dataset_code, started_at, status)
            VALUES (%s, %s, 'RUNNING')
            RETURNING sync_run_id
            """,
            (dataset, datetime.now(timezone.utc)),
        ).fetchone()[0]
        conn.commit()

        output = ""
        try:
            for command, stdin in dataset_commands(
                dataset, apply=apply, refresh=refresh, pages=pages
            ):
                output += run(command, stdin=stdin)
            counts = parse_counts(output)
            conn.execute(
                """
                UPDATE sync_run
                SET finished_at=now(), status='SUCCEEDED',
                    fetched_count=%s, inserted_count=%s,
                    updated_count=%s, unchanged_count=%s,
                    failed_count=0, error_message=NULL
                WHERE sync_run_id=%s
                """,
                (counts.fetched, counts.inserted, counts.updated, counts.unchanged, run_id),
            )
            conn.commit()
            return counts
        except Exception as exc:
            conn.rollback()
            conn.execute(
                """
                UPDATE sync_run
                SET finished_at=now(), status='FAILED', failed_count=1,
                    error_message=%s
                WHERE sync_run_id=%s
                """,
                (str(exc)[:2000], run_id),
            )
            conn.commit()
            raise
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"carepong-refresh:{dataset}",))
            conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=(*DATASETS, "all"), default="all")
    parser.add_argument("--apply", action="store_true", help="검증 후 RDS upsert 실행")
    parser.add_argument("--refresh", action="store_true", help="기존 raw 페이지도 다시 수집")
    parser.add_argument("--pages", type=int, default=0, help="0이면 전체 페이지")
    args = parser.parse_args()
    if args.pages < 0:
        parser.error("--pages는 0 이상이어야 합니다.")

    selected = DATASETS if args.dataset == "all" else (args.dataset,)
    for dataset in selected:
        print(f"\n===== {dataset} 시작 =====", flush=True)
        counts = run_dataset(dataset, apply=args.apply, refresh=args.refresh, pages=args.pages)
        print(f"===== {dataset} 완료: {counts} =====", flush=True)


if __name__ == "__main__":
    main()
