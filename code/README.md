# 外卖点餐与配送系统

运行环境：Windows PowerShell、Python 3.12、SQLite。运行时仅使用 Python 标准库；风格检查使用 `requirements-dev.txt` 中固定的 pylint。数据库结构在 `app/schema.sql`，业务对象在 `app/models.py`，各命令入口在 `app/cli.py`。

```powershell
.\tasks.ps1 setup
.\tasks.ps1 init
.\tasks.ps1 check
.\tasks.ps1 recovery
.\tasks.ps1 load
```

`check` 依次执行编译、自动测试、有效代码行数统计和 pylint 错误级检查，结果保存在 `evidence`。`load` 使用 100 家店铺、1000 个菜品、10000 笔历史订单和 30 名用户运行 10 分钟；每名用户在操作间隔 0.1 秒，菜品查询使用组合筛选。`recovery` 检查 100 笔订单在定时工作进程中断后的恢复。单独运行命令可用 `python -m app.cli --help`；默认数据库为 `lab3.sqlite3`，也可在子命令前指定 `--db 路径`。

`seed-demo` 产生顾客 `customer-demo`、商家 `merchant-demo`、配送员 `courier-demo` 和管理员 `admin-demo`，密码均为 `demo-pass-123`。业务命令需先 `login 账号`，然后将返回的令牌放入当前 PowerShell 会话：

```powershell
$env:LAB3_SESSION = '登录命令返回的令牌'
.\tasks.ps1 demo
```

顾客使用 `create-cart`、`change-cart`、`save-address`、`confirm-checkout`、`create-order` 下单。付款用 `start-payment` 发起，管理员用 `payment-result` 注入模拟成功或失败；商家用 `merchant-response` 和 `finish-preparation` 履约；管理员用 `assign-courier` 人工派单；配送员用 `courier-response`、`courier-step` 完成取餐和送达。顾客可以 `request-cancel`，商家或管理员用 `decide-cancel` 处理；模拟退款由管理员用 `refund-result` 注入结果，失败后可用 `failed-refunds` 查询和 `retry-refund` 重试。

`run-jobs` 执行已到期的付款、接单、配送响应、自动完成及退款任务。`notifications`、`view-order`、`history`、`statistics`、`audit-logs` 分别查询本人通知、订单、历史、经营统计和授权日志。`record-arrival`、`record-contact`、`report-exception`、`resolve-exception` 支持配送异常。`open-dispute`、`view-dispute`、`resolve-dispute` 支持争议处理与管理员查看留痕。

模拟支付、退款和通知均在本地数据库内完成，不连接真实资金或短信服务。店铺配送费是固定金额，配送范围按区域编码匹配。首期没有优惠券、会员、推荐、评价、发票、跨店购物车、真实支付、地图、自动路线规划或自动派单入口。
