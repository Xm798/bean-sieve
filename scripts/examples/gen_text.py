"""Plain-text statements: CSV exports and the App Store HAR capture."""

import json
from pathlib import Path

from _common import generator, write_text


@generator("alipay")
def alipay(out_dir: Path) -> None:
    rows = [
        "2030-03-02 08:30:00,餐饮美食,coffee-shop-a,shop-a@example.com,拿铁,支出,12.00,余额,交易成功,2030030200000001,M0001,",
        "2030-03-03 12:00:00,日用百货,store-b,store-b@example.com,收纳盒,支出,35.00,招商银行信用卡(1234),交易成功,2030030300000002,M0002,",
        "2030-03-04 19:00:00,交通出行,taxi-c,taxi-c@example.com,打车,支出,23.00,花呗,交易成功,2030030400000003,M0003,",
        "2030-03-05 10:00:00,日用百货,store-d,store-d@example.com,item-d,支出,80.00,余额,交易成功,2030030500000004,M0004,",
        "2030-03-07 10:00:00,退款,store-d,store-d@example.com,退款-item-d,不计收支,80.00,余额,退款成功,2030030500000004_R001,M0004,",
        "2030-03-08 13:00:00,餐饮美食,restaurant-e,restaurant-e@example.com,午餐,支出,45.00,招商银行信用卡(1234)&红包,交易成功,2030030800000005,M0005,",
        "2030-03-09 20:00:00,转账红包,friend-f,friend-f@example.com,转账,收入,100.00,余额,交易成功,2030030900000006,,",
        "2030-03-10 15:00:00,日用百货,store-g,store-g@example.com,item-g1,支出,10.00,余额,交易成功,2030031000000007,M0007,",
        "2030-03-10 15:00:00,日用百货,store-g,store-g@example.com,item-g2,支出,15.00,余额,交易成功,2030031000000008,M0008,",
        "2030-03-11 09:00:00,投资理财,余额宝,,余额宝-转入,不计收支,200.00,余额,交易成功,2030031100000009,,",
        "2030-03-12 18:00:00,餐饮美食,restaurant-h,restaurant-h@example.com,晚餐,支出,30.00,余额,交易成功,2030031200000010,M0010,",
        "2030-03-13 11:00:00,日用百货,store-i,store-i@example.com,item-i,支出,50.00,余额,交易关闭,2030031300000011,M0011,",
        "2030-03-15 09:00:00,信用借还,花呗,,花呗主动还款,不计收支,23.00,招商银行储蓄卡(5678),交易成功,2030031500000012,,",
    ]
    header = [
        "-" * 84,
        "导出信息：",
        "姓名：示例用户",
        "支付宝账户：user@example.com",
        "起始时间：[2030-03-01 00:00:00]    终止时间：[2030-03-31 23:59:59]",
        "导出交易类型：[全部]",
        "导出时间：[2030-04-01 09:00:00]",
        f"共{len(rows)}笔记录",
        "收入：1笔 100.00元",
        "支出：10笔 300.00元",
        "不计收支：3笔 303.00元",
        "",
        "特别提示：",
        *(f"{i}.提示{i}" for i in range(1, 10)),
        "",
        "-" * 24 + "支付宝支付科技有限公司  电子客户回单" + "-" * 24,
    ]
    columns = "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注,"
    write_text(
        out_dir / "alipay.csv",
        "\n".join([*header, columns, *rows]) + "\n",
        encoding="gbk",
    )


@generator("cmb_debit")
def cmb_debit(out_dir: Path) -> None:
    # date, time, income, expense, balance, type, remark
    rows = [
        ("20300801", "09:00:00", "8500.00", "", "8500.00", "代发工资", "employer-a 工资"),
        ("20300803", "12:20:00", "", "34.00", "8466.00", "快捷支付", "支付宝-restaurant-b-午餐"),
        ("20300806", "20:40:00", "", "680.00", "7786.00", "快捷支付", "财付通-store-c"),
        ("20300809", "10:00:00", "", "2500.00", "5286.00", "信用卡还款", "招商银行信用卡还款"),
        ("20300812", "15:00:00", "", "1000.00", "4286.00", "转账汇款", "测试用户 6222****5678"),
        ("20300815", "11:30:00", "", "2000.00", "2286.00", "朝朝宝购买", "朝朝宝"),
        ("20300821", "02:00:00", "1.60", "", "2287.60", "账户结息", "结息"),
        ("20300826", "19:10:00", "", "27.00", "2260.60", "快捷支付", "支付宝-shop-d-晚餐"),
    ]  # fmt: skip
    lines = [
        '﻿"# 招商银行交易记录"',
        '"# 导出时间: [            2030-09-01 10:00:00]"',
        '"# 账    号: [一卡通:6214********1234   招商银行]"',
        '"# 币    种: [                         人民币]"',
        '"# 起始日期: [20300801]   终止日期: [20300831]"',
        '"# 过滤设置:  无"',
        "",
        '"交易日期","交易时间","收入","支出","余额","交易类型","交易备注"',
    ]
    # CMB prefixes the date, time and remark cells with a TAB.
    for d, t, inc, exp, bal, typ, remark in rows:
        lines.append(f'"\t{d}","\t{t}","{inc}","{exp}","{bal}","{typ}","\t{remark}"')
    lines += [
        "",
        '"# 收入合计: 2 笔，共 8501.60 元"',
        '"# 支出合计: 6 笔，共 6241.00 元"',
    ]
    write_text(out_dir / "CMB_20300801-20300831.csv", "\n".join(lines) + "\n")


@generator("icbc_debit")
def icbc_debit(out_dir: Path) -> None:
    header = (
        "交易日期,摘要,交易详情,交易场所,交易国家或地区简称,钞/汇,交易金额(收入),"
        "交易金额(支出),交易币种,记账金额(收入),记账金额(支出),记账币种,余额,对方户名,对方账户"
    )
    # ICBC exports latest first.
    # date, summary, detail, location, income, expense, balance, counterparty, counter_account
    rows = [
        ("2030-11-26", "消费", "支付宝-晚餐", "shop-d", "", "29.00", "3,974.60", "支付宝-shop-d", "2088****0002"),
        ("2030-11-21", "利息", "", "", "1.60", "", "4,003.60", "", ""),
        ("2030-11-15", "跨行汇款", "", "", "", "1,500.00", "4,002.00", "测试用户", "6222****5678"),
        ("2030-11-10", "代扣", "水电燃气费", "utility-a", "", "268.00", "5,502.00", "utility-a", "1100****0005"),
        ("2030-11-06", "消费", "财付通-store-c", "store-c", "", "199.00", "5,770.00", "财付通-store-c", "1000****0003"),
        ("2030-11-03", "消费", "支付宝-午餐", "restaurant-b", "", "31.00", "5,969.00", "支付宝-restaurant-b", "2088****0002"),
        ("2030-11-01", "工资", "代发工资", "", "6,000.00", "", "6,000.00", "employer-a", "1100****0001"),
    ]  # fmt: skip
    lines = [
        "﻿明细查询文件下载",
        "",
        '卡号: 6222****1234,"卡别名: "',
        "",
        '子账户序号: 00000,子账户类别: 活期,"子账户别名: "',
        "",
        header,
    ]
    for d, summary, detail, loc, inc, exp, bal, cp, ca in rows:
        lines.append(
            f'{d},"{summary}","{detail}","{loc}","CHN",钞,"-","-",-,'
            f'"{inc}","{exp}",人民币,"{bal}","{cp}","{ca}"'
        )
    lines += ["", "人民币合计,,,,,,,,"]
    write_text(out_dir / "hisdetail20301130.csv", "\n".join(lines))


@generator("jd")
def jd(out_dir: Path) -> None:
    header = """导出信息：
京东账号名：jd_example
申请时间：2030-07-01 09:00:00
日期区间：2030-06-01 至 2030-06-30
导出交易类型：全部
导出交易场景：全部
共：6笔记录
收入：1笔，15.00元
支出：3笔，284.00元
不计收支：2笔，220.00元

特别提示
1.提示一；
2.提示二；
3.提示三；
4.提示四；
5.提示五；
6.提示六；
7.提示七；
8.提示八。

交易时间,商户名称,交易说明,金额,收/付款方式,交易状态,收/支,交易分类,交易订单号,商家订单号,备注
"""
    rows = [
        ("2030-06-02 10:00:00", "store-a", "日用品", "59.00", "京东白条", "交易成功", "支出", "日用百货", "300000000001", "400000000001", ""),
        ("2030-06-04 20:30:00", "shop-b", "数码配件", "300.00(已退款120.00)", "招商银行信用卡(1234)", "交易成功", "支出", "数码电器", "300000000002", "400000000002", ""),
        ("2030-06-06 12:00:00", "shop-b", "退款-数码配件", "120.00", "招商银行信用卡(1234)", "退款成功", "不计收支", "退款", "300000000003", "400000000003", ""),
        ("2030-06-08 09:15:00", "shop-c", "价保返还", "15.00", "京东白条", "交易成功", "收入", "其他", "300000000004", "400000000004", ""),
        ("2030-06-10 18:00:00", "shop-d", "图书", "45.00", "京东白条", "交易成功", "支出", "图书音像", "300000000005", "400000000005", ""),
        ("2030-06-20 08:00:00", "京东白条", "白条还款", "100.00", "招商银行储蓄卡(5678)", "还款成功", "不计收支", "白条", "300000000006", "400000000006", ""),
    ]  # fmt: skip
    # JD puts a TAB after the timestamp and after each order number.
    body = "".join(
        f"{r[0]}\t," + ",".join(r[1:8]) + f",{r[8]}\t,{r[9]}\t,{r[10]}\n" for r in rows
    )
    write_text(out_dir / "京东交易流水.csv", header + body, encoding="utf-8-sig")


@generator("meituan")
def meituan(out_dir: Path) -> None:
    header = """美团交易账单明细
美团用户名：[example_user]
起始时间：[2030-08-01] 终止时间：[2030-08-31]
导出交易类型：[全部]
导出时间：[2030-09-01 09:00:00]
""
共：6笔记录
支出：4笔 153.00元
收入：1笔 40.00元
不计收支：1笔 100.00元
""
特别提示：
1. 提示一
2. 提示二
3. 提示三
4. 提示四
5. 提示五
""
【美团交易账单明细列表】
交易创建时间,交易成功时间,交易类型,订单标题,收/支,支付方式,订单金额,实付金额,交易单号,商家单号,备注
"""
    rows = [
        ("2030-08-02 12:00:00", "2030-08-02 12:00:03", "支付", "餐厅甲-午餐套餐", "支出", "美团月付", "¥35.00", "¥35.00", "TX0000000001", "MC0000000001", "/"),
        ("2030-08-05 19:00:00", "2030-08-05 19:00:02", "支付", "餐厅乙-晚餐外卖", "支出", "招商银行信用卡(1234)", "¥30.00", "¥25.00", "TX0000000002", "MC0000000002", "/"),
        ("2030-08-07 10:00:00", "2030-08-07 10:00:05", "支付", "酒店丙-房费", "支出", "美团月付", "¥40.00", "¥40.00", "TX0000000003", "MC0000000003", "/"),
        ("2030-08-08 09:00:00", "2030-08-08 09:00:04", "退款", "酒店丙-房费", "收入", "美团月付", "¥40.00", "¥40.00", "TX0000000004", "MC0000000003", "/"),
        ("2030-08-10 08:00:00", "2030-08-10 08:00:01", "支付", "电费充值50元", "支出", "招商银行信用卡(1234)", "¥53.00", "¥53.00", "TX0000000005", "MC0000000005", "/"),
        ("2030-08-20 08:00:00", "2030-08-20 08:00:02", "还款", "美团月付还款", "不计收支", "招商银行储蓄卡(5678)", "¥100.00", "¥100.00", "TX0000000006", "", "/"),
    ]  # fmt: skip
    # Meituan appends a TAB inside the quoted order and merchant IDs.
    body = "".join(",".join(r[:8]) + f',"{r[8]}\t","{r[9]}\t",{r[10]}\n' for r in rows)
    write_text(
        out_dir / "美团账单(20300801-20300831).csv",
        header + body,
        encoding="utf-8-sig",
    )


def _har_item(
    item_id: str,
    amount: str,
    date: str,
    name: str,
    detail: str,
    media: str,
    *,
    free: bool = False,
    credit: bool = False,
) -> dict:
    return {
        "itemId": item_id,
        "adamId": "9" + item_id[-9:],
        "amountPaid": amount,
        "pliDate": date,
        "isFreePurchase": free,
        "isCredit": credit,
        "localizedContent": {
            "nameForDisplay": name,
            "detailForDisplay": detail,
            "mediaType": media,
        },
        "lineItemType": "BaseLineItem",
        "estimatedTotal": amount,
    }


def _har_purchase(weborder: str, date: str, items: list[dict]) -> dict:
    return {
        "purchaseId": "8" + weborder[-11:],
        "weborder": weborder,
        "purchaseDate": date,
        "invoiceDate": date,
        "isPendingPurchase": False,
        "plis": items,
    }


def _har_entry(purchases: list[dict], batch: str) -> dict:
    return {
        "request": {
            "method": "POST",
            "url": "https://reportaproblem.apple.com/api/purchase/search",
            "postData": {
                "mimeType": "application/json",
                "text": json.dumps({"batchId": batch}),
            },
        },
        "response": {
            "status": 200,
            "content": {
                "mimeType": "application/json",
                "text": json.dumps({"purchases": purchases}, ensure_ascii=False),
            },
        },
    }


@generator("app_store")
def app_store(out_dir: Path) -> None:
    iap = "App 内购买项目"
    page1 = [
        _har_purchase(
            "MXG0000000001",
            "2030-07-02T02:00:00Z",
            [_har_item("900000000000001", "¥12.00", "2030-07-02T02:00:00Z", "app-a", "app-a", "App")],
        ),
        # 17:30 UTC is 01:30 the next day in Beijing, the calendar the ledger uses.
        _har_purchase(
            "MXG0000000002",
            "2030-07-03T17:30:00Z",
            [
                _har_item("900000000000002", "¥25.00", "2030-07-03T17:30:00Z", "月度会员", "app-b", iap),
                _har_item("900000000000003", "¥6.00", "2030-07-03T17:30:00Z", "金币包", "app-b", iap),
            ],
        ),
        _har_purchase(
            "MXG0000000003",
            "2030-07-05T08:00:00Z",
            [_har_item("900000000000004", "¥0.00", "2030-07-05T08:00:00Z", "app-c", "app-c", "App", free=True)],
        ),
    ]  # fmt: skip
    page2 = [
        _har_purchase(
            "MXG0000000004",
            "2030-07-08T04:00:00Z",
            [_har_item("900000000000005", "¥18.00", "2030-07-08T04:00:00Z", "年度订阅", "app-d", iap)],
        ),
        _har_purchase(
            "MXG0000000005",
            "2030-07-10T04:00:00Z",
            [_har_item("900000000000006", "¥18.00", "2030-07-10T04:00:00Z", "年度订阅", "app-d", iap, credit=True)],
        ),
    ]  # fmt: skip
    har = {
        "log": {
            "version": "1.2",
            "creator": {"name": "WebInspector", "version": "537.36"},
            "entries": [_har_entry(page1, ""), _har_entry(page2, "next-page-token")],
        }
    }
    write_text(
        out_dir / "reportaproblem.apple.com.har",
        json.dumps(har, ensure_ascii=False, indent=2) + "\n",
    )
