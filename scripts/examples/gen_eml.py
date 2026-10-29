"""HTML e-mail credit card statements."""

from pathlib import Path

from _common import generator, write_eml
from gen_pdf import (
    abc_credit_reissue,
    ccb_credit_reissue,
    cgb_credit_reissue,
    cmb_credit_reissue,
    icbc_credit_export,
    icbc_credit_reissue,
)


@generator("abc_credit")
def abc_credit(out_dir: Path) -> None:
    # A month only the reissue covers.
    abc_credit_reissue(
        out_dir / "农业银行信用卡对账单2030年02月（补）.pdf",
        "2030年02月",
        "4321",
        "2030/02/01-2030/02/28",
        "-144.00",
        [
            ("300204", "300204", "4321", "网上消费 支付宝，store-f", "-62.00"),
            ("300211", "300212", "4321", "消费 store-g", "-82.00"),
            ("300218", "300218", "", "约定还款", "893.00"),
        ],
    )
    # (trans_date, post_date, card, description, bank-signed amount)
    rows = [
        ("300303", "300303", "4321", "网上消费 支付宝，store-a", "-38.00"),
        ("300306", "300307", "4321", "网上消费 财付通，restaurant-b午餐", "-56.00"),
        ("300309", "300310", "4321", "消费 store-c", "-120.00"),
        ("300312", "300313", "4321", "退货 store-c", "120.00"),
        ("300315", "300315", "", "约定还款", "1,500.00"),
        ("300320", "300320", "4321", "分期手续费", "-6.00"),
    ]
    formula_labels = [
        ("币种", "Curr"),
        ("本期应还金额", "New Balance"),
        "-",
        ("本期账户溢缴款", "Deposit"),
        "=",
        ("上期账单应还金额", "Previous Balance"),
        "-",
        ("上期账户溢缴款", "Previous Deposit"),
        "+",
        ("本期账单金额", "New Charge"),
        "-",
        ("本期还款、退货金额", "Payments"),
        "-",
        ("本期调整金额", "Adjustment"),
    ]
    formula_values = [
        "人民币(CNY)",
        "95.00",
        "0.00",
        "1500.00",
        "0.00",
        "220.00",
        "1620.00",
        "5.00",
    ]
    labels = "\n".join(
        f"<td><span>{c}</span></td>"
        if isinstance(c, str)
        else f"<td><span>{c[0]}</span><span>{c[1]}</span></td>"
        for c in formula_labels
    )
    values = "\n".join(f"<td><span>{v}</span></td>" for v in formula_values)
    body = "\n".join(
        f"""    <tr>
        <td>{t}</td>
        <td>{p}</td>
        <td>{c}</td>
        <td>{d}</td>
        <td>{a}/CNY</td>
        <td>{a}/CNY</td>
    </tr>"""
        for t, p, c, d, a in rows
    )
    html = f"""<html>
<head><title>金穗信用卡电子对账单</title></head>
<body>
<table>
    <tr><td><span>620000******4321</span></td></tr>
    <tr><td><span>2030/03/01-2030/03/31</span></td></tr>
    <tr><td><span>本期应还款额</span></td></tr>
    <tr><td>-95.00</td></tr>
</table>
<div>
<table>
<tr>
{labels}
</tr>
</table>
<table>
<tr>
{values}
</tr>
</table>
</div>
<table>
{body}
</table>
<table>
<tr><td><span>本期使用刷卡金</span></td><td><span>5.00</span></td></tr>
</table>
</body>
</html>"""
    write_eml(
        out_dir / "农业银行金穗信用卡2030年03月电子账单.eml",
        html,
        "金穗信用卡电子对账单",
        encoding="base64",
    )


_BOCOM_HEADER = """                <tr>
                    <td></td>
                    <td>交易日期</td>
                    <td>记账日期</td>
                    <td>卡末四位</td>
                    <td>交易说明</td>
                    <td>交易金额</td>
                    <td>入账金额</td>
                </tr>"""


def _bocom_table(rows: list[tuple[str, str, str, str, str]]) -> str:
    body = "\n".join(
        f"""                <tr>
                    <td></td>
                    <td>{t}</td>
                    <td>{p}</td>
                    <td>4321</td>
                    <td>{d}</td>
                    <td>{o}</td>
                    <td>{b}</td>
                </tr>"""
        for t, p, d, o, b in rows
    )
    return f"""            <table>
{_BOCOM_HEADER}
{body}
            </table>"""


@generator("bocom_credit")
def bocom_credit(out_dir: Path) -> None:
    payments = [
        ("03/13", "03/14", "退货 store-d", "CNY120.00", "CNY120.00"),
        ("03/15", "03/15", "刷卡金返还 活动抵扣", "CNY5.00", "CNY5.00"),
        ("03/18", "03/18", "信用卡还款 转账还款-银联", "CNY1500.00", "CNY1500.00"),
    ]
    spending = [
        ("03/03", "03/03", "消费 支付宝-store-a", "CNY38.00", "CNY38.00"),
        ("03/06", "03/07", "消费 财付通-restaurant-b午餐", "CNY56.00", "CNY56.00"),
        ("03/09", "03/10", "消费 overseas-shop-c", "HKD100.00", "CNY90.00"),
        ("03/11", "03/12", "消费 store-d", "CNY120.00", "CNY120.00"),
    ]
    html = f"""<html>
<head><title>交通银行信用卡电子账单</title></head>
<body>
<table>
    <tr><td>交通银行个人信用卡 622200******4321</td></tr>
    <tr><td>账单周期 2030/03/01-2030/03/31</td></tr>
</table>
<table>
    <tr><td>还款、退货、费用返还明细</td></tr>
    <tr>
        <td>
{_bocom_table(payments)}
        </td>
    </tr>
    <tr><td>消费、取现、其他费用明细</td></tr>
    <tr>
        <td>
{_bocom_table(spending)}
        </td>
    </tr>
</table>
</body>
</html>"""
    write_eml(
        out_dir / "交通银行个人信用卡2030年03月电子账单.eml",
        html,
        "交通银行信用卡电子账单",
        encoding="base64",
        charset="gbk",
    )


@generator("bosc_credit")
def bosc_credit(out_dir: Path) -> None:
    rows = [
        ("2030年03月03日", "2030年03月04日", "store-a 消费", "38.00+"),
        ("2030年03月06日", "2030年03月07日", "restaurant-b 午餐", "56.00+"),
        ("2030年03月09日", "2030年03月10日", "store-c 消费", "120.00+"),
        ("2030年03月12日", "2030年03月13日", "退款-store-c", "120.00-"),
        ("2030年03月15日", "2030年03月15日", "还款", "500.00-"),
        ("2030年03月20日", "2030年03月21日", "分期手续费", "6.00+"),
    ]
    body = "\n".join(
        f"""    <tr loop2="{i}">
        <td>{t}</td>
        <td>{p}</td>
        <td>{d}</td>
        <td>{a}</td>
        <td>4321</td>
    </tr>"""
        for i, (t, p, d, a) in enumerate(rows, 1)
    )
    html = f"""<html>
<head><title>上海银行信用卡电子对账单</title></head>
<body>
<table>
    <tr><td>对账周期：2030年03月01日-2030年03月31日</td></tr>
</table>
<table>
    <tr><td>人民币账户</td></tr>
{body}
</table>
</body>
</html>"""
    write_eml(
        out_dir / "上海银行信用卡2030年03月电子对账单.eml",
        html,
        "上海银行信用卡电子对账单",
    )


@generator("ccb_credit")
def ccb_credit(out_dir: Path) -> None:
    # A month only the reissue covers, with a purchase billed in another currency.
    ccb_credit_reissue(
        out_dir / "中国建设银行信用卡对账单2030年02月.pdf",
        "2030/02/01-2030/02/28",
        [
            (
                "2030-02-04",
                "2030-02-04",
                "4321",
                "支付宝-store-f",
                "CNY 71.00",
                "CNY 71.00",
            ),
            ("2030-02-09", "2030-02-10", "4321", "shop-g", "HKD 82.00", "CNY 74.00"),
            (
                "2030-02-16",
                "2030-02-17",
                "4321",
                "银联入账 还款",
                "CNY -930.00",
                "CNY -930.00",
            ),
        ],
    )
    rows = [
        ("2030-03-03", "2030-03-03", "支付宝-store-a", "38.00"),
        ("2030-03-06", "2030-03-07", "财付通-restaurant-b午餐", "56.00"),
        ("2030-03-09", "2030-03-09", "store-c", "120.00"),
        ("2030-03-12", "2030-03-13", "退货-store-c", "-120.00"),
        ("2030-03-14", "2030-03-14", "年费", "200.00"),
        ("2030-03-14", "2030-03-14", "积分兑换年费", "-200.00"),
        ("2030-03-18", "2030-03-19", "银联入账 还款", "-1,500.00"),
    ]
    body = "\n".join(
        f"""    <tr>
        <td>{t}</td>
        <td>{p}</td>
        <td>4321</td>
        <td>{d}</td>
        <td>CNY</td>
        <td>{a}</td>
        <td>CNY</td>
        <td>{a}</td>
    </tr>"""
        for t, p, d, a in rows
    )
    html = f"""<html>
<head><title>龙卡信用卡对账单</title></head>
<body>
<table>
    <tr><td>账单周期Statement Cycle</td><td>2030/03/01-2030/03/31</td></tr>
</table>
<table>
    <tr><td>【交易明细】</td></tr>
    <tr>
        <td>交易日</td>
        <td>银行记账日</td>
        <td>卡号后四位</td>
        <td>交易描述</td>
        <td>交易币/金额</td>
        <td>结算币/金额</td>
    </tr>
    <tr>
        <td>[人民币账户] RMB Account</td>
        <td>上期账单余额(Previous Balance)</td>
        <td>1,500.00</td>
    </tr>
{body}
</table>
</body>
</html>"""
    write_eml(
        out_dir / "中国建设银行信用卡电子账单.eml",
        html,
        "中国建设银行信用卡电子账单",
        encoding="base64",
    )


def _cgb_table(card: str, rows: list[str]) -> str:
    header = (
        "<tr><td>交易日期</td><td>入账日期</td><td>交易摘要</td><td>交易金额</td>"
        "<td>交易货币</td><td>入账金额</td><td>入账货币</td></tr>"
    )
    body = "\n".join(f"    <tr><td>{r}</td></tr>" for r in rows)
    return f"""<table>
    卡号：6200********{card}
    {header}
{body}
</table>"""


@generator("cgb_credit")
def cgb_credit(out_dir: Path) -> None:
    # A month only the reissue covers.
    cgb_credit_reissue(
        out_dir / "cgb_credit-2030-02.pdf",
        ("2030/02/01", "2030/02/28"),
        [
            (
                "4321",
                [
                    (
                        "2030/02/05",
                        "2030/02/06",
                        "(消费)支付宝-store-f",
                        "87.00",
                        "人民币",
                        "87.00",
                        "人民币",
                    ),
                    (
                        "2030/02/18",
                        "2030/02/18",
                        "(还款)网银还款",
                        "-1,170.00",
                        "人民币",
                        "-1,170.00",
                        "人民币",
                    ),
                ],
            ),
            (
                "8765",
                [
                    (
                        "2030/02/11",
                        "2030/02/12",
                        "(消费)store-g",
                        "93.00",
                        "人民币",
                        "93.00",
                        "人民币",
                    )
                ],
            ),
        ],
    )
    issuer = "广发银行"
    card_a = [
        "2030/03/03 2030/03/04 (消费)支付宝-store-a 38.00 人民币 38.00 人民币",
        "2030/03/06 2030/03/07 (消费)财付通-restaurant-b午餐 56.00 人民币 56.00 人民币",
        "2030/03/09 2030/03/10 (消费)overseas-shop-c 20.00 美元 145.00 人民币",
        "2030/03/15 2030/03/15 (赠送)bonus-d -5.00 人民币 -5.00 人民币",
        "2030/03/18 2030/03/18 (还款)网银还款 -1,500.00 人民币 -1,500.00 人民币",
    ]
    card_b = [
        "2030/03/11 2030/03/12 (消费)store-e 120.00 人民币 120.00 人民币",
        "2030/03/13 2030/03/14 (还款)退货-store-e -120.00 人民币 -120.00 人民币",
    ]
    html = f"""<!DOCTYPE html>
<html>
<head><title>{issuer}信用卡电子账单</title></head>
<body>
<table>
    <tr><td>感谢您使用{issuer}信用卡，以下是您2030年03月的信用卡账单：</td></tr>
    <tr><td>账单周期:2030/03/01-2030/03/31</td></tr>
    <tr><td>账单日:2030/03/31</td></tr>
</table>
{_cgb_table("4321", card_a)}
{_cgb_table("8765", card_b)}
</body>
</html>"""
    write_eml(
        out_dir / "cgb_credit-2030-03.eml",
        html,
        f"{issuer}信用卡电子账单",
        encoding="base64",
        charset="gbk",
    )


@generator("cib_credit")
def cib_credit(out_dir: Path) -> None:
    rows = [
        ("2030-03-03", "2030-03-03", "支付宝快捷--store-a", "38.00"),
        ("2030-03-06", "2030-03-06", "财付通快捷--restaurant-b午餐", "56.00"),
        ("2030-03-08 19:30", "2030-03-09", "charger-c充电", "45.00"),
        ("2030-03-10", "2030-03-10", "store-d", "120.00"),
        ("2030-03-12", "2030-03-13", "退货--store-d", "-120.00"),
        ("2030-03-15", "2030-03-15", "活动刷卡金", "-5.00"),
        ("2030-03-18", "2030-03-18", "网银还款", "-1,500.00"),
    ]
    body = "\n".join(
        f"""        <tr id="detail_tr_100">
            <td>&nbsp;<span id="detail_tdate_100">{t}</span></td>
            <td><span id="detail_adate_100">{p}</span></td>
            <td><span id="detail_desc1_100">{d}</span></td>
            <td align="right"></td>
            <td align="right"><span id="detail_tamt_100">{a}</span></td>
        </tr>"""
        for t, p, d, a in rows
    )
    html = f"""<html>
<head><meta charset="gbk"><title>兴业银行信用卡对账单</title></head>
<body>
<table>
    <tr><td>卡片名称：<span id="card_name">card-name-a</span></td></tr>
    <tr><td>卡号末四位：<span id="cardno_last4">4321</span></td></tr>
    <tr><td>账单周期：<span id="period">2030/03/01-2030/03/31</span></td></tr>
</table>
<table id="detail_table_100" width="100%">
    <tbody>
        <tr id="detail_table_head_100">
            <td>交易日期<br>Trans Date</td>
            <td>记账日期<br>Post Date</td>
            <td>交易摘要<br>Trans Description</td>
            <td>交易地金额<br>Trans Amount</td>
            <td>记账币金额<br>Amount(RMB)</td>
        </tr>
        <td colspan="20" name="masterMsg">**** 本卡明细(卡号末四位 4321) ****</td>
{body}
    </tbody>
</table>
</body>
</html>"""
    write_eml(
        out_dir / "兴业银行信用卡2030年03月电子账单.eml",
        html,
        "兴业银行信用卡2030年03月电子账单",
        encoding="base64",
        charset="gbk",
    )


@generator("cmb_credit")
def cmb_credit(out_dir: Path) -> None:
    # (trans_date, post_date, description, amount, card, original_amount, country)
    rows = [
        ("0303", "0304", "支付宝-store-a", "39.30", "4321", "39.30", "CN"),
        ("0306", "0307", "财付通-restaurant-b午餐", "53.80", "4321", "53.80", "CN"),
        ("0309", "0310", "overseas-shop-c", "88.80", "8765", "102.10", "HK"),
        ("0311", "0312", "store-d", "123.10", "8765", "123.10", "CN"),
        ("0313", "0314", "退款-store-d", "-123.10", "8765", "-123.10", "CN"),
        ("", "0318", "银联在线网络还款", "-1,502.10", "4321", "-1,502.10", ""),
    ]
    body = "\n".join(
        f"""    <tr>
        <td>{t}{p}{d}¥ {a}{c}{o}</td>
        <td></td>
        <td>{t}</td>
        <td>{p}</td>
        <td>{d}</td>
        <td>¥ {a}</td>
        <td>{c}</td>
        <td>{o}</td>
        <td>{n}</td>
    </tr>"""
        for t, p, d, a, c, o, n in rows
    )
    html = f"""<!DOCTYPE HTML>
<html>
<head><meta charset="UTF-8"><title>招商银行信用卡电子账单</title></head>
<body>
<div>您2030年03月信用卡账单已出</div>
<table class="bgTable">
{body}
</table>
</body>
</html>"""
    write_eml(
        out_dir / "招商银行信用卡电子账单2030年03月.eml",
        html,
        "招商银行信用卡电子账单",
        encoding="base64",
    )

    # The reissue of the mailed month loses to the mail; February has no mail.
    def pdf_row(trans, post, desc, amount, card, original, country):
        return (
            f"{trans[:2]}/{trans[2:]}" if trans else "",
            f"{post[:2]}/{post[2:]}",
            desc,
            amount,
            card,
            f"{original}({country})" if country else original,
        )

    cmb_credit_reissue(
        out_dir / "reissue-2030-03.pdf",
        2030,
        3,
        22,
        [("消费", [pdf_row(*row) for row in rows])],
    )
    cmb_credit_reissue(
        out_dir / "reissue-2030-02.pdf",
        2030,
        2,
        22,
        [
            (
                "还款",
                [("", "02/03", "银联在线网络还款", "-1,357.00", "4321", "-1,357.00")],
            ),
            (
                "分期",
                [
                    (
                        "09/18",
                        "02/05",
                        "消费分期-store-e 006-012",
                        "146.50",
                        "4321",
                        "146.50",
                    )
                ],
            ),
            (
                "消费",
                [
                    ("02/08", "02/09", "支付宝-store-a", "57.30", "4321", "57.30(CN)"),
                    ("02/12", "02/13", "overseas-shop-c", "83.40", "8765", "92.60(HK)"),
                ],
            ),
            (
                "退款",
                [("02/18", "02/19", "退款-store-a", "-57.30", "4321", "-57.30(CN)")],
            ),
        ],
    )


def _cmbc_rows(
    row_id: str,
    cell_ids: list[str],
    rows: list[tuple[str, ...]],
    wrap_id: str = "",
) -> str:
    desc_id, amt_id, card_id = cell_ids
    open_tag = f"<span id='{row_id}'>" + (f"<span id='{wrap_id}'>" if wrap_id else "")
    close_tag = "</span></span>" if wrap_id else "</span>"
    return "\n".join(
        f"""        {open_tag}
            <font>{t}</font><font>{p}</font>
            <span id='{desc_id}'><font>{d}</font></span>
            <span id='{amt_id}'><font>{a}</font></span>
            <span id='{card_id}'><font>{c}</font></span>
        {close_tag}"""
        for t, p, d, a, c in rows
    )


def _cmbc_section(
    loop_id: str, band_ids: tuple[str, ...], rows: list[tuple[str, ...]]
) -> str:
    row_id, wrap_id, *cell_ids = band_ids
    spans = _cmbc_rows(row_id, cell_ids, rows, wrap_id)
    return f"    <span id='{loop_id}'>\n{spans}\n    </span>"


@generator("cmbc_credit")
def cmbc_credit(out_dir: Path) -> None:
    purchases = [
        ("03/03", "03/03", "支付宝-store-a", "38.00", "4321"),
        ("03/06", "03/07", "财付通-restaurant-b午餐", "56.00", "4321"),
        ("03/09", "03/10", "store-c", "120.00", "8765"),
        ("03/11", "03/11", "分期手续费", "6.00", "4321"),
    ]
    refunds = [
        ("03/13", "03/13", "退货-store-c", "-120.00", "8765"),
        ("03/14", "03/16", "退货-store-d", "-25.00", "4321"),
    ]
    payments = [
        ("03/15", "03/15", "银联入账还款", "-1,500.00", "4321"),
        ("03/17", "03/17", "网银还款", "-200.00", "4321"),
    ]
    purchase_spans = "\n".join(
        f"""        <span id='fixBand1'>
            <span id='fixBand8'>{t}{p}{d}{a}{c}</span>
            <span id='fixBand82'>{d}</span>
            <span id='fixBand22'>{a}</span>
            <span id='fixBand9'>{c}</span>
        </span>"""
        for t, p, d, a, c in purchases
    )
    refund_section = _cmbc_section(
        "loopBand7",
        ("fixBand39", "fixBand40", "fixBand65", "fixBand64", "fixBand41"),
        refunds,
    )
    payment_section = _cmbc_section(
        "loopBand5",
        ("fixBand15", "fixBand16", "fixBand19", "fixBand18", "fixBand17"),
        payments,
    )
    html = f"""<!DOCTYPE HTML>
<html>
<head><meta charset="UTF-8"><title>民生银行信用卡电子对账单</title></head>
<body>
<div>2030年3月对账单</div>
<div>Statement Date</div><div>2030/03/20</div>
<span id='loopBand3'>
    <span id='loopBand1'>
{purchase_spans}
    </span>
{refund_section}
{payment_section}
</span>
</body>
</html>"""
    write_eml(
        out_dir / "民生信用卡2030年03月电子对账单.eml",
        html,
        "民生信用卡电子对账单",
        encoding="base64",
    )
    # Earlier statements use a single table: refunds and payments are rows
    # with negative amounts.
    rows = [
        ("02/03", "02/03", "支付宝-store-e", "45.00", "4321"),
        ("02/05", "02/06", "store-f", "88.00", "8765"),
        ("02/08", "02/09", "退货-store-e", "-45.00", "4321"),
        ("02/12", "02/12", "银联入账还款", "-900.00", "4321"),
    ]
    row_spans = _cmbc_rows("fixBand9", ["fixBand22", "fixBand8", "fixBand2"], rows)
    html = f"""<!DOCTYPE HTML>
<html>
<head><meta charset="UTF-8"><title>民生银行信用卡电子对账单</title></head>
<body>
<div>2030年2月对账单</div>
<div>Statement Date</div><div>2030/02/20</div>
<span id='fixBand1'><span id='reportPanel1'>
<span id='loopBand1'><span id='loopBand2'><span id='loopBand3'>
{row_spans}
</span></span></span>
</span></span>
</body>
</html>"""
    write_eml(
        out_dir / "民生信用卡2030年02月电子对账单.eml",
        html,
        "民生信用卡电子对账单",
        encoding="base64",
    )


_HXB_HEADER = """<table>
<tr><td>交易日</td></tr>
<tr><td>记账日</td></tr>
<tr><td>交易描述</td></tr>
<tr><td>卡号后四位</td></tr>
<tr><td>金额</td></tr>
</table>"""


def _hxb_rows(rows: list[tuple[str, str, str, str, str]]) -> str:
    return "\n".join(
        f"""<table>
<tr><td>{t}</td></tr>
<tr><td>{p}</td></tr>
<tr><td>{d}</td></tr>
<tr><td>{c}</td></tr>
<tr><td>{a}</td></tr>
</table>"""
        for t, p, d, c, a in rows
    )


@generator("hxb_credit")
def hxb_credit(out_dir: Path) -> None:
    cny_rows = [
        ("03/03", "03/03", "支付宝-store-a", "4321", "￥38.00"),
        ("03/06", "03/07", "财付通-restaurant-b午餐", "4321", "￥56.00"),
        ("03/09", "03/10", "store-c", "8765", "￥120.00"),
        ("03/12", "03/13", "退款-store-c", "8765", "-￥120.00"),
        ("03/15", "03/15", "网银还款", "4321", "-￥1,500.00"),
    ]
    usd_rows = [
        ("03/11", "03/12", "overseas-shop-d", "4321", "＄20.00"),
    ]
    html = f"""<html>
<head><title>华夏信用卡对账单</title></head>
<body>
<div>华夏信用卡对账单(2030/03)</div>
<div>账单日 每月20日</div>
<div>人民币账务信息</div>
{_HXB_HEADER}
{_hxb_rows(cny_rows)}
<div>美元账务信息</div>
<div>本期应还 ＄20.00</div>
{_HXB_HEADER}
{_hxb_rows(usd_rows)}
</body>
</html>"""
    write_eml(
        out_dir / "华夏信用卡-电子账单2030年03月.eml",
        html,
        "华夏信用卡对账单",
        encoding="base64",
    )


def _icbc_rows(card: str, rows: list[tuple[str, str, str, str, str, str]]) -> str:
    return "\n".join(
        f"<tr><td>{card}</td><td>{t}</td><td>{p}</td><td>{ty}</td>"
        f"<td>{m}</td><td>{o}</td><td>{b}</td></tr>"
        for t, p, ty, m, o, b in rows
    )


@generator("icbc_credit")
def icbc_credit(out_dir: Path) -> None:
    icbc_credit_reissue(out_dir / "中国工商银行信用卡对账单（补）2030年02月.pdf")
    icbc_credit_export(out_dir / "中国工商银行信用卡历史明细.pdf")
    rows = [
        (
            "2030-03-02",
            "2030-03-02",
            "消费",
            "财付通-store-a",
            "36.40/RMB",
            "36.40/RMB(支出)",
        ),
        (
            "2030-03-05",
            "2030-03-06",
            "消费",
            "支付宝-cafe-b",
            "26.00/RMB",
            "26.00/RMB(支出)",
        ),
        (
            "2030-03-09",
            "2030-03-10",
            "跨行消费",
            "overseas-shop-c",
            "97.50/HKD",
            "86.20/RMB(支出)",
        ),
        (
            "2030-03-12",
            "2030-03-12",
            "退货",
            "财付通-store-a",
            "36.40/RMB",
            "36.40/RMB(存入)",
        ),
        (
            "2030-03-15",
            "2030-03-15",
            "分期付款",
            "shop-d 第2/6期",
            "150.00/RMB",
            "150.00/RMB(支出)",
        ),
        (
            "2030-03-20",
            "2030-03-20",
            "信用卡还款",
            "支付宝",
            "1,200.00/RMB",
            "1,200.00/RMB(存入)",
        ),
    ]
    html = f"""<html>
<head><title>中国工商银行客户对账单</title></head>
<body>
<table><tr><td>信 用 卡 对 账 单</td></tr></table>
<table>
<tr><td>账单周期</td><td>2030年3月1日—2030年3月31日</td></tr>
<tr><td>对账单生成日</td><td>2030年3月31日</td></tr>
</table>
<table>
<tr><td>需 还 款 明 细</td></tr>
<tr><td>卡号后四位</td><td>币种</td><td>应还款额</td><td>最低还款额</td><td>信用额度</td></tr>
<tr><td>1111(牡丹贷记卡)</td><td>人民币(本位币)</td><td>262.20/RMB</td><td>26.22/RMB</td><td>10,000.00/RMB</td></tr>
</table>
<table>
<tr><td>人民币(本位币) 交 易 明 细</td></tr>
<tr><td>卡号后四位</td><td>交易日</td><td>记账日</td><td>交易类型</td><td>商户名称/城市</td><td>交易金额/币种</td><td>记账金额/币种</td></tr>
<tr><td>---主卡明细---</td></tr>
{_icbc_rows("1111", rows)}
</table>
<table><tr><td>网站</td><td>www.icbc.com.cn</td></tr></table>
</body>
</html>"""
    write_eml(
        out_dir / "中国工商银行客户对账单2030年03月.eml",
        html,
        "中国工商银行客户对账单(ICBC Peony Card Bank Statement)",
        sender="webmaster@icbc.example.com",
        charset="gbk",
    )


@generator("psbc_credit")
def psbc_credit(out_dir: Path) -> None:
    headers = (
        "交易日",
        "记账日",
        "交易摘要",
        "人民币金额",
        "卡号末四位",
        "交易国别",
        "境内外交易标识",
    )
    rows = [
        ("20300303", "20300303", "支付宝-store-a", "￥38.00", "4321", "CHN", "境内"),
        (
            "20300306",
            "20300307",
            "财付通-restaurant-b午餐",
            "￥56.00",
            "4321",
            "CHN",
            "境内",
        ),
        ("20300309", "20300310", "overseas-shop-c", "￥145.00", "4321", "USA", "境外"),
        ("20300311", "20300312", "store-d", "￥120.00", "4321", "CHN", "境内"),
        ("20300313", "20300314", "退货-store-d", "￥-120.00", "4321", "CHN", "境内"),
        ("20300315", "20300315", "网银还款", "￥-1,500.00", "4321", "", ""),
    ]
    header = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    body = "\n".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows
    )
    html = f"""<html><head>
<title>中国邮政储蓄银行信用卡对账单</title>
</head><body>
<p>您本期账单周期为【2030/03/01-2030/03/31】</p>
<section><table><tbody>
{header}
{body}
</tbody></table></section>
</body></html>"""
    write_eml(
        out_dir / "邮储银行信用卡电子账单2030年03月.eml",
        html,
        "邮储银行信用卡电子账单",
        encoding="base64",
    )
