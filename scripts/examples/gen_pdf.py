"""PDF statements drawn with PyMuPDF."""

import re
from pathlib import Path

import fitz
from _common import PdfPage, generator


@generator("boc_credit")
def boc_credit(out_dir: Path) -> None:
    pdf = PdfPage(font_size=9)
    pdf.put(30, 40, "中国银行信用卡账单", 14)
    pdf.put(30, 70, "Payment Due Date / Statement Closing Date")
    # Due date then closing date in one block, as the real summary prints them.
    pdf.put(30, 90, "2030-03-25")
    pdf.put(30, 102, "2030-03-04")
    pdf.put(30, 114, "0.00")

    xs = [30, 100, 170, 210, 420, 520]
    headers = ["交易日", "银行记账日", "卡号后四位", "交易描述", "存入", "支出"]

    def section(y: float, card: str, rows: list[list[str]]) -> float:
        pdf.put(30, y, f"(卡号：000000000000{card})")
        y += 25
        # Header labels sit in their own blocks; "Expenditure" must be alone.
        for x, label in zip(xs, headers, strict=True):
            pdf.put(x, y, label)
        pdf.put(520, y + 25, "Expenditure", 7)
        y += 60
        # Column-major writes keep each column in its own text block, which is
        # how the bank's PDF lays out the table and what the parser's x-based
        # column detection relies on.
        for ci, x in enumerate(xs):
            for ri, row in enumerate(rows):
                if row[ci]:
                    pdf.put(x, y + ri * 25, row[ci])
        return y + len(rows) * 25 + 20

    y = section(
        160,
        "1234",
        [
            ["2030-02-06", "2030-02-07", "1234", "消费 store-a", "", "35.00"],
            ["2030-02-10", "2030-02-11", "1234", "网上消费 shop-b", "", "120.00"],
            ["2030-02-15", "2030-02-16", "1234", "退货 shop-b", "120.00", ""],
            ["2030-02-20", "2030-02-20", "1234", "还款", "500.00", ""],
        ],
    )
    y = section(
        y,
        "5678",
        [
            ["2030-02-12", "2030-02-13", "5678", "消费 cafe-c", "", "28.00"],
            ["2030-02-27", "2030-02-28", "5678", "消费 market-d", "", "66.00"],
        ],
    )
    pdf.put(30, y + 10, "积分奖励计划 Loyalty Plan")
    pdf.save(out_dir / "中国银行信用卡电子合并账单2030年03月账单.pdf")


@generator("welab_debit")
def welab_debit(out_dir: Path) -> None:
    date_x, type_x, desc_x, amt_x = 55, 165, 270, 505
    pdf = PdfPage(font_size=6.0)
    put = pdf.put

    def row(y: float, d: str, ty: str, de: str, am: str) -> None:
        put(date_x, y, d)
        # A bilingual type wraps onto a second line, Chinese first.
        match = re.match(r"^([一-鿿（）()]+)\s*(.*)$", ty)
        if match and match.group(2):
            put(type_x, y, match.group(1))
            put(type_x, y + 8, match.group(2))
        else:
            put(type_x, y, ty)
        put(desc_x, y, de)
        put(amt_x, y, am)

    put(40, 75, "Your Bank Statement (1 Feb 2030 - 28 Feb 2030) 你的银行月结单")
    put(40, 90, "寰球钱包 核心账户 Global Wallet Core Account")
    put(40, 100, "定期存款 Time Deposits - GoSave 2.0 智安存")

    put(
        40,
        120,
        "核心账户 Core Account (1010000000) - 港元 HKD (包括智安存 Include Money Safe)",
    )
    put(40, 138, "日期 种类 交易详情 金额 (港元)")
    put(40, 147, "Date Type Transaction Description Amount (HKD)")
    row(168, "01 Feb 2030", "承上结余", "Balance From Previous Statement", "-")
    row(190, "03 Feb 2030", "收款 Receive money", "Receive money from employer-a", "10,000.00")  # fmt: skip
    put(desc_x, 198, "Ref: FT30F001", 4)
    row(212, "06 Feb 2030", "转账 Transfer", "Transfer to landlord-b Ref: FT30F002", "-6,000.00")  # fmt: skip
    row(234, "10 Feb 2030", "外币兑换", "CNY/HKD @ 1.10 Ref: FX30F00003", "-1,100.00")
    row(256, "15 Feb 2030", "服务收费 Service Charge", "service-fee-a Ref: FT30F004", "-20.00")  # fmt: skip
    row(278, "28 Feb 2030", "利息 Interest", "GoSave interest Ref: FT30F005", "12.00")
    row(300, "28 Feb 2030", "帐户结余 Closing Balance", "", "2,892.00")

    put(
        40,
        340,
        "核心账户 Core Account (1010000000) - 人民币 CNY (包括智安存 Include Money Safe)",
    )
    put(40, 358, "日期 种类 交易详情 金额 (人民币)")
    put(40, 367, "Date Type Transaction Description Amount (CNY)")
    row(388, "10 Feb 2030", "外币兑换", "CNY/HKD @ 1.10 Ref: FX30F00003", "1,000.00")
    put(date_x, 410, "18 Feb 2030")
    put(type_x, 410, "借记卡消费")
    put(type_x, 418, "Debit Card spending")
    put(desc_x, 410, "WEIXIN*store-c CHN Online 5999")
    put(desc_x, 418, "Ref: FT30F006, Transaction Date: 17 Feb 2030", 4)
    put(amt_x, 410, "-300.00")
    row(432, "21 Feb 2030", "借记卡退款 Debit Card Refund", "WEIXIN*store-c CHN Online Ref: FT30F007", "100.00")  # fmt: skip
    put(date_x, 454, "24 Feb 2030")
    put(type_x, 454, "借记卡消费")
    put(type_x, 462, "Debit Card spending")
    put(desc_x, 454, "Alipay*shop-d CHN Online 5812")
    put(desc_x, 462, "Ref: FT30F008, Transaction Date: 24 Feb 2030", 4)
    put(amt_x, 454, "-45.00")
    row(476, "28 Feb 2030", "帐户结余 Closing Balance", "", "755.00")

    put(40, 750, "Page 1 of 1")
    pdf.save(out_dir / "welab_203002.pdf")


@generator("zabank_debit")
def zabank_debit(out_dir: Path) -> None:
    cols = [30, 95, 390, 450, 515, 580]
    line_h = 9.0
    font = 5.0
    pdf = PdfPage(font_size=font)

    def table(top: float, rows: list[list[str]]) -> float:
        """Draw a ruled table; each cell may hold several lines split by ``\\n``."""
        y = top
        for row in rows:
            n_lines = max(len(c.split("\n")) for c in row)
            h = n_lines * line_h + 4
            for i, cell in enumerate(row):
                rect = fitz.Rect(cols[i], y, cols[i + 1], y + h)
                pdf.page.draw_rect(rect, color=(0, 0, 0), width=0.5)
                for j, line in enumerate(cell.split("\n")):
                    pdf.put(cols[i] + 2, y + 2 + font + j * line_h, line)
            y += h
        return y

    header = [
        "Date 日期",
        "Transaction Details 交易详情",
        "Deposit 存入",
        "Withdrawal 支出",
        "Balance 结余",
    ]
    exchange = (
        "Currency exchange 货币兑换\n"
        "sell卖出 HKD 1,100.00 buy买入 CNY 1,000.00\n"
        "Transaction number 交易编号: 300310000001"
    )

    pdf.put(40, 60, "ZA Bank 众安银行 Consolidated Statement 综合月结单")
    pdf.put(40, 72, "01 Mar 2030 - 31 Mar 2030")

    pdf.put(40, 100, "HKD Savings 港元活期储蓄")
    y = table(
        110,
        [
            header,
            ["01 Mar 2030", "Opening balance 上期结余", "", "", "5,000.00"],
            ["03 Mar 2030", "Transfer in 转入\nEMPLOYER*A", "12,000.00", "", "17,000.00"],
            ["06 Mar 2030", "Transfer out 转出\nLANDLORD*B", "", "7,000.00", "10,000.00"],
            ["10 Mar 2030", exchange, "", "1,100.00", "8,900.00"],
            ["18 Mar 2030", "Card spending 消费\nSHOP*D HONG KONG", "", "85.00", "8,815.00"],
            ["31 Mar 2030", "Interest 利息", "3.50", "", "8,818.50"],
        ],
    )  # fmt: skip

    pdf.put(40, y + 30, "CNY 人民币")
    table(
        y + 40,
        [
            header,
            ["01 Mar 2030", "Opening balance 上期结余", "", "", "0.00"],
            ["10 Mar 2030", exchange, "1,000.00", "", "1,000.00"],
            ["15 Mar 2030", "Card spending 消费\nALIPAY*STORE-C", "", "260.00", "740.00"],
            ["20 Mar 2030", "Card spending refund 消费冲正\nALIPAY*STORE-C", "60.00", "", "800.00"],
        ],
    )  # fmt: skip
    pdf.save(out_dir / "zabank_203003.pdf")


@generator("spdb_debit")
def spdb_debit(out_dir: Path) -> None:
    # Wider than the bank's columns so the built-in font fits each cell; cells
    # are pre-wrapped the way the bank's PDF wraps them at the column width.
    cols = [20 + 62 * i for i in range(10)]
    line_h = 9.0
    pdf = PdfPage(font_size=5.0)
    put = pdf.put

    put(138, 50, "上海浦东发展银行个人客户交易流水专用回单")
    put(126, 70, "Transaction Statement of Shanghai Pudong Development Bank")
    put(90, 92, "户名: 测试用户  账号: 6200000000001234  起止日期: 20300301-20300331")
    put(90, 112, "Name: TEST USER  Account No:  Start & End Date")
    put(90, 132, "币种: 人民币  钞汇标志: 钞户  交易类型: 全部")
    put(90, 152, "Currency: CNY  Cash/Exchange: Cash  Transaction Type: ALL")

    card = "62000000000\n01234"
    rows = [
        ["交易日期\nDate", "交易时间\nTime", "交易账号\nTransaction\nAccount", "交易名称\nTransaction\nName", "交易金额\nTransaction\nAmount", "账户余额\nBalance", "对手姓名\nCounter\nParty", "对手账号\nOpponent\nAccount", "交易摘要\nSummary"],
        ["20300305", "093000", card, "代发工资\n30030500000\n001", "8000.00", "9000.00", "雇主甲有限\n公司", "100000001", "工资"],
        ["20300308", "121500", card, "网上支付-渠\n道", "-35.00", "8965.00", "商户乙有限\n公司", "100000002", ""],
        ["20300312", "190000", card, "网上支付-渠\n道", "-24.00", "8941.00", "餐厅丙", "100000003", ""],
        ["20300315", "101000", card, "网上支付-渠\n道退款", "15.00", "8956.00", "商户乙有限\n公司", "100000002", ""],
        ["20300320", "200000", card, "信用卡还款\n5678", "-300.00", "8656.00", "测试用户", "400000000\n0005678", ""],
        ["20300321", "020000", card, "结息", "1.20", "8657.20", "", "", "利息"],
    ]  # fmt: skip

    y = 161.0
    for row in rows:
        h = line_h * max(c.count("\n") + 1 for c in row) + 6
        for i, cell in enumerate(row):
            rect = fitz.Rect(cols[i], y, cols[i + 1], y + h)
            pdf.page.draw_rect(rect, color=(0, 0, 0), width=0.5)
            for j, line in enumerate(cell.split("\n")):
                put(cols[i] + 2, y + 9 + j * line_h, line)
        y += h
    put(250, 800, "第1页/共1页，Page 1 of 1")
    pdf.save(out_dir / "spdb_debit_203003.pdf")
