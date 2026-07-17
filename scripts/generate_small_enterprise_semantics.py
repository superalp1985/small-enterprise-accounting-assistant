#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Build semantic-only vocabulary, categories, and a fixed regression corpus."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from account_catalog import enrich_vocab_records, load_account_catalog, semantic_payload


VOCAB_PATH = ROOT / "vocab_library_small_enterprise.json"
CATEGORY_PATH = ROOT / "semantic_categories_small_enterprise.json"
CORPUS_PATH = ROOT / "tests" / "fixtures" / "semantic_regression_small_enterprise.json"


TERMS = {
    "1001": ["现金收付", "现金盘点", "钱箱现金", "收现金", "付现金"],
    "1002": ["银行账户收付", "对公账户余额", "银行转账款", "网银到账", "公户付款"],
    "1012": ["支付宝企业账户", "微信商户余额", "银行汇票存款", "外埠存款", "第三方支付余额"],
    "1101": ["短期理财投资", "一年内变现投资", "短期股票投资", "购买短期理财", "短期持有债券"],
    "1121": ["收到商业汇票", "应收银行承兑", "应收商业承兑", "客户开承兑票", "销售收到汇票"],
    "1122": ["客户赊欠货款", "销售款待收", "服务款未收", "客户还没付款", "挂客户往来"],
    "1123": ["预付供应商款", "采购预付款", "合同预付款", "先给供应商打款", "货还没到先付款"],
    "1131": ["应收现金股利", "已宣告未收股利", "应收投资分红", "被投企业已宣告分红", "分红款待收"],
    "1132": ["应收债券利息", "已计未收利息", "应收投资利息", "债券利息待收", "理财利息未到账"],
    "1221": ["员工暂借款", "押金保证金", "代员工垫款", "借给员工周转", "支付租赁押金"],
    "1401": ["计划成本采购材料", "材料采购在途核算", "采购材料计划价", "按计划成本买料", "材料采购成本归集"],
    "1402": ["已付款未到货", "已开票未入库", "商品运输途中", "货在路上", "采购货物尚未验收"],
    "1403": ["生产材料入库", "原料库存", "辅助材料库存", "采购原料入库", "领用生产原料"],
    "1404": ["材料计划实际差异", "材料成本节约差", "材料成本超支差", "计划价和实际价有差", "结转材料成本差异"],
    "1405": ["购进商品入库", "产成品入库", "待售商品库存", "商品还在仓库", "销售商品出库"],
    "1407": ["商品售价进价差", "进销差价", "售价金额核算差额", "商品售价比进价高的差额", "摊销商品进销差价"],
    "1408": ["委外加工材料", "外发加工物资", "委托工厂加工", "材料发给外厂加工", "收回委托加工物资"],
    "1411": ["低值易耗品", "包装物周转", "周转用具", "领用可重复使用工具", "购入包装箱周转"],
    "1421": ["待售生物资产", "养殖存栏资产", "种植作物资产", "养的鱼还没卖", "种的农作物未收获"],
    "1501": ["长期持有债券", "一年以上债券投资", "长期债券本金", "买入长期国债", "持有债券超过一年"],
    "1511": ["长期股权出资", "持有被投企业股权", "长期权益投资", "投资另一家公司股权", "长期持有公司股份"],
    "1601": ["购置机器设备", "办公设备原值", "运输车辆原值", "买电脑作为固定资产", "购入经营用设备"],
    "1602": ["计提设备折旧", "固定资产累计折旧", "车辆累计折旧", "本月提电脑折旧", "机器设备已经折旧"],
    "1604": ["未完工建设项目", "安装工程成本", "在建装修工程", "厂房还在建设", "设备安装尚未完工"],
    "1605": ["工程专用材料", "基建物资", "待领用工程设备", "为施工买的专用材料", "工程物料入库"],
    "1606": ["处置固定资产", "报废设备清理", "出售车辆清理", "旧设备正在变卖", "固定资产报废损失待结转"],
    "1621": ["产果生物资产", "役畜资产", "多年生经济林", "养的奶牛用于产奶", "果树作为生产资产"],
    "1622": ["生产性生物资产折旧", "经济林累计折旧", "役畜累计折旧", "本月计提奶牛折旧", "生产性生物资产已折旧"],
    "1701": ["外购软件资产", "专利商标成本", "土地使用权成本", "买的软件长期使用", "取得专利权"],
    "1702": ["无形资产摊销", "软件累计摊销", "专利累计摊销", "本月摊销软件", "无形资产已经摊销"],
    "1801": ["长期分摊费用", "装修费长期摊销", "一年以上待摊支出", "办公室装修分几年摊", "长期服务费分期摊销"],
    "1901": ["财产盘盈待处理", "财产盘亏待处理", "资产毁损待审批", "盘点发现少了货", "盘点多出设备待查"],
    "2001": ["一年内银行借款", "流动资金贷款", "短期贷款本金", "向银行借一年内周转款", "偿还短期贷款"],
    "2201": ["开出商业汇票", "应付银行承兑", "应付商业承兑", "采购给供应商开承兑", "到期支付承兑票"],
    "2202": ["欠供应商货款", "采购款未付", "服务费应付款", "货到了还没付款", "挂供应商往来"],
    "2203": ["预收客户款", "客户合同预付款", "发货前收款", "客户先打了订金", "服务还没做先收钱"],
    "2211": ["应付员工工资", "应付职工社保", "应付职工福利", "本月工资还没发", "计提员工薪酬"],
    "2221": ["应交增值税", "应交企业所得税", "应交附加税", "本期税款待缴", "计提应交税费"],
    "2231": ["应付借款利息", "已计未付利息", "贷款利息待支付", "银行利息还没付", "计提本月借款利息"],
    "2232": ["应付股东利润", "已分配未付利润", "应付投资者分红", "股东分红还没支付", "宣告利润分配"],
    "2241": ["股东代垫款", "收取押金", "其他暂收款", "老板先替公司付款", "收到员工保证金"],
    "2401": ["递延补助收益", "待以后确认收益", "附条件补助递延", "补贴要分期确认", "与资产相关补助未摊销"],
    "2501": ["一年以上银行借款", "长期贷款本金", "长期项目贷款", "向银行借三年期款", "偿还长期贷款本金"],
    "2701": ["融资租赁应付款", "分期购置资产款", "长期设备应付款", "设备款分三年支付", "融资租入固定资产欠款"],
    "3001": ["股东投入资本", "注册资本到账", "实缴资本", "老板往公司投入注册资金", "新增股东出资"],
    "3002": ["资本溢价", "股本溢价", "接受非收益性捐赠", "股东投入超过注册资本", "资本公积转增资本"],
    "3101": ["法定盈余公积", "任意盈余公积", "提取盈余公积", "从利润中提公积金", "盈余公积弥补亏损"],
    "3103": ["当年累计利润", "本期损益结转", "年度经营成果", "月末把收入费用转利润", "本年发生亏损"],
    "3104": ["未分配利润", "利润分配方案", "向股东分红", "年末利润转未分配", "弥补以前年度亏损"],
    "4001": ["产品生产成本", "直接材料人工", "在产品成本", "归集生产车间成本", "完工产品成本结转"],
    "4101": ["车间间接费用", "生产设备折旧费", "制造部门水电费", "工厂管理开支", "分配制造费用"],
    "4301": ["研究阶段支出", "开发项目支出", "研发人员材料费", "做新产品研发", "研发项目尚未完成"],
    "4401": ["建筑合同施工成本", "合同工程毛利", "工程项目施工", "归集工地人工材料", "结转建筑工程成本"],
    "4403": ["施工机械作业成本", "机械台班费用", "工程机械使用费", "挖掘机在项目上作业", "分配机械作业成本"],
    "5001": ["主营销售收入", "主要服务收入", "主营业务营业收入", "卖商品确认收入", "提供服务收到营业款"],
    "5051": ["非主营营业收入", "出租资产收入", "销售材料收入", "偶尔出租设备收款", "卖多余材料取得收入"],
    "5111": ["股权投资收益", "债券投资收益", "处置投资收益", "收到被投公司分红", "卖出投资取得收益"],
    "5301": ["非日常经营收益", "资产盘盈收益", "无需支付款项", "收到与日常经营无关的奖励", "债务不用再偿还"],
    "5401": ["已售商品成本", "主营服务成本", "主营销售成本", "卖出商品结转成本", "结转本月服务项目成本"],
    "5402": ["出租业务成本", "销售材料成本", "非主营业务成本", "结转出租设备折旧", "结转卖出材料成本"],
    "5403": ["城建教育附加", "消费税附加税费", "经营税金附加", "计提本月附加税", "缴纳印花税"],
    "5601": ["广告推广开支", "销售人员费用", "销售运输包装费", "投放广告拉客户", "销售部门报销费用"],
    "5602": ["行政管理开支", "公司管理费用", "办公管理支出", "行政部门日常花费", "老板报销公司办公费"],
    "5603": ["银行手续费", "借款利息费用", "汇兑损益", "网银扣了手续费", "支付银行贷款利息"],
    "5711": ["非日常经营损失", "罚款滞纳金", "资产盘亏损失", "支付行政罚款", "处置资产发生净损失"],
    "5801": ["计提企业所得税", "本期所得税费用", "汇算所得税费用", "季度计提所得税", "确认年度所得税"],
}


GOVERNMENT_SUBSIDY_RECORD = {
    "input": "政府补助",
    "subject_code": "5301",
    "subject_detail": "政府补助",
    "layer2": "财政补贴、稳岗补贴、政府奖励、收到财政局补贴",
    "layer3": "政府发的钱、财政局打款、稳岗返还、政策奖励款",
    "logic": "已满足确认条件且与日常经营无直接对应的政府补助计入营业外收入；附条件或与资产相关且需以后期间确认的款项先复核递延收益",
    "distinction_rule": "已满足当期收益确认条件用5301；需递延确认用2401",
    "overlap_risk": "与2401递延收益需按补助条件和受益期间区分",
}


PLATFORM_RECORDS = [
    {
        "input": "平台待结算款",
        "subject_code": "1012",
        "subject_detail": "平台待结算款",
        "layer2": "抖店货款待结算、快手小店货款待结算、微信小店货款待结算、小红书店铺货款待结算、淘宝直播货款待结算、平台佣金待提现",
        "layer3": "平台钱包、可提现余额、结算账户、抖店钱包、快手小店钱包、微信小店钱包、小红书店铺钱包、淘宝直播结算账户",
        "logic": "平台已形成结算权利但尚未转入企业银行账户的货款、佣金或创作者收益，先在其他货币资金明细核算",
        "distinction_rule": "订单收入按业务发生额确认；平台钱包到银行仅结转1012至1002，不重复确认收入",
        "overlap_risk": "净额到账可能同时包含收入、退款、平台费和代扣项目，必须按平台结算单拆分",
    },
    {
        "input": "平台商品销售收入",
        "subject_code": "5001",
        "subject_detail": "平台商品销售",
        "layer2": "抖店自营商品销售、快手小店自营商品销售、微信小店自营商品销售、小红书店铺商品销售、淘宝直播店铺商品销售、直播间自营商品销售",
        "layer3": "抖店卖货、快手小店卖货、视频号小店卖货、小红书店铺卖货、淘宝直播卖货、直播间出单",
        "logic": "公司作为销售方在平台自营店铺或直播间销售商品，按订单和履约资料确认主营业务收入",
        "distinction_rule": "自营商品销售用本明细；仅为第三方推广并收取佣金时用平台带货佣金",
        "overlap_risk": "不得把平台净提现额直接作为收入，应按订单含税金额、退款和平台扣费分别核对",
    },
    {
        "input": "平台带货佣金收入",
        "subject_code": "5001",
        "subject_detail": "平台带货佣金",
        "layer2": "巨量百应带货佣金、精选联盟带货佣金、快分销带货佣金、优选联盟带货佣金、小红书买手合作佣金、B站悬赏带货佣金、淘宝联盟带货佣金、热浪引擎带货佣金",
        "layer3": "百应佣金、橱窗佣金、快分销佣金、视频号带货佣金、买手佣金、悬赏带货、淘宝客佣金、热浪佣金",
        "logic": "公司为第三方商品提供推广、直播或内容带货服务并按成交取得佣金，作为主营服务收入核算",
        "distinction_rule": "佣金服务收入与自营商品销售收入分开；是否含税以平台账单、合同和开票口径复核",
        "overlap_risk": "平台可能先扣技术服务费或税费，佣金毛额、扣费和实收款不得合并记成一笔净收入",
    },
    {
        "input": "内容商业合作收入",
        "subject_code": "5001",
        "subject_detail": "内容商业合作",
        "layer2": "巨量星图商单收入、磁力聚星商单收入、腾讯互选广告收入、小红书蒲公英商单收入、B站花火商单收入、品牌定制视频收入",
        "layer3": "星图商单、聚星商单、互选广告、蒲公英报备、花火商单、品牌植入、定制内容、商务合作费",
        "logic": "公司按合同为品牌提供广告植入、定制视频、图文种草或其他内容商业合作，作为主营服务收入核算",
        "distinction_rule": "固定或项目制商单收入用本明细；按商品成交比例取得的报酬用平台带货佣金",
        "overlap_risk": "应结合合同、交付验收、平台结算单和发票确认收入时点及含税口径",
    },
    {
        "input": "创作者激励与打赏收入",
        "subject_code": "5001",
        "subject_detail": "创作者激励与打赏",
        "layer2": "B站创作激励收入、B站充电收入、B站直播打赏收入、抖音直播打赏收入、快手直播打赏收入、视频号直播打赏收入、平台创作激励收入",
        "layer3": "创作激励、充电计划、充电收益、直播礼物、直播打赏、音浪收益、快币收益、视频号直播收益",
        "logic": "公司持续创作内容或开展直播取得的平台激励、观众支持和直播收益，按平台结算资料作为主营业务收入核算",
        "distinction_rule": "创作或直播经营形成的经常性收益用本明细；与日常经营无关的偶发奖励才复核营业外收入",
        "overlap_risk": "平台虚拟币换算、分成比例、退款和代扣项目应以结算单逐项复核，不按展示数或净到账数直接记账",
    },
    {
        "input": "平台技术服务费",
        "subject_code": "5601",
        "subject_detail": "平台技术服务费",
        "layer2": "抖店技术服务费、快手小店技术服务费、微信小店技术服务费、小红书平台技术服务费、淘宝平台技术服务费、平台交易服务费",
        "layer3": "平台扣点、平台服务费、交易扣费、渠道服务费、商家佣金、技术费、软件服务年费",
        "logic": "平台为交易、店铺、支付或结算提供服务而向公司收取的技术服务费、交易服务费等，计入销售费用",
        "distinction_rule": "与订单成交直接相关的平台扣费用本明细；主动购买流量曝光用平台推广投流",
        "overlap_risk": "平台净额结算时必须从收入和退款中拆出，不得只按银行净到账额确认收入",
    },
    {
        "input": "平台推广投流费",
        "subject_code": "5601",
        "subject_detail": "平台推广投流",
        "layer2": "巨量千川投流费、DOU+投放费、磁力金牛投流费、小红书聚光投放费、小红书薯条投放费、万相台推广费、阿里妈妈推广费",
        "layer3": "千川消耗、抖加投放、金牛消耗、聚光消耗、薯条加热、万相台消耗、买量、投流充值",
        "logic": "为商品、直播间、账号或内容购买流量、曝光和广告投放服务，计入销售费用的平台推广投流明细",
        "distinction_rule": "主动购买曝光或转化流量用本明细；平台按订单成交扣取的服务费用平台技术服务费",
        "overlap_risk": "充值只代表预存资金时应先核对余额，实际消耗后再确认费用",
    },
    {
        "input": "平台店铺保证金",
        "subject_code": "1221",
        "subject_detail": "平台保证金",
        "layer2": "抖店店铺保证金、快手小店店铺保证金、微信小店店铺保证金、小红书店铺保证金、淘宝店铺保证金、直播平台履约保证金",
        "layer3": "抖店押金、快手小店押金、视频号小店押金、小红书开店押金、淘宝开店押金、平台冻结保证金",
        "logic": "向平台支付且满足条件后可退回的店铺、履约或风险保证金，计入其他应收款明细",
        "distinction_rule": "可退保证金用1221；已实际扣除且不再退回的违约款或服务费应按性质另行确认",
        "overlap_risk": "保证金冻结、扣划和退回状态应以平台规则及资金明细复核",
    },
    {
        "input": "平台销售退回",
        "subject_code": "5001",
        "subject_detail": "平台销售退回",
        "layer2": "抖店订单退货退款、快手小店订单退货退款、微信小店订单退货退款、小红书订单退货退款、淘宝直播订单退货退款、平台退款冲减销售收入",
        "layer3": "直播间退货、平台订单退款、仅退款、退货退款、售后退款、拒收退款、逆向订单",
        "logic": "已确认的平台商品销售发生退货退款或红字冲减时，作为主营业务收入的抵减明细核算",
        "distinction_rule": "退款冲减收入与平台额外收取的服务费分别核算；已结转商品成本还应按实际退货情况处理",
        "overlap_risk": "仅退款、退货入库、平台赔付和优惠补贴的会计处理不同，必须核对售后单和结算单",
    },
    {
        "input": "内容合作方分成成本",
        "subject_code": "5401",
        "subject_detail": "内容合作分成",
        "layer2": "支付MCN机构分成、支付签约主播分成、支付合作达人分成、支付内容合作方分成、结算直播团队分成、结算联合创作者分成",
        "layer3": "MCN抽成、主播分成、达人分成、合作方抽成、直播团队抽成、联合创作分账、商务经纪分成",
        "logic": "为取得内容、直播或带货主营收入而按合同支付给MCN、主播、达人或合作创作者的直接分成，计入主营业务成本",
        "distinction_rule": "与具体主营收入直接对应的履约分成用5401；平台向商家收取的交易服务费用5601",
        "overlap_risk": "应核对合同主体、结算比例、发票或其他税前扣除凭证以及个人所得税扣缴义务",
    },
]


PLATFORM_MANUAL_CATEGORIES = {
    "平台净额结算拆分": {
        "tags": ["平台净额结算", "扣完平台费净到账", "平台结算单净额", "实际到账小于订单金额"],
        "details": ["平台待结算款", "平台商品销售", "平台带货佣金", "平台技术服务费"],
        "extra_codes": ["2221"],
        "review_message": "平台净额结算不能按到账净额直接记收入，请按结算单拆分收入、退款、平台费、代扣税费和待结算款后手工确认。",
    },
    "平台代扣税费复核": {
        "tags": ["平台代扣税费", "平台预扣税款", "平台扣税后结算", "平台代缴税费"],
        "details": ["平台待结算款", "平台带货佣金", "内容商业合作", "创作者激励与打赏"],
        "extra_codes": ["2221"],
        "review_message": "平台显示的代扣税费不当然等于公司已完成全部申报，请核对扣缴主体、税种、凭证和电子税务局记录后拆分入账。",
    },
    "平台退款扣费拆分": {
        "tags": ["平台退款并扣服务费", "退货退款扣平台费", "平台退款净额", "退款后平台又扣费"],
        "details": ["平台待结算款", "平台销售退回", "平台技术服务费"],
        "extra_codes": [],
        "review_message": "退款与平台服务费属于不同业务，请按售后单和结算单分别冲减收入、处理退货并确认平台费用。",
    },
}


def record_identity(record):
    return (
        str(record.get("input", "")).strip(),
        str(record.get("subject_code", "")).strip(),
        str(record.get("subject_detail", "")).strip(),
    )


def upsert_special_records(existing, records, next_id):
    """Add maintained records once while preserving user-edited vocabulary fields."""
    by_identity = {record_identity(row): row for row in existing}
    for source in records:
        identity = record_identity(source)
        target = by_identity.get(identity)
        if target is None:
            target = {"id": next_id, **source}
            next_id += 1
            existing.append(target)
            by_identity[identity] = target
            continue
        for key, value in source.items():
            if not str(target.get(key, "")).strip():
                target[key] = value
    return next_id


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    catalog = load_account_catalog(ROOT / "account_catalog_small_enterprise.json")
    raw_vocab = read_json(VOCAB_PATH)
    if isinstance(raw_vocab, dict):
        raw_vocab = raw_vocab.get("科目", [])
    existing = enrich_vocab_records(
        [row for row in raw_vocab if str(row.get("id", "")).isdigit()], catalog
    )
    codes = {str(row.get("subject_code", "")) for row in existing}
    next_id = max((int(row["id"]) for row in existing), default=0) + 1
    for account in catalog["accounts"]:
        code = str(account["code"])
        if code in codes:
            continue
        terms = TERMS[code]
        existing.append({
            "id": next_id,
            "input": str(account["name"]),
            "subject_code": code,
            "layer2": "、".join(terms[:3]),
            "layer3": "、".join(terms[3:]),
            "logic": f"业务实质符合{account['name']}的官方核算范围时使用",
            "overlap_risk": "需结合交易时点、权利义务和用途复核",
        })
        next_id += 1
        codes.add(code)

    next_id = upsert_special_records(
        existing,
        [GOVERNMENT_SUBSIDY_RECORD, *PLATFORM_RECORDS],
        next_id,
    )

    semantic = semantic_payload(enrich_vocab_records(existing, catalog))
    semantic.sort(key=lambda row: int(row["id"]))
    write_json(VOCAB_PATH, semantic)
    enriched = enrich_vocab_records(semantic, catalog)

    categories = read_json(CATEGORY_PATH)
    category_map = categories.setdefault("categories", {})
    referenced = {
        subject for info in category_map.values() for subject in info.get("subjects", [])
    }
    by_code = {}
    for row in enriched:
        by_code.setdefault(str(row.get("subject_code", "")), row)
    for account in catalog["accounts"]:
        row = by_code[str(account["code"])]
        subject = row["subject"]
        if subject in referenced:
            continue
        terms = [row.get("input", "")] + str(row.get("layer2", "")).split("、")[:3]
        category_map[f"科目-{account['name']}"] = {
            "tags": [term for term in terms if term],
            "subjects": [subject],
            "description": str(account.get("usage", "")),
        }
        referenced.add(subject)

    subsidy = next(row for row in enriched if row.get("subject_detail") == "政府补助")
    subsidy_category = category_map.setdefault("营业外收益", {"tags": [], "subjects": []})
    for term in ["政府补助", "财政补贴", "稳岗补贴", "政府奖励", "收到财政局补贴"]:
        if term not in subsidy_category.setdefault("tags", []):
            subsidy_category["tags"].append(term)
    if subsidy["subject"] not in subsidy_category.setdefault("subjects", []):
        subsidy_category["subjects"].insert(0, subsidy["subject"])

    by_detail = {
        str(row.get("subject_detail", "")): row
        for row in enriched
        if str(row.get("subject_detail", "")).strip()
    }
    platform_categories = {
        "平台待结算与提现": {
            "tags": ["平台待结算款", "平台钱包", "可提现余额", "货款待结算", "佣金待提现"],
            "details": ["平台待结算款"],
            "description": "平台已结算但尚未转入企业银行账户的货款、佣金或创作者收益。",
        },
        "平台商品销售": {
            "tags": ["平台商品销售", "自营商品销售", "直播间卖货", "店铺商品销售"],
            "details": ["平台商品销售"],
            "description": "公司作为销售方通过平台店铺或直播间销售自营商品。",
        },
        "平台带货佣金": {
            "tags": ["带货佣金", "精选联盟", "快分销", "优选联盟", "小红书买手合作", "悬赏带货", "淘宝联盟", "热浪引擎"],
            "details": ["平台带货佣金"],
            "description": "为第三方商品提供推广或直播带货并按成交取得佣金。",
        },
        "内容商业合作": {
            "tags": ["内容商单", "蒲公英报备商单", "巨量星图", "磁力聚星", "腾讯互选", "小红书蒲公英", "B站花火", "品牌定制视频"],
            "details": ["内容商业合作"],
            "description": "品牌广告植入、定制视频、图文种草等内容商业合作。",
        },
        "创作者激励与打赏": {
            "tags": ["创作激励", "B站充电", "直播打赏", "音浪收益", "快币收益", "直播礼物"],
            "details": ["创作者激励与打赏"],
            "description": "持续创作内容或直播经营取得的平台激励和观众支持。",
        },
        "平台技术服务费": {
            "tags": ["平台技术服务费", "平台交易服务费", "平台扣点", "商家佣金"],
            "details": ["平台技术服务费"],
            "description": "平台按交易、店铺、支付或结算服务向公司收取的费用。",
        },
        "平台推广投流": {
            "tags": ["平台投流", "巨量千川", "DOU+", "磁力金牛", "小红书聚光", "小红书薯条", "万相台"],
            "details": ["平台推广投流"],
            "description": "为商品、直播间、账号或内容购买流量、曝光和广告投放服务。",
        },
        "平台保证金": {
            "tags": ["平台保证金", "店铺保证金", "履约保证金", "平台冻结保证金"],
            "details": ["平台保证金"],
            "description": "向平台支付且满足条件后可退回的店铺或履约保证金。",
        },
        "平台退货退款": {
            "tags": ["平台退货退款", "订单退款", "仅退款", "逆向订单", "退款冲减收入"],
            "details": ["平台销售退回"],
            "description": "已确认的平台销售发生退货退款或红字冲减。",
        },
        "MCN及合作方分成": {
            "tags": ["MCN分成", "主播分成", "达人分成", "直播团队分成", "联合创作者分成"],
            "details": ["内容合作分成"],
            "description": "为取得主营内容或直播收入而支付的直接履约分成。",
        },
    }
    for category, info in platform_categories.items():
        category_map[category] = {
            "tags": info["tags"],
            "subjects": [by_detail[detail]["subject"] for detail in info["details"]],
            "description": info["description"],
            "platform_scope": "主流境内内容与直播电商平台",
        }

    for category, info in PLATFORM_MANUAL_CATEGORIES.items():
        subjects = [by_detail[detail]["subject"] for detail in info["details"]]
        subjects.extend(by_code[code]["subject"] for code in info["extra_codes"])
        category_map[category] = {
            "tags": info["tags"],
            "subjects": list(dict.fromkeys(subjects)),
            "description": info["review_message"],
            "manual_review": True,
            "review_message": info["review_message"],
            "platform_scope": "主流境内内容与直播电商平台",
        }

    tag_index = {}
    for category, info in category_map.items():
        subjects = list(dict.fromkeys(str(value) for value in info.get("subjects", []) if value))
        info["subjects"] = subjects
        info["tags"] = list(dict.fromkeys(str(value) for value in info.get("tags", []) if value))
        for tag in info["tags"]:
            tag_index.setdefault(tag, []).append({"category": category, "subjects": subjects})
    categories["tag_index"] = tag_index
    categories["metadata"] = {
        "standard": "小企业会计准则",
        "account_count": 66,
        "purpose": "三级词库只做语义映射，科目主数据来自独立66科目目录",
        "platform_scope": "抖音/抖店、快手/快手小店、微信视频号/微信小店、小红书、哔哩哔哩、淘宝直播",
        "platform_review_date": "2026-07-17",
        "boundary": "平台品牌词本身不构成记账依据；净额结算、退款扣费和代扣税费必须按结算单拆分并人工复核",
    }
    write_json(CATEGORY_PATH, categories)

    corpus = []
    for account in catalog["accounts"]:
        row = by_code[str(account["code"])]
        candidates = [row.get("input", "")] + str(row.get("layer2", "")).split("、")
        selected = []
        for term in candidates:
            term = str(term).strip()
            if term and term not in selected:
                selected.append(term)
            if len(selected) == 3:
                break
        if len(selected) < 3:
            selected.extend(TERMS[str(account["code"])][:3 - len(selected)])
        corpus.extend({"text": term, "subject_code": str(account["code"])} for term in selected[:3])
    corpus.extend({"text": term, "subject_code": "5301"} for term in (
        "政府补助", "财政补贴", "稳岗补贴", "政府奖励", "收到财政局补贴",
    ))
    for record in PLATFORM_RECORDS:
        terms = [record["input"], *str(record["layer2"]).split("、")]
        corpus.extend({
            "text": term,
            "subject_code": record["subject_code"],
            "subject_detail": record["subject_detail"],
            "scenario": "内容平台资金往来",
        } for term in terms if term)
    write_json(CORPUS_PATH, corpus)
    print(json.dumps({
        "vocabulary_records": len(semantic),
        "account_codes": len({row.get('subject_code') for row in semantic}),
        "categories": len(category_map),
        "regression_utterances": len(corpus),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
