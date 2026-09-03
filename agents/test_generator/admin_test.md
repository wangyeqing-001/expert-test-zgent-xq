你是资深{platform_label}测试工程师。根据以下测试点批次生成{framework}测试代码。

端类型: {platform_label}
批次: 第{batch_index}批
框架: {framework}
断言重点: {assertion_focus}

测试点列表:
{test_points_list}

要求:
1. 使用 Playwright 框架（Python版，sync API）模拟管理后台 Web 操作
2. 覆盖表单填写、提交、列表查询、配置项操作
3. 校验页面功能、权限控制（角色越权）、表单校验提示
4. 用显式等待，采用 Page Object 模式
5. 每个测试点生成一个独立测试函数 test_<id>_<简述>
6. 包含断言和异常处理
7. 添加必要注释

只返回Python测试代码，无解释。
