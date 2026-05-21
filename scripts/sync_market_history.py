from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from sqlmodel import Session, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.db import engine
from backend.models import EastmoneyPositionSnapshot, EastmoneyTradeRecord, User
from backend.core.stock.market_data import (
    DEFAULT_HISTORY_AUTYPE,
    DEFAULT_HISTORY_KTYPE,
    DEFAULT_POSITION_LOOKBACK_DAYS,
    serialize_sync_result,
    sync_market_history_from_futu,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    with Session(engine) as session:
        user = resolve_user(session, user_id=args.user_id, username=args.username)
        if user is None:
            raise SystemExit("未找到有东财持仓或成交记录的用户，请用 --user-id 或 --username 指定。")

        try:
            result = sync_market_history_from_futu(
                session,
                user_id=int(user.id),
                database_path=args.database or None,
                start_date=args.start or None,
                end_date=args.end or None,
                lookback_days=args.lookback_days,
                ktype=args.ktype,
                autype=args.autype,
                host=args.host,
                port=args.port,
                incremental=not args.full_refresh,
                dry_run=args.dry_run,
                limit=args.limit,
            )
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
    print(json.dumps(serialize_sync_result(result), ensure_ascii=False, indent=2))
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步富途历史行情到本地 market-data.sqlite。")
    parser.add_argument("--user-id", type=int, default=None, help="CodeYun 用户 ID。默认选择第一个有东财股票数据的用户。")
    parser.add_argument("--username", default="", help="CodeYun 用户名。优先级高于默认自动选择。")
    parser.add_argument("--database", default="", help="行情缓存 SQLite 路径。默认在 CODEYUN_DATA_DIR/stock/market-data.sqlite。")
    parser.add_argument("--start", default="", help="强制开始日期，格式 YYYY-MM-DD。默认按最早成交日或持仓回看天数。")
    parser.add_argument("--end", default="", help="结束日期，格式 YYYY-MM-DD。默认今天。")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_POSITION_LOOKBACK_DAYS, help="只有持仓但没有成交记录时的默认回看天数。")
    parser.add_argument("--ktype", default=DEFAULT_HISTORY_KTYPE, help="K 线周期，默认 1m。")
    parser.add_argument("--autype", default=DEFAULT_HISTORY_AUTYPE, help="复权类型，默认 none，成交价分析建议用 none。")
    parser.add_argument("--host", default="127.0.0.1", help="Futu OpenD host。")
    parser.add_argument("--port", type=int, default=11111, help="Futu OpenD port。")
    parser.add_argument("--limit", type=int, default=None, help="只同步前 N 个目标，便于试跑。")
    parser.add_argument("--full-refresh", action="store_true", help="不按本地最大 time_key 增量同步，从目标 start 重新覆盖。")
    parser.add_argument("--dry-run", action="store_true", help="只输出待同步目标，不连接富途、不写数据库。")
    return parser.parse_args(argv)


def resolve_user(session: Session, *, user_id: int | None, username: str = "") -> User | None:
    if username:
        return session.exec(select(User).where(User.username == username)).first()
    if user_id is not None:
        return session.get(User, user_id)

    users = session.exec(select(User).order_by(User.id)).all()
    for user in users:
        has_position = session.exec(
            select(EastmoneyPositionSnapshot.id)
            .where(EastmoneyPositionSnapshot.user_id == user.id)
            .limit(1)
        ).first()
        has_trade = session.exec(
            select(EastmoneyTradeRecord.id)
            .where(EastmoneyTradeRecord.user_id == user.id)
            .limit(1)
        ).first()
        if has_position or has_trade:
            return user
    return None


if __name__ == "__main__":
    raise SystemExit(main())
