"""동기화된 복약 일정의 미래 회차를 멱등 생성한다.

실행 예: python -m app.jobs.generate_medication_occurrences --days 30
"""

import argparse

from ..services import db
from ..services.medication_schedule import DEFAULT_HORIZON_DAYS, generate_horizon


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=DEFAULT_HORIZON_DAYS)
    args = parser.parse_args()

    db.init()
    if not db.available():
        raise SystemExit("Database unavailable")
    try:
        with db.conn() as conn:
            created = generate_horizon(conn, days=args.days)
        print(f"예정 회차 생성 완료: 신규 {created}건")
    finally:
        db.close()


if __name__ == "__main__":
    main()
