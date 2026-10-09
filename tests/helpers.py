"""Statement and ledger builders shared by tests."""

from datetime import date
from pathlib import Path

from bean_sieve.api import full_reconcile
from bean_sieve.core.sieve import Sieve, SieveConfig
from bean_sieve.core.types import MatchResult

ALIPAY_DATA_HEADER = (
    "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,"
    "交易状态,交易订单号,商家订单号,备注,"
)


def write_alipay_csv(
    tmp_path: Path,
    rows: list[str],
    *,
    filename: str = "alipay.csv",
    period: tuple[date, date] | None = None,
) -> Path:
    """Write a GBK Alipay export: 24 header lines, the column header, then rows."""
    period_line = (
        f"起始时间：[{period[0]} 00:00:00]    终止时间：[{period[1]} 23:59:59]"
        if period
        else ""
    )
    header = [
        "-" * 84,
        "导出信息：",
        "姓名：测试用户",
        "支付宝账户：test@example.com",
        period_line,
        "导出交易类型：[全部]",
        "导出时间：[2030-02-01 10:00:00]",
        f"共{len(rows)}笔记录",
        "收入：0笔 0.00元",
        "支出：0笔 0.00元",
        "不计收支：0笔 0.00元",
        "",
        "特别提示：",
        *(f"{i}.提示{i}" for i in range(1, 10)),
        "",
        "-" * 24 + "支付宝支付科技有限公司  电子客户回单" + "-" * 24,
    ]
    path = tmp_path / filename
    path.write_text(
        "\n".join([*header, ALIPAY_DATA_HEADER, *rows]) + "\n", encoding="gbk"
    )
    return path


def run_full_reconcile(
    tmp_path: Path, statement_paths: list[Path], config_yaml: str, ledger_text: str
) -> MatchResult:
    """Write bean-sieve.yaml and main.bean, then reconcile the statements."""
    config_path = tmp_path / "bean-sieve.yaml"
    config_path.write_text(config_yaml, encoding="utf-8")
    ledger_path = tmp_path / "main.bean"
    ledger_path.write_text(ledger_text, encoding="utf-8")
    return full_reconcile(
        statement_paths=statement_paths,
        ledger_path=ledger_path,
        config_path=config_path,
    ).match_result


def load_sieve(tmp_path: Path, ledger_text: str, *, date_tolerance: int = 0) -> Sieve:
    """Write ledger_text to a ledger file and load it into a fresh Sieve."""
    ledger_path = tmp_path / "ledger.bean"
    ledger_path.write_text(ledger_text, encoding="utf-8")
    sieve = Sieve(SieveConfig(date_tolerance=date_tolerance))
    sieve.load_ledger(ledger_path)
    return sieve
