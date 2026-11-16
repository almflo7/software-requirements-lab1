# 外卖点餐与配送系统

运行环境：Windows PowerShell、Python 3.12、SQLite。命令行与业务层使用 Python 标准库；桌面界面使用 `requirements-ui.txt` 中的 PySide6；风格检查使用 `requirements-dev.txt` 中的 pylint。数据库结构在 `app/schema.sql`，业务对象在 `app/models.py`，各命令入口在 `app/cli.py`。

```powershell
.\tasks.ps1 setup
.\tasks.ps1 init
.\tasks.ps1 check
.\tasks.ps1 recovery
.\tasks.ps1 load
```

`check` 依次执行编译、自动测试、有效代码行数统计和 pylint 错误级检查，结果保存在 `evidence`。`load` 使用 100 家店铺、1000 个菜品、10000 笔历史订单和 30 名用户运行 10 分钟；每名用户在操作间隔 0.1 秒，菜品查询使用组合筛选。`recovery` 检查 100 笔订单在定时工作进程中断后的恢复。单独运行命令可用 `python -m app.cli --help`；默认数据库为 `lab3.sqlite3`，也可在子命令前指定 `--db 路径`。

当前 `lab3.sqlite3` 已准备好管理员 `a`、商家 `m1` 和 `m2`、配送员 `d1` 和 `d2`、顾客 `c`，密码均为 `Demo1234`。`init` 只建立数据库结构，不会添加测试账号。业务命令需先 `login 账号`，然后将返回的令牌放入当前 PowerShell 会话：

```powershell
$env:LAB3_SESSION = '登录命令返回的令牌'
.\tasks.ps1 demo
```

顾客使用 `create-cart`、`change-cart`、`save-address`、`confirm-checkout`、`create-order` 下单。付款用 `start-payment` 发起，管理员用 `payment-result` 注入模拟成功或失败；商家用 `merchant-response` 和 `finish-preparation` 履约；管理员用 `assign-courier` 人工派单；配送员用 `courier-response`、`courier-step` 完成取餐和送达。顾客可以 `request-cancel`，商家或管理员用 `decide-cancel` 处理；模拟退款由管理员用 `refund-result` 注入结果，失败后可用 `failed-refunds` 查询和 `retry-refund` 重试。

`run-jobs` 执行已到期的付款、接单、配送响应、自动完成及退款任务。`notifications`、`view-order`、`history`、`statistics`、`audit-logs` 分别查询本人通知、订单、历史、经营统计和授权日志。`record-arrival`、`record-contact`、`report-exception`、`resolve-exception` 支持配送异常。`open-dispute`、`view-dispute`、`resolve-dispute` 支持争议处理与管理员查看留痕。

模拟支付、退款和通知均在本地数据库内完成，不连接真实资金或短信服务。新订单须使用管理员开放的虚拟地图取件点和审核通过的商家取餐点；虚拟直线距离不超过 2 公里配送费 3 元，超过 2 公里至 5 公里为 5 元，超过 5 公里不可下单。旧地址及旧店铺不会通过文字推测坐标，须重新选择取件点或申请取餐点；旧订单快照不变。全员优惠券只抵餐费，顾客可从首页举报店铺或菜品，只有管理员可查看并处理。会员、推荐、评价、发票、跨店购物车、真实支付、GPS、真实道路导航和自动派单仍不提供入口。

新增命令包括 `nearby-points`、`save-point`、`request-store-point`、`review-store-point`、`list-categories`、`manage-category`、`list-coupons`、`issue-coupon`、`submit-report`、`list-reports` 和 `resolve-report`。`save-address` 可加 `--pickup-point-id`，`confirm-checkout` 可加 `--coupon-id`。具体参数使用 `python -m app.cli 子命令 --help` 查看。

## UI检查

人工检查或者UI查看可以在本地使用以下命令行代码：
```bash
Set-Location -LiteralPath 'E:\2026-1\软件工程\软件工程实验三\实验3_241220074_肖若璠\code'
.\.venv\Scripts\python.exe -m pip install -r requirements-ui.txt
.\tasks.ps1 compile
.\.venv\Scripts\python.exe -m app.ui
```
第一次运行方式如上; 之后只运行以下代码即可：
```bash
.\.venv\Scripts\python.exe -m app.ui
```
`ui-showcase.sqlite3` 现在也只含上述 6 个账号、2 家店铺和 6 个菜品。若要重新生成大量性能数据，应使用另一个数据库文件：

```powershell
Set-Location -LiteralPath 'E:\2026-1\软件工程\软件工程实验三\实验3_241220074_肖若璠\code'

.\.venv\Scripts\python.exe -m app.cli --db large-demo.sqlite3 seed-demo
.\.venv\Scripts\python.exe -m scripts.seed_data --db large-demo.sqlite3
.\.venv\Scripts\python.exe -m app.ui --db large-demo.sqlite3
```

这批性能数据是固定生成的约 **100 家店铺、1000 个菜品、10000 笔已完成订单**，主要用于查看大量列表，不是随机的完整履约流程。`customer-demo` 可浏览菜品；下单时须选择管理员开放的虚拟取件点。性能数据中的 `customer-0` 等账号没有密码，不能直接登录。

**管理员页面已经做了角色切换**，方式是点击右上角“退出登录”，再用账号 `a`、密码 `Demo1234` 登录。登录后会按账号角色显示管理员侧边栏。
