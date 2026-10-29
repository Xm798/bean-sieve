"""PDF statements drawn with PyMuPDF."""

import re
from pathlib import Path

import fitz
from _common import PdfPage, generator
from gen_sheets import spdb_credit_xls


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


@generator("spdb_credit")
def spdb_credit(out_dir: Path) -> None:
    spdb_credit_xls(out_dir)

    # An older template without the original-amount column, its table
    # running onto a second page, for a month the XLS export does not cover.
    spdb_credit_pdf(
        out_dir / "浦发银行信用卡电子账单2030年04月.pdf",
        "203004",
        [
            [
                ["20300325", "20300325", "消费 store-a", "1234", "￥45.00"],
                ["20300402", "20300403", "网上消费 shop-b", "1234", "￥260.00"],
                ["20300406", "20300407", "退货 shop-b", "1234", "￥-260.00"],
                ["20300410", "20300410", "分期付款-shop-e\n第01期共03期", "5678", "￥120.00"],
            ],
            [
                ["20300415", "20300415", "还款", "1234", "￥-400.00"],
                ["20300418", "20300419", "消费 cafe-c", "5678", "￥18.00"],
            ],
        ],
    )  # fmt: skip
    # A reissue of the month the XLS export covers, on the newer template.
    spdb_credit_pdf(
        out_dir / "浦发银行信用卡电子账单2030年05月.pdf",
        "203005",
        [
            [
                ["20300503", "20300504", "消费 store-a", "1234", "￥31.00", "31.00(CNY)"],
                ["20300505", "20300506", "网上消费 shop-b", "1234", "￥150.00", "150.00(CNY)"],
                ["20300508", "20300509", "退货 shop-b", "1234", "￥-150.00", "-150.00(CNY)"],
                ["20300510", "20300512", "境外消费 merchant-x", "5678", "￥36.00", "5.00(USD)"],
                ["20300515", "20300516", "消费 cafe-c", "5678", "￥22.00", "22.00(CNY)"],
                ["20300520", "20300520", "还款", "1234", "￥-600.00", "-600.00(CNY)"],
            ],
        ],
    )  # fmt: skip


def spdb_credit_pdf(out: Path, month: str, pages: list[list[list[str]]]) -> None:
    pdf = PdfPage(font_size=6)
    put = pdf.put
    put(40, 40, f"{month}信用卡账单", 14)
    put(40, 70, "您好!感谢您使用本行信用卡，现呈上您浦发个人信用卡对账单。")
    put(40, 95, "账单日：")
    put(40, 105, "Statement Due Date")
    put(40, 115, "20")
    put(40, 145, "本期账务明细(注：如明细未显示，请于隔天下载)")
    # The font's digits are full width; these gaps keep every cell its own
    # text line, as in the bank's PDF, where adjacent cells would merge.
    xs = [30, 95, 160, 330, 370, 440]
    headers = [
        ("交易日期", "Transaction Date"),
        ("记账日期", "Posting Date"),
        ("交易摘要", "Description"),
        ("卡号末四位", "Card No."),
        ("交易金额", "Amount"),
        ("原始交易金额&币种", "Original Trans Amount & Currency"),
    ]
    for x, (zh, en) in zip(xs, headers[: len(pages[0][0])], strict=False):
        put(x, 165, zh)
        put(x, 175, en, 5)

    y = 195.0
    for number, rows in enumerate(pages, start=1):
        if number > 1:
            pdf.page = pdf.doc.new_page(width=595, height=842)
            put(40, 40, "温馨提示：")
            y = 70.0
        for row in rows:
            for x, cell in zip(xs, row, strict=False):
                # A long description wraps within its cell.
                for i, line in enumerate(cell.split("\n")):
                    put(x, y + i * 8, line)
            y += 18
        put(260, 820, f"第 {number} 页 共 {len(pages)} 页")
    pdf.save(out)


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


def cmb_credit_reissue(
    out: Path,
    year: int,
    month: int,
    closing_day: int,
    sections: list[tuple[str, list[tuple[str, ...]]]],
) -> None:
    """Draw a CMB reissued statement (补发账单) at the bank's column positions.

    A section is (label, rows); the label sits in the 交易日 column of its
    first row, as the bank prints 还款 / 消费 / 退款. A row is (trans, post,
    description, amount, card, original), "" for an empty cell.
    """
    pdf = PdfPage(font_size=8)
    # The bank's page is wider than A4; the 交易地金额 column needs the room.
    pdf.page.set_mediabox(fitz.Rect(0, 0, 624, 863))
    pdf.put(45, 60, "账单日")
    pdf.put(45, 72, f"{year}年{month:02d}月{closing_day:02d}日")
    pdf.put(45, 84, "Statement Date")
    pdf.put(42, 120, "人民币账户 RMB A/C")
    xs = [43, 92, 140, 369, 455, 542]
    headers = ["交易日", "记账日", "交易摘要", "人民币金额", "卡号末四位", "交易地金额"]
    for x, label in zip(xs, headers, strict=True):
        pdf.put(x, 134, label)
    y = 150
    for label, rows in sections:
        pdf.put(xs[0], y, label)
        for row in rows:
            for x, cell in zip(xs, row, strict=True):
                if cell:
                    pdf.put(x, y, cell)
            y += 13
    pdf.put(
        40, 820, f"招商银行信用卡对账单（个人消费卡账户 {year}年{month:02d}月）（补）"
    )
    pdf.put(40, 832, f"CMB Credit Card Statement ({year}.{month:02d})")
    pdf.save(out)


# The icbc_credit generator in gen_eml writes these next to its eml.
def icbc_credit_reissue(out: Path) -> None:
    pdf = PdfPage(font_size=6)
    # The print-time watermark comes first in the page's text, in large type.
    for y in (120, 400, 680):
        pdf.put(60, y, "2030-03-05 10:00:00 中国工商银行", 13)
    pdf.put(40, 200, "账单周期 2030年2月1日—2030年2月28日")
    pdf.put(40, 212, "对账单生成日 2030年2月28日")
    pdf.put(40, 240, "人 民 币 交 易 明 细", 13)
    xs = [20, 55, 120, 185, 240, 360, 460]
    for x, label in zip(
        xs,
        [
            "卡号后四位",
            "交易日",
            "记账日",
            "交易类型",
            "商户名称/城市",
            "交易金额/币种",
            "记账金额/币种",
        ],
        strict=True,
    ):
        pdf.put(x, 260, label)
    pdf.put(37, 276, "---主卡明细---")
    rows = [
        (
            "2030-02-03",
            "2030-02-03",
            "消费",
            ["财付通-market-e"],
            "64.00/RMB",
            "64.00/RMB(支出)",
        ),
        # A long merchant name wraps inside its cell.
        (
            "2030-02-10",
            "2030-02-11",
            "消费",
            ["支付宝-restaurant-f-", "branch-g"],
            "88.00/RMB",
            "88.00/RMB(支出)",
        ),
        (
            "2030-02-14",
            "2030-02-14",
            "年费减免",
            ["年费减免23.40元"],
            "0.00/RMB",
            "0.00/RMB(存入)",
        ),
        (
            "2030-02-18",
            "2030-02-18",
            "信用卡还款",
            ["支付宝"],
            "300.00/RMB",
            "300.00/RMB(存入)",
        ),
    ]
    y = 294.0
    for trans, post, kind, merchant, amount, booked in rows:
        for x, text in zip(xs[:4], ["1111", trans, post, kind], strict=True):
            pdf.put(x, y, text)
        for i, line in enumerate(merchant):
            pdf.put(xs[4], y + i * 12, line)
        pdf.put(xs[5], y, amount)
        pdf.put(xs[6], y, booked)
        y += 12 * len(merchant) + 10
    pdf.put(40, 700, "信用卡对账单（补）", 13)
    pdf.put(400, 800, "网站www.icbc.com.cn 服务热线 95588")
    pdf.save(out)


def icbc_credit_export(out: Path) -> None:
    # The export is a landscape table printed on a portrait page turned a
    # quarter: every line runs up the page, rows advance along x and columns
    # along y.
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.set_rotation(90)

    def put(x: float, y: float, text: str, size: float = 5) -> None:
        # Cells are centred on their column, as in the bank's export; china-s
        # advances every character, ASCII included, by one em.
        page.insert_text(
            (x, y + len(text) * size / 2),
            text,
            fontname="china-s",
            fontsize=size,
            rotate=90,
        )

    put(30, 400, "中国工商银行信用卡历史明细（电子版）", 12)
    put(55, 760, "卡号: 6222000000001111")
    put(55, 560, "户名：cardholder-a")
    put(55, 330, "起止日期：2030-03-20 — 2030-04-10")
    columns = [
        ("入账日期", 790),
        ("交易卡号", 720),
        ("收支", 650),
        ("交易币种", 610),
        ("交易金额", 560),
        ("入账币种", 510),
        ("入账金额", 460),
        ("账户余额", 400),
        ("对方户名", 340),
        ("对方账号", 280),
        ("摘要", 220),
        ("交易场所", 160),
    ]
    for label, y in columns:
        if label == "收支":
            put(76, y, "收")
            put(83, y, "支")
        else:
            put(80, y, label)
    rows = [
        (
            "2030-03-20",
            "10:20:30",
            "贷",
            "人民币",
            "1200.00",
            "人民币",
            "1200.00",
            "-311.00",
            "cardholder-a",
            "6222****8888",
            "转帐",
            ["支付宝"],
        ),
        (
            "2030-04-03",
            "12:00:00",
            "借",
            "人民币",
            "52.00",
            "人民币",
            "52.00",
            "-363.00",
            "",
            "",
            "消费",
            ["财付通-bakery-h-", "station-i"],
        ),
        (
            "2030-04-06",
            "09:15:00",
            "贷",
            "人民币",
            "300.00",
            "人民币",
            "300.00",
            "-63.00",
            "cardholder-a",
            "6222****9999",
            "转帐",
            ["手机银行"],
        ),
        (
            "2030-04-08",
            "18:40:00",
            "借",
            "港币",
            "50.00",
            "人民币",
            "46.00",
            "-109.00",
            "",
            "",
            "消费",
            ["overseas-shop-c"],
        ),
    ]
    # Column-major writes keep each cell its own line in the extracted text,
    # as in the bank's export; row-major writes would merge neighbouring cells.
    xs = [100.0]
    for row in rows[:-1]:
        xs.append(xs[-1] + 7 * max(2, len(row[-1])) + 4)
    for x, (day, clock, *_) in zip(xs, rows, strict=True):
        put(x, 790, day)
        put(x + 7, 790, clock)
    for x in xs:
        put(x + 3.5, 720, "6222000000001111")
    for col, (_, y) in enumerate(columns[2:11], start=2):
        for x, row in zip(xs, rows, strict=True):
            if row[col]:
                put(x + 3.5, y, row[col])
    for x, row in zip(xs, rows, strict=True):
        for i, line in enumerate(row[-1]):
            put(x + i * 7, 160, line)
    x = xs[-1] + 18
    put(x + 20, 400, "支出合计：143.00 收入合计：300.00")
    doc.set_metadata({})
    doc.save(out, garbage=4, deflate=True, no_new_id=True)
    doc.close()
