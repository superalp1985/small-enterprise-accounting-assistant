#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Shared policy presets and responsibility boundary text."""


POLICY_PRESET_REVIEW_DATE = "2026-07-17"
POLICY_EFFECTIVE_THROUGH = "2027-12-31"

POLICY_SOURCES = [
    {
        "title": "关于印发《小企业会计准则》的通知（财会〔2011〕17号）",
        "url": "https://kjs.mof.gov.cn/zhengcefabu/201111/t20111107_605525.htm",
        "publisher": "财政部",
        "date": "2011-11-18",
    },
    {
        "title": "关于增值税小规模纳税人减免增值税政策的公告（2023年第19号）",
        "url": "https://szs.mof.gov.cn/zhengcefabu/202308/t20230802_3899759.htm",
        "publisher": "财政部、税务总局",
        "date": "2023-08-03",
    },
    {
        "title": "关于进一步支持小微企业和个体工商户发展有关税费政策的公告（2023年第12号）",
        "url": "https://szs.mof.gov.cn/zhengcefabu/202308/t20230802_3899800.htm",
        "publisher": "财政部、税务总局",
        "date": "2023-08-02",
    },
    {
        "title": "国家税务总局关于发布《个人所得税扣缴申报管理办法（试行）》的公告（2018年第61号）",
        "url": "http://fgk.chinatax.gov.cn/zcfgk/c100012/c5194838/content.html",
        "publisher": "国家税务总局",
        "date": "2018-12-21",
    },
    {
        "title": "国家税务总局关于实施《中华人民共和国印花税法》等有关事项的公告（2022年第14号）",
        "url": "http://fgk.chinatax.gov.cn/zcfgk/c100012/c5196761/content.html",
        "publisher": "国家税务总局",
        "date": "2022-06-28",
    },
    {
        "title": "国家税务总局企业所得税申报目录（使用时核对现行表单）",
        "url": "https://www.chinatax.gov.cn/chinatax/c102276/c5181990/5181990/files/e6a0323aaef848c996da13198d4a07de.pdf",
        "publisher": "国家税务总局",
        "date": "动态目录",
    },
]

LEGAL_NOTICE_SUMMARY = (
    "免费、本地ERP式记账与财税准备辅助工具：结果仅按当前账套资料和可修改参数生成，"
    "不承诺准确、合规、适用或被申报系统接受；使用者须按原始凭证、业务实质和主管机关口径复核。"
)

LEGAL_NOTICE_FULL = """软件定位与责任边界

1. 本软件按本地ERP式工作流提供账套、凭证、往来、资产、结账、报表和税务准备功能，但产品性质仍是免费的记账与财税准备辅助工具，不是代理记账、纳税申报或专业鉴证服务。
2. 软件中的税率、免税阈值、优惠资格条件和政策截止日期是可修改的测算参数，不代表永久有效的法定标准。
3. 语义模型、OCR、会计科目推荐、税费测算和Excel导出均可能存在识别、分类、录入或政策适用偏差，使用者必须核对原始凭证和业务实质。
4. 导出Excel仅是根据本地账套生成的记账、对账和报税准备工作底稿，可能需要补充或调整；软件不承诺其可直接上传、被任何电子税务局接受，或满足特定地区、期间和业务的全部报送要求。
5. 使用者应在申报前确认纳税人资格、所属地区、申报频率、含税或不含税口径、优惠条件和政策有效期，并以电子税务局最终计算结果为准。
6. 软件按“现状”免费提供，不附带结果准确、完整、合规、政策持续有效、适用于特定业务或申报成功的明示或默示保证。使用者对原始资料真实性、账务确认、参数维护、最终申报和资料留存承担全部责任。

软件会保留政策资料链接和参数快照，便于复核，但不会自动代替使用者判断政策是否仍然有效。"""


def policy_snapshot_text(tax_settings):
    """Return a compact, user-visible snapshot of configurable assumptions."""
    tax = tax_settings or {}
    return (
        f"参数复核日：{tax.get('policy_reference_date', POLICY_PRESET_REVIEW_DATE)}；"
        f"优惠政策截止：{tax.get('policy_effective_through', POLICY_EFFECTIVE_THROUGH)}；"
        f"增值税测算率：{float(tax.get('vat_rate', 0) or 0) * 100:g}%；"
        f"月/季免税阈值：{float(tax.get('vat_monthly_exemption_threshold', 0) or 0):g}/"
        f"{float(tax.get('vat_quarterly_exemption_threshold', 0) or 0):g}元；"
        f"所得税有效测算率：{float(tax.get('cit_rate', 0) or 0) * 100:g}%；"
        f"支持范围：小规模纳税人 + 小型微利企业。"
    )
