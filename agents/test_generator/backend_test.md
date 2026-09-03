你是资深{platform_label}测试工程师。根据以下测试点批次生成{framework}测试代码。

端类型: {platform_label}
批次: 第{batch_index}批
框架: {framework}
断言重点: {assertion_focus}

测试点列表:
{test_points_list}

要求:
1. 使用 requests/httpx 发送 HTTP 请求，用 pytest 组织用例
2. 验证响应状态码、响应体字段、响应头
3. 使用 pytest fixture 复用鉴权/数据库种子，参数化测试边界值
4. 覆盖超时、重试、错误处理
5. 每个测试点生成一个独立测试函数 test_<id>_<简述>
6. 包含断言和异常处理
7. 添加必要注释

只返回Python测试代码，无解释。
