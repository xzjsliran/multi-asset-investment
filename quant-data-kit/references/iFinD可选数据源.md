# iFinD可选数据源

## 接入定位

iFinD作为金融资料查询、指标发现及交叉核对来源。基础行情继续按已有免费来源/本地Tushare流程获取；美国宏观继续保留FRED、ALFRED方法。iFinD尚未接入自动`fetch`的行情替换链，原始响应不自动进入回测。

官方提供股票、基金、债券、港美股、指数板块、宏观行业、新闻公告及期货期权服务。工具清单随账户权益变化，先检查实际返回清单。股票、基金和EDB的多数查询采用自然语言；调用成功还需要检查问题是否被准确解析。

- [官方网站与密钥入口](https://mcp.51ifind.com/)
- [官方Skill安装指南](https://mcp.51ifind.com/gwstatic/static/ds_web/ifind-mcp-web/skills/SKILL_INSTALL_GUIDE.md)
- [官方Skill包](https://s.thsi.cn/cd/ifind-java-ds-bff-web-container/ifind-mcp-web/skills/ifind-finance-data-1.4.0.zip)

官方Skill可单独安装；也可使用本kit附带的Python适配器。后者直接调用同一个远程MCP服务，保留TLS证书验证，无需另装MCP服务器或修改宿主全局配置。Tushare仍使用本地代码调用，凭证不会发给iFinD。

## 配置与小量查询

执行`scripts/configure_credentials.py ifind`，在本地终端不回显输入。默认配置位于使用者主目录`.config/multi-assets/credentials.json`；也支持`IFIND_API_KEY`环境变量。文件夹和报告不保存密钥。

1. `scripts/ifind.py tools --service edb --work <查询工作目录>`查看工具名称与参数。清单保存在工作目录；官方说明工具清单查询不扣数据查询次数。
2. 在工作目录写参数文件，例如`{"query":"美国:国债收益率:10年，2025年1月2日至2025年1月10日的日频数据，注明来源与单位"}`。
3. `scripts/ifind.py query --service edb --tool get_edb_data --params-file <参数文件> --work <同一查询目录> --max-calls 4`。

每个工作目录记录累计`tools/call`尝试次数，默认最多4次。成功及失败均计入本地上限，超时不自动重试；相同查询复用缓存，避免重复扣量。本地计数记录已尝试调用，不等于账户后台的结算次数或剩余额度。新任务可以另建目录，但Agent必须按用户授权的总预算安排查询，不能换目录绕过上限。

服务名称为`stock`、`fund`、`edb`、`news`、`bond`、`global_stock`、`index`、`future`。先用`tools`确认工具及参数。安装说明与实际服务端工具不一致时，以在线清单为准。

## 数据进入策略前

适配器输出来源、问题参数、获取时间、原始响应及`signal_eligible=false`。`ok`只表示通信/工具层未报错，不能据此认定内容符合请求。进入数据模块标准表前依次核对：

1. 返回证券代码、日期区间和频率必须符合请求；不将周末沿用价当作成交价。
2. 原始价与复权价分开，不能用缺失的后复权字段替代现有研究价格。
3. 将“亿/万”等文字单位明确转换；ROE加权和摊薄口径分开，不能任意择一。
4. 宏观筛掉错误国家和预测序列；利率水平、债券收益率和议息概率保持不同字段。
5. 历史财务和宏观保留报告期/统计期、可用时间、版本说明；仅查询当前修订值时不宣称恢复了当时信息。

给Agent的建议：先用单证券、单指标、短日期范围的小问题确认口径；根据返回的具体指标代码取数。出现日期不符或字段遗漏时保存问题，回到已有来源或等待用户扩展测试预算。

## 额度与后续使用

付费套餐的问答次数、宏观搜索和CSV容量分别计量，以[订阅页](https://mcp.51ifind.com/#/pricing)及账户实际权益为准。商业客户报告展示及数据服务使用范围，按[会员协议](https://mcp.51ifind.com/#/terms/member-service)向服务方确认。
