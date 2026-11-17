# 场外 QDII 与美元存款行情

## 场外净值

| 敞口 | 预设标的 | 行情 |
|---|---|---|
| 标普 500 | 博时标普 500 ETF 联接 A 人民币 `050025.OF` | 人民币场外净值 |
| 纳斯达克 100 | 国泰纳斯达克 100 指数 QDII `160213.OF` | 人民币场外净值 |

配置用 `kind=qdii_nav`。Tushare `fund_nav` 请求 `market=O`，取 `unit_nav` 作为原始参考价，`adj_nav` 作为研究净值，分别保留 `nav_date` 和 `ann_date`。只有公布日期时，按北京时间次日零点后可用。同一净值日使用第一个可核验的版本。人民币净值已含折算后的资产价值。 [Tushare 公募基金净值](https://tushare.pro/document/2?doc_id=119) · [博时基金](https://www.bosera.com/service/fundSalesOrg.do?fundCode=050025) · [国泰基金](https://www.gtfund.com/product/productlist/haiwai/160213/summary/index.html)

场外配置用净值模拟，避开场内折溢价。回测采用最新已经公布的净值近似申赎价格。赎回款按产品配置的 `redemption_days` 保留在待到账款，到账后进入可用资金。实际申购限额、基金确认净值和份额以渠道公告或成交回执为准。 [国泰基金招募说明书](https://st.gtfund.com/report_root/2024/06/%E5%9B%BD%E6%B3%B0%E7%BA%B3%E8%BE%BE%E5%85%8B100%E6%8C%87%E6%95%B0%E8%AF%81%E5%88%B8%E6%8A%95%E8%B5%84%E5%9F%BA%E9%87%91%E6%9B%B4%E6%96%B0%E6%8B%9B%E5%8B%9F%E8%AF%B4%E6%98%8E%E4%B9%A6%EF%BC%882024%E5%B9%B4%E7%AC%AC%E4%B8%80%E5%8F%B7%EF%BC%89.pdf)

自定义组合也可用场内 QDII 的 `etf` 类型，例如 `513500.SH` 或 `513100.SH`，按人民币市场价和相应的复权行情运算。直接购买美国证券用 `us_etf` 和已知汇率。各标的分别取数和检验覆盖。

## 美元活期

`USD_DEPOSIT` 的类型为 `usd_deposit`。按生效日读取中国银行境内美元活期挂牌年利率，逐日累计美元利息。估值时乘以决策前可用的美元兑人民币汇率。存款利率可按实际账户另填，用 `data_sources.usd_deposit_rates` 传入带来源的牌价表。 [中国银行外汇存款挂牌档案](https://www.boc.cn/fimarkets/lilv/fd33/)

汇率优先使用 Yahoo `CNY=X`，FRED `DEXCHUS` 作备选。后一来源按 H.10 次周发布时点对齐。存款占用已投资预算，未分配现金另行记录。 [FRED 美元兑人民币汇率](https://fred.stlouisfed.org/series/DEXCHUS)
