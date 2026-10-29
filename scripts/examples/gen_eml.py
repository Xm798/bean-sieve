"""HTML e-mail credit card statements."""

from pathlib import Path

from _common import generator, write_eml


@generator("abc_credit")
def abc_credit(out_dir: Path) -> None:
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
        ("0303", "0304", "支付宝-store-a", "38.00", "4321", "38.00", "CN"),
        ("0306", "0307", "财付通-restaurant-b午餐", "56.00", "4321", "56.00", "CN"),
        ("0309", "0310", "overseas-shop-c", "90.00", "8765", "100.00", "HK"),
        ("0311", "0312", "store-d", "120.00", "8765", "120.00", "CN"),
        ("0313", "0314", "退款-store-d", "-120.00", "8765", "-120.00", "CN"),
        ("", "0318", "银联在线网络还款", "-1,500.00", "4321", "-1,500.00", ""),
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


def _cmbc_section(
    loop_id: str, band_ids: tuple[str, ...], rows: list[tuple[str, ...]]
) -> str:
    row_id, wrap_id, desc_id, amt_id, card_id = band_ids
    spans = "\n".join(
        f"""        <span id='{row_id}'><span id='{wrap_id}'>
            <font>{t}</font><font>{p}</font>
            <span id='{desc_id}'><font>{d}</font></span>
            <span id='{amt_id}'><font>{a}</font></span>
            <span id='{card_id}'><font>{c}</font></span>
        </span></span>"""
        for t, p, d, a, c in rows
    )
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
